"""The fetch pipeline for one track: reference → search → rank → download → verify identity & quality → file."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import requests

from . import audio, library, sources
from .audio import Tier
from .config import Settings
from .events import Event, Sink, null_sink
from .identity import Reference, Verdict, check
from .ranking import Candidate, candidates
from .slskd import Slskd, SlskdError, Transfer
from .state import State
from .text import Query, fmt_len, norm


class Status(str, Enum):
    OK = "ok"                    # got it (or upgraded it)
    ALREADY = "already"          # already in the library, nothing better to get
    QUEUED = "queued"            # waiting in a peer's upload queue; a later run collects it
    LOW_ONLY = "low_only"        # only below-DJ-quality copies exist
    NOT_FOUND = "not_found"
    FAILED = "failed"            # candidates existed but none downloaded/passed
    DRY_RUN = "dry_run"


@dataclass
class Result:
    query: str
    status: Status
    path: str | None = None
    tier: str | None = None
    verdict: str | None = None
    note: str = ""
    candidates: list[dict] = field(default_factory=list)

    @property
    def needs_listen(self) -> bool:
        return self.status in (Status.OK, Status.ALREADY) and self.verdict in (Verdict.UNSURE, Verdict.UNKNOWN)

    def as_dict(self) -> dict:
        return {"query": self.query, "status": self.status.value, "path": self.path, "tier": self.tier,
                "verdict": self.verdict, "note": self.note, "needs_listen": self.needs_listen,
                "candidates": self.candidates}


@dataclass
class Options:
    dry_run: bool = False
    upgrade: bool = False        # re-fetch library tracks that aren't lossless yet
    allow_low: bool = False      # accept below-256 copies when nothing better exists
    make_lq: bool | None = None  # None: use settings


class Parked(Exception):
    pass


@dataclass
class _Got:
    path: Path
    tier: Tier
    verdict: Verdict
    note: str
    cand: Candidate


class Fetcher:
    def __init__(self, settings: Settings, slskd: Slskd, state: State, sink: Sink = null_sink,
                 lookup: Callable[[Query], Reference | None] = sources.reference):
        self.s, self.slskd, self.state, self.sink, self.lookup = settings, slskd, state, sink, lookup

    def _emit(self, q: Query, kind: str, message: str = "", **data) -> None:
        self.sink(Event(kind, str(q), message, data))

    # ------------------------------------------------------------ main entry

    def fetch(self, q: Query, opts: Options | None = None) -> Result:
        opts = opts or Options()
        self._emit(q, "start", str(q))
        result = self._fetch(q, opts)
        if result.status != Status.DRY_RUN:
            self.state.log(str(q), result.status.value, result.note or (result.path or ""))
        self._emit(q, "done", result.status.value, **result.as_dict())
        return result

    def _fetch(self, q: Query, opts: Options) -> Result:
        ref = self._reference(q)
        have = self.state.track(q.key)
        have_path = Path(have["path"]) if have else None
        have_tier = Tier(have["tier"]) if have else Tier.TOO_LOW

        # A download parked in a peer's queue on an earlier run may have finished since.
        if not opts.dry_run and (got := self._collect_finished(q, ref)):
            return self._file(q, got, have_path, opts, "collected finished download")

        wrong_version = bool(have and q.length and abs((have["length"] or 0) - q.length) > 4)
        if wrong_version:
            self._emit(q, "skip", f"you have a {fmt_len(have['length'])} version; replacing it")
            have_tier = Tier.TOO_LOW
        if have and not wrong_version and (not opts.upgrade or have_tier == Tier.LOSSLESS):
            return Result(str(q), Status.ALREADY, have["path"], have_tier.label, have["verdict"], "already in library")

        cands, low, responses = self._search(q, ref, opts)
        if not cands:
            if low:
                best = low[0]
                return Result(str(q), Status.LOW_ONLY, note=f"only low quality available ({len(low)} files, best "
                              f"{best.ext} {round(best.kbps or 0)}kbps)", candidates=[c.as_dict() for c in low[:5]])
            return Result(str(q), Status.NOT_FOUND, note=f"{responses} peers responded, no matching files")
        if opts.dry_run:
            return Result(str(q), Status.DRY_RUN, candidates=[c.as_dict() for c in cands[:10]])
        if have:
            cands = [c for c in cands if c.tier > have_tier]
            if not cands:
                return Result(str(q), Status.ALREADY, have["path"], have_tier.label, have["verdict"],
                              f"nothing better than your {have_tier.label} copy")

        try:
            got = self._download_best(q, cands, ref, opts)
        except Parked as e:
            return Result(str(q), Status.QUEUED, note=str(e))
        if not got:
            return Result(str(q), Status.FAILED, note="no candidate downloaded and passed the checks")
        return self._file(q, got, have_path if (wrong_version or got.tier > have_tier) else None, opts)

    # ------------------------------------------------------------ steps

    def _reference(self, q: Query) -> Reference | None:
        ref = q.ref
        if ref is None and self.s.check_identity:
            try:
                ref = self.lookup(q)
            except Exception as e:   # lookups are best effort
                self._emit(q, "reference", f"lookup failed: {e}")
        if q.length and not (q.ref and q.ref.strict):
            ref = (ref or Reference()).pinned(q.length)
        self._emit(q, "reference", ref.describe() if ref else "none found (can't check identity)",
                   lengths=ref.lengths if ref else [], sources=ref.sources if ref else [],
                   bpm=ref.bpm if ref else None)
        return ref

    def _search(self, q: Query, ref: Reference | None, opts: Options) -> tuple[list[Candidate], list[Candidate], int]:
        flaky, wrong = self.state.flaky(), self.state.wrong_sizes()
        lengths = ref.lengths if ref else None
        texts = []
        if q.catalog:
            texts.append(f"{q.search_text} {norm(q.catalog)}")
        texts.append(q.search_text)
        # Looser retries: first artist word + title, then the title alone (survives a misspelled artist).
        first_artist = (norm(q.artist.replace("'", " ")).split() or [""])[0]
        texts += [" ".join([first_artist, *q.title_words]), " ".join(q.title_words)]
        cands, responses = [], []
        for text in dict.fromkeys(t.strip() for t in texts if t.strip()):
            self._emit(q, "search", f"searching '{text}'", text=text)
            responses = self.slskd.search(text, self.s.search_timeout)
            cands = candidates(q, responses, flaky, wrong, lengths)
            if cands:
                break
        low = [c for c in cands if c.tier < self.s.min_tier]
        if not opts.allow_low:
            cands = [c for c in cands if c.tier >= self.s.min_tier]
        self._emit(q, "candidates", f"{len(responses)} peers, {len(cands)} matching files"
                   + (f", {sum(c.ref_match for c in cands)} with an official length" if lengths else ""),
                   count=len(cands), peers=len(responses), top=[c.as_dict() for c in cands[:5]])
        return cands, low, len(responses)

    def _download_best(self, q: Query, cands: list[Candidate], ref: Reference | None, opts: Options) -> _Got | None:
        best: _Got | None = None
        attempts = 0
        for i, c in enumerate(cands):
            if attempts >= self.s.max_attempts or (best and best.tier >= c.tier):
                break
            attempts += 1
            self._emit(q, "try", f"try {attempts}: {c.describe()}", candidate=c.as_dict())
            rest = [o for o in cands[i + 1:] if o.username != c.username]
            fallback = any(o.tier >= c.tier for o in rest)
            try:
                path = self._download(q, c, max_eta=self.s.max_eta if fallback else None,
                                      park=not rest and best is None)
            except SlskdError as e:
                self._reject(q, c, f"refused: {e}", strike=True)
                continue
            except requests.RequestException as e:
                self._reject(q, c, f"slskd error: {e}")
                continue
            if not path:
                continue

            verdict, note = check(path, ref) if ref else (Verdict.UNKNOWN, "no reference data")
            if verdict == Verdict.WRONG:
                self._reject(q, c, f"wrong track: {note}")
                if note.startswith("audio"):   # audio mismatch = mislabeled; a length miss is just another version
                    self.state.mark_wrong(c.size, c.username, c.filename, note)
                path.unlink(missing_ok=True)
                continue
            self._emit(q, "identity", note, verdict=verdict.value, note=note)

            claimed, tier, cliff = audio.check_quality(path, c.tier)
            cliff_s = f"cutoff {cliff / 1000:.1f}kHz" if cliff else "no lowpass"
            self._emit(q, "quality", f"{'verified' if tier == claimed else 'claims ' + claimed.label + ' but is'} "
                       f"{tier.label} ({cliff_s})", claimed=claimed.label, verified=tier.label, cliff=cliff)
            if (tier >= self.s.min_tier or opts.allow_low) and (best is None or tier > best.tier):
                if best:
                    best.path.unlink(missing_ok=True)
                best = _Got(path, tier, verdict, note, c)
            else:
                path.unlink(missing_ok=True)
            if tier == c.tier:
                break
        return best

    def _download(self, q: Query, c: Candidate, max_eta: int | None, park: bool) -> Path | None:
        """Download one candidate. Returns the local file, None to try the next candidate, or raises Parked when
        this is the only source and it's queued: slskd keeps the transfer and a later run collects it."""
        started = time.time()
        self.slskd.enqueue(c.username, c.filename, c.size)
        last_bytes, last_progress, flowing = -1, time.time(), None
        while True:
            time.sleep(2)
            t = self.slskd.transfer(c.username, c.filename)
            if not t:
                if time.time() - started > self.s.queue_timeout:
                    self._reject(q, c, "transfer never appeared", strike=True)
                    return None
                continue
            if t.succeeded:
                return self._locate(c, started)
            if t.done:
                self._reject(q, c, f"{t.state} {t.exception or ''}".strip(), strike=True)
                return None
            if t.bytes_transferred != last_bytes:
                last_bytes, last_progress = t.bytes_transferred, time.time()
            waited = time.time() - last_progress
            queued = "Queued" in t.state
            if park and "Remotely" in t.state and waited > self.s.queue_timeout:
                place = f" (place {t.place_in_queue})" if t.place_in_queue else ""
                self._emit(q, "parked", f"waiting in {c.username}'s queue{place}", username=c.username)
                raise Parked(f"waiting in {c.username}'s queue{place}")
            if (queued and waited > self.s.queue_timeout) or ("InProgress" in t.state and waited > self.s.stall_timeout) \
                    or waited > max(self.s.queue_timeout, self.s.stall_timeout) + 60:
                return self._give_up(q, c, t, f"no progress for {int(waited)}s ({t.state})")
            if "InProgress" in t.state and t.bytes_transferred > 0:
                flowing = flowing or (time.time(), t.bytes_transferred)
                dt = time.time() - flowing[0]
                if max_eta and dt > 20:
                    speed = (t.bytes_transferred - flowing[1]) / dt
                    eta = (c.size - t.bytes_transferred) / speed if speed > 0 else float("inf")
                    if eta > max_eta:
                        return self._give_up(q, c, t, f"too slow ({speed / 1e3:.0f}KB/s, ~{eta / 60:.0f} min left)")
            pct = 100 * t.bytes_transferred / c.size if c.size else 0
            self._emit(q, "progress", f"{t.state} {pct:.0f}%", state=t.state, percent=round(pct, 1))

    def _give_up(self, q: Query, c: Candidate, t: Transfer, why: str) -> None:
        self.slskd.cancel(t)
        self._reject(q, c, why, strike=True)
        return None

    def _reject(self, q: Query, c: Candidate, reason: str, strike: bool = False) -> None:
        if strike:
            self.state.strike(c.username, reason)
        self._emit(q, "rejected", reason, username=c.username, reason=reason)

    def _locate(self, c: Candidate, since: float) -> Path | None:
        """slskd's folder layout differs between versions: find the finished file by size and recency."""
        root = self.s.downloads
        hits = [p for p in root.rglob("*") if p.is_file() and p.stat().st_size == c.size
                and p.stat().st_mtime >= since - 5]
        hits.sort(key=lambda p: (p.suffix.lower().lstrip(".") == c.ext, p.name == c.name), reverse=True)
        return hits[0] if hits else None

    def _collect_finished(self, q: Query, ref: Reference | None) -> _Got | None:
        p = library.find_finished(q, self.s.downloads)
        if not p:
            return None
        verdict, note = check(p, ref) if ref else (Verdict.UNKNOWN, "no reference data")
        if verdict == Verdict.WRONG:
            self._emit(q, "rejected", f"finished download is the wrong track ({note}); deleted", reason=note)
            p.unlink()
            return None
        claimed, tier, _ = audio.check_quality(p)
        if tier < self.s.min_tier:
            p.unlink()
            return None
        dummy = Candidate("", p.name, p.stat().st_size, p.suffix.lstrip("."), None, None, None, None, False, 0, 0)
        return _Got(p, tier, verdict, note, dummy)

    def _file(self, q: Query, got: _Got, replace: Path | None, opts: Options, how: str = "") -> Result:
        dest = library.file_into_hq(got.path, q, self.s.hq, self.s.downloads, replace=replace)
        self.state.put_track(q.key, q.artist, q.title, dest, got.tier, got.verdict.value, got.note,
                             audio.duration(dest), got.cand.username)
        if opts.make_lq if opts.make_lq is not None else self.s.make_lq:
            lq, note = library.export_lq(dest, self.s.lq)
            self._emit(q, "lq", f"LQ: {lq.name} ({note})", path=str(lq))
        if self.s.share_hq:
            self.slskd.rescan_shares()
        note = how or ("replaced your older copy" if replace else "")
        return Result(str(q), Status.OK, str(dest), got.tier.label, got.verdict.value, note)
