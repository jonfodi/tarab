"""Official metadata: lengths, BPM, audio clips and tracklists from Deezer, MusicBrainz and Bandcamp.

All lookups are best-effort: any service can be down, rate-limited or change shape (Bandcamp's search endpoint is
unofficial), and a failure only means less to check against, never a crash.
"""
from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass

import requests

from . import __version__
from .identity import Reference, clip_from_preview, clip_from_stream
from .text import STOP, VARIANTS, Query, compact_in, other_version, tokens

# MusicBrainz asks clients to identify themselves with contact info; set to the repo URL once it's public.
UA = f"rasa/{__version__} (open-source DJ library tool)"
_http = requests.Session()
_http.headers["User-Agent"] = UA


@dataclass
class AlbumTrack:
    title: str
    length: int
    audio_url: str | None = None   # full stream (Bandcamp) or 30s preview (Deezer)


@dataclass
class Release:
    artist: str
    title: str
    source: str            # Bandcamp / Deezer / MusicBrainz
    url: str               # where to buy / view it
    tracks: list[AlbumTrack]
    catalog: str | None = None

    def queries(self, with_audio: bool = True) -> list[Query]:
        """One query per track, each pinned to its official length and checked against official audio."""
        out = []
        for t in self.tracks:
            q = Query(self.artist, t.title, t.length or None, self.catalog)
            clip = None
            if with_audio and t.audio_url:
                clip = clip_from_stream(t.audio_url, t.length) if self.source == "Bandcamp" \
                    else _safe(lambda: clip_from_preview(t.audio_url))
            q.ref = Reference([t.length] if t.length else [], [clip] if clip is not None else [], None,
                              [self.source], strict=bool(t.length))
            out.append(q)
        return out


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# ---------------------------------------------------------------- MusicBrainz (≈1 request/s)

_mb_last = 0.0


def _mb(path: str, **params) -> dict:
    global _mb_last
    for wait in (0, 1.5, 4):
        time.sleep(max(wait, 1.1 - (time.time() - _mb_last)))
        _mb_last = time.time()
        r = _http.get(f"https://musicbrainz.org/ws/2/{path}", params={**params, "fmt": "json"}, timeout=10)
        if r.status_code != 503:
            return r.json()
    return {}


# ---------------------------------------------------------------- Bandcamp

def _bandcamp_search(text: str, kind: str) -> list[dict]:
    """kind: 'a' albums, 't' tracks."""
    try:
        r = _http.post("https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic", timeout=10,
                       json={"search_text": text, "search_filter": kind, "full_page": False, "fan_id": None})
        return r.json().get("auto", {}).get("results", [])
    except (requests.RequestException, ValueError):
        return []


def _bandcamp_page(url: str) -> dict | None:
    try:
        page = _http.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15).text
        m = re.search(r'data-tralbum="([^"]+)"', page)
        return json.loads(html.unescape(m[1])) if m else None
    except (requests.RequestException, ValueError):
        return None


def _split_artist(title: str, artist: str) -> tuple[str, str]:
    """Label pages list "Artist - Title" as the title, with the label as the artist. Returns (title, artist)."""
    if " - " in title:
        a, t = title.split(" - ", 1)
        return t.strip(), a.strip()
    return title, artist


def bandcamp_release(url: str) -> Release | None:
    """A Bandcamp album/EP link → its release and tracklist."""
    data = _bandcamp_page(url)
    if not data or not data.get("trackinfo"):
        return None
    title, artist = _split_artist(data["current"]["title"], data.get("artist", ""))
    tracks = [AlbumTrack(_split_artist(t["title"], artist)[0], round(t.get("duration") or 0),
                         (t.get("file") or {}).get("mp3-128")) for t in data["trackinfo"]]
    return Release(artist, title, "Bandcamp", url.split("?")[0], tracks)


# ---------------------------------------------------------------- releases

def find_release(artist: str, title: str, catalog: str | None = None) -> Release | None:
    """Tracklist for "Artist - Release": Bandcamp first (underground labels), then Deezer, then MusicBrainz."""
    want_a, want_r = tokens(artist) - STOP, tokens(title) - STOP

    def same(t: str, by: str) -> bool:
        return want_r <= tokens(t) and bool(want_a & (tokens(by) | tokens(t)))

    for hit in _bandcamp_search(f"{artist} {title}", "a"):
        if hit.get("type") == "a" and same(hit.get("name", ""), hit.get("band_name", "")):
            if (rel := bandcamp_release(hit["item_url_path"])) and rel.tracks:
                rel.artist, rel.catalog = artist, catalog
                return rel
    try:
        for a in _http.get("https://api.deezer.com/search/album", params={"q": f"{artist} {title}"},
                           timeout=10).json().get("data", []):
            if same(a["title"], a["artist"]["name"]):
                ts = _http.get(f"https://api.deezer.com/album/{a['id']}/tracks", timeout=10).json()["data"]
                return Release(artist, a["title"], "Deezer", a["link"],
                               [AlbumTrack(t["title"], t["duration"], t.get("preview")) for t in ts], catalog)
    except (requests.RequestException, ValueError, KeyError):
        pass
    try:
        q = f'release:"{title}" AND artist:"{artist}"' + (f' OR catno:"{catalog}"' if catalog else "")
        for r in _mb("release", query=q).get("releases", []):
            credit = " ".join(c.get("name", "") for c in r.get("artist-credit", []))
            if r.get("score", 0) >= 90 and same(r["title"], credit):
                full = _mb(f"release/{r['id']}", inc="recordings")
                tracks = [AlbumTrack(t["title"], round((t.get("length") or 0) / 1000))
                          for m in full.get("media", []) for t in m.get("tracks", [])]
                return Release(artist, r["title"], "MusicBrainz", f"https://musicbrainz.org/release/{r['id']}",
                               tracks, catalog)
    except (requests.RequestException, ValueError):
        pass
    return None


# ---------------------------------------------------------------- single tracks

def reference(q: Query) -> Reference | None:
    """Official lengths / BPM / audio for one track, from every source that has it."""
    asked, want, want_artist = q.all_tokens, tokens(q.title) - STOP, tokens(q.artist) - STOP

    def same(title: str, artist: str) -> bool:
        t = tokens(title)
        return bool((want <= t or compact_in(q.title, title)) and not other_version(title, asked)
                    and not (t & VARIANTS) - asked and want_artist & tokens(artist))

    lengths: list[int] = []
    clips, bpm, sources = [], None, []

    # Deezer: lengths, BPM, 30s previews
    try:
        hits = _http.get("https://api.deezer.com/search", params={"q": q.search_text}, timeout=10).json()
        for t in hits.get("data", []):
            if not same(t["title"], t["artist"]["name"]):
                continue
            lengths.append(t["duration"])
            if t.get("preview") and len(clips) < 4 and (c := _safe(lambda: clip_from_preview(t["preview"]))) is not None:
                clips.append(c)
                bpm = bpm or _safe(lambda: _http.get(f"https://api.deezer.com/track/{t['id']}", timeout=10)
                                   .json().get("bpm")) or None
        if lengths:
            sources.append("Deezer")
    except (requests.RequestException, ValueError):
        pass

    # MusicBrainz: lengths (incl. vinyl-only releases)
    n = len(lengths)
    title = re.sub(r"\((original|extended)( mix)?\)", "", q.title, flags=re.I).replace('"', "").strip()
    for r in _safe(lambda: _mb("recording", query=f'recording:"{title}" AND artist:"{q.artist.replace(chr(34), "")}"',
                               limit=15), {}).get("recordings", []):
        credit = " ".join(a.get("name", "") for a in r.get("artist-credit", []))
        if r.get("score", 0) >= 85 and (r.get("length") or 0) >= 90_000 and same(r["title"], credit):  # <90s: snippets
            lengths.append(round(r["length"] / 1000))
    if len(lengths) > n:
        sources.append("MusicBrainz")

    # Bandcamp: underground releases missing from Deezer, with full-length streams
    if not clips:
        for hit in _bandcamp_search(f"{q.artist} {q.title}", "t")[:5]:
            t_title, by = _split_artist(hit.get("name", ""), hit.get("band_name", ""))
            if hit.get("type") != "t" or not same(t_title, f"{by} {hit.get('name', '')}"):
                continue
            t = ((_bandcamp_page(hit["item_url_path"]) or {}).get("trackinfo") or [{}])[0]
            if t.get("duration"):
                lengths.append(round(t["duration"]))
                url = (t.get("file") or {}).get("mp3-128")
                if url and (c := clip_from_stream(url, t["duration"])) is not None:
                    clips.append(c)
                sources.append("Bandcamp")
                break

    uniq: list[int] = []
    for l in sorted(lengths):
        if not uniq or l - uniq[-1] > 3:
            uniq.append(l)
    return Reference(uniq, clips, bpm, sources) if (uniq or clips) else None
