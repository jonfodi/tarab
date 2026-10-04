"""The user's folders: HQ (verified originals named "Artist - Title.ext") and LQ (320k MP3 copies for old CDJs)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import audio
from .audio import AUDIO_EXT, LOSSLESS_CODECS
from .text import STOP, Query, tokens


def clean_name(path: Path, q: Query) -> str:
    """"Artist - Title" from the file's tags (falling back to the query), without "(Original Mix)", label tags,
    track numbers, curly quotes or characters macOS/CDJs choke on."""
    t = audio.tags(path)
    artist, title = t.get("artist") or q.artist, t.get("title") or q.title
    if artist.lower() == q.artist.lower() and not q.artist.islower():
        artist = q.artist                                   # your capitalisation wins over a lowercase tag
    title = re.sub(rf"^\s*{re.escape(artist)}\s*-\s*", "", title, flags=re.I)
    title = re.sub(r"\s*\((original|original mix)\)", "", title, flags=re.I)
    title = re.sub(r"\s*\[[^\]]*(records|recordings|audio|music|label)[^\]]*\]", "", title, flags=re.I)
    title = re.sub(r"\[([^\]]+)\]", r"(\1)", title)
    title = re.sub(r"\(\s*([^)]*?)\s*\)", r"(\1)", title)
    name = f"{artist.rstrip(' .')} - {title}"
    name = name.replace("/", "-").replace(":", "").replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", name).strip(" .")


def _unique(dest: Path) -> Path:
    i = 2
    out = dest
    while out.exists():
        out = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        i += 1
    return out


def file_into_hq(src: Path, q: Query, hq: Path, staging_root: Path, replace: Path | None = None) -> Path:
    """Move a verified download into HQ under its clean name. `replace` is an older copy being upgraded."""
    hq.mkdir(parents=True, exist_ok=True)
    dest = hq / f"{clean_name(src, q)}{src.suffix.lower()}"
    if replace and replace.exists():
        replace.unlink()
    if dest.exists():
        dest = _unique(dest)
    shutil.move(str(src), dest)
    _tidy(src.parent, staging_root)
    return dest


def _tidy(folder: Path, root: Path) -> None:
    """Remove empty slskd download folders up to `root`."""
    while folder != root and root in folder.parents:
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def find_finished(q: Query, staging: Path) -> Path | None:
    """A finished download for this track sitting in slskd's folder (e.g. one parked in a peer's queue earlier)."""
    if not staging.exists():
        return None
    want_artist = tokens(q.artist) - STOP
    for p in staging.rglob("*"):
        if not p.is_file() or p.suffix.lower().lstrip(".") not in AUDIO_EXT:
            continue
        if not want_artist & tokens(str(p.relative_to(staging))):
            continue
        if q.same_title(p.stem) or (q.length and tokens(q.title) - STOP <= tokens(p.stem)
                                    and abs(audio.duration(p) - q.length) <= 4):
            return p
    return None


def export_lq(src: Path, lq: Path, force: bool = False) -> tuple[Path, str]:
    """MP3 copy for old CDJs: 320k CBR, 44.1kHz, ID3v2.3 (older players can't read v2.4), cover art kept.
    MP3 sources are copied untouched: re-encoding lossy audio only loses more."""
    lq.mkdir(parents=True, exist_ok=True)
    dest = lq / f"{src.stem}.mp3"
    if not force and dest.exists() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest, "up to date"
    if src.suffix.lower() == ".mp3":
        shutil.copy2(src, dest)
        return dest, "copied (already mp3)"
    c = audio.codec(src)
    art = audio.has_cover(src)
    tmp = dest.with_suffix(".part.mp3")
    base = [audio._bin("ffmpeg"), "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0:a:0"]
    enc = ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2", "-map_metadata", "0",
           "-id3v2_version", "3", "-write_id3v1", "1", str(tmp)]
    with_art = ["-map", "0:v:0", "-c:v", "copy", "-disposition:v", "attached_pic"] if art else []
    if subprocess.run(base + with_art + enc, capture_output=True).returncode:
        subprocess.run(base + enc, check=True, capture_output=True)   # odd cover formats: drop the art
    tmp.replace(dest)
    note = f"encoded 320k from {c}" + ("" if c in LOSSLESS_CODECS else " (lossy source: not a true 320)")
    return dest, note
