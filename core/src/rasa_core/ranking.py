"""Turn raw Soulseek search responses into ranked download candidates for a query."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .audio import LOSSLESS_EXT, Tier
from .text import STOP, VARIANTS, Query, basename, compact, compact_in, fmt_len, other_version, tokens

MIN_LENGTH = 90   # s: shorter files are previews/clips

# DJ-mix CDs: tracks are short edits blended into their neighbours, the most-shared copy of many classics
# (e.g. Robert Hood's 3:41 cut on Fabric 39 vs the 7:37 original). Ranked last unless nothing else exists.
MIX_COMPILATION = re.compile(r"mixed by|\bdj[ -]?mix\b|\bmix ?cd\b|continuous mix|\bfabric ?(live)? ?\d+|"
                             r"dj[ -]?kicks|global underground|\bbalance ?\d+|\(mixed\)|\bmixed\b", re.I)
LENGTH_TOLERANCE = 4


@dataclass
class Candidate:
    username: str
    filename: str            # full remote path
    size: int
    ext: str
    kbps: float | None
    length: int | None
    bit_depth: int | None
    sample_rate: int | None
    free_slot: bool
    queue: int
    speed: int               # bytes/s the peer reports
    is_vbr: bool = False
    tier: Tier = Tier.TOO_LOW
    majority: bool = False       # length matches what most copies of this track agree on
    ref_match: bool = False      # length matches an official length
    from_release: bool = False   # path contains the catalog number asked for
    version_bonus: int = 0       # "Extended" / "Original Mix" in the name
    flaky_peer: bool = False     # peer stalled or refused before
    mix_compilation: bool = False  # from a DJ-mix CD (short blended edit)

    @property
    def name(self) -> str:
        return basename(self.filename)

    def sort_key(self):
        """Higher is better. Version certainty first, then quality, then how fast we'll actually get it."""
        return (self.from_release, not self.mix_compilation, self.ref_match, self.majority, self.tier,
                (self.kbps or 0) if self.tier == Tier.TOO_LOW else 0,
                not self.flaky_peer, self.free_slot, self.version_bonus, self.ext != "wav",
                (self.bit_depth or 16) >= 24, -self.queue, self.speed)

    def describe(self) -> str:
        q = f"{self.ext} {self.bit_depth or '?'}bit/{(self.sample_rate or 0) / 1000:g}k" if self.tier == Tier.LOSSLESS \
            else f"{self.ext} {round(self.kbps or 0)}kbps"
        slot = "free" if self.free_slot else f"queue {self.queue}"
        return f"[{self.tier.label:8}] {q:22} {fmt_len(self.length):>5}  {slot:10} {self.speed / 1e6:5.1f}MB/s  " \
               f"{self.username}: {self.name}"

    def as_dict(self) -> dict:
        return {"username": self.username, "name": self.name, "size": self.size, "ext": self.ext,
                "kbps": round(self.kbps) if self.kbps else None, "length": self.length, "tier": self.tier.label,
                "free_slot": self.free_slot, "queue": self.queue, "speed": self.speed}


def classify(c: Candidate) -> Tier:
    k = c.kbps or 0
    if c.ext in LOSSLESS_EXT:
        return Tier.LOSSLESS if k == 0 or k >= 400 else Tier.TOO_LOW   # "lossless" under 400k is fake/corrupt
    if c.ext == "m4a":
        return Tier.LOSSLESS if k >= 500 else (Tier.T256 if k >= 250 else Tier.TOO_LOW)   # ALAC vs AAC
    if c.ext == "mp3":
        if k >= 315 or (k >= 220 and c.is_vbr):
            return Tier.T320
        return Tier.T256 if k >= 250 else Tier.TOO_LOW
    return Tier.TOO_LOW   # ogg/opus/wma: not DJ-software friendly


def matches(q: Query, filename: str) -> bool:
    """Does this remote file look like the requested track and version?"""
    if "@eaDir" in filename or "@Syno" in filename:   # Synology NAS metadata
        return False
    base = basename(filename)
    base_t = tokens(re.sub(r"\.[^.]+$", "", base))
    want_title, want_artist = tokens(q.title) - STOP, tokens(q.artist) - STOP
    if not compact_in(q.title, base) and (len(want_title) <= 1 or not want_title <= base_t):  # "On & On": need it all
        return False
    if want_artist and len(want_artist & tokens(filename)) / len(want_artist) < 0.6:
        return False
    return True


def candidates(q: Query, responses: list[dict], flaky: set[str] = frozenset(),
               known_wrong: set[int] = frozenset(), official_lengths: list[int] | None = None) -> list[Candidate]:
    asked = q.all_tokens
    out: list[Candidate] = []
    for r in responses:
        for f in r.get("files", []):
            name = f["filename"]
            if not matches(q, name) or f["size"] in known_wrong:
                continue
            base = basename(name)
            base_t = tokens(re.sub(r"\.[^.]+$", "", base))
            length = f.get("length")
            pinned = bool(q.length and length and abs(length - q.length) <= LENGTH_TOLERANCE)
            if ((base_t & VARIANTS) - asked or other_version(base, asked)) and not pinned:
                continue
            if length is not None and length < MIN_LENGTH:
                continue
            if q.length and length and not pinned:
                continue
            ext = (f.get("extension") or name.rsplit(".", 1)[-1]).lower().lstrip(".")
            kbps = f.get("bitRate")
            if ext in LOSSLESS_EXT | {"m4a"} and length:   # lossless bitRate is often missing: derive it
                kbps = f["size"] * 8 / length / 1000
            c = Candidate(r["username"], name, f["size"], ext, kbps, length, f.get("bitDepth"), f.get("sampleRate"),
                          bool(r.get("hasFreeUploadSlot")), r.get("queueLength") or 0, r.get("uploadSpeed") or 0,
                          bool(f.get("isVariableBitRate")))
            c.tier = classify(c)
            c.version_bonus = int("extended" in base_t) + int({"original", "mix"} <= base_t)
            c.flaky_peer = c.username in flaky
            c.mix_compilation = bool(MIX_COMPILATION.search(name)) and "mixed" not in asked
            out.append(c)

    # Versions share titles (album cut vs single edit). When copies clearly agree on a length, that's the version
    # people mean, so prefer it over a rarer one, even a higher-quality one.
    pool = [o for o in out if not o.mix_compilation] or out
    agree = {id(c): sum(1 for o in pool if c.length and o.length and abs(c.length - o.length) <= 3) for c in out}
    top = max(agree.values(), default=0)
    cat = compact(q.catalog) if q.catalog else None
    for c in out:
        c.majority = top >= 3 and agree[id(c)] >= top * 0.5
        c.from_release = bool(cat and cat in compact(c.filename))
        c.ref_match = bool(official_lengths and c.length and any(abs(c.length - l) <= 3 for l in official_lengths))
    return sorted(out, key=Candidate.sort_key, reverse=True)
