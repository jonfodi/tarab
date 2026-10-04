"""Is a downloaded file the track (and version) that was asked for?

Compares the file's chroma (which notes are playing, frame by frame) against official audio: Deezer's 30s
previews or a slice of Bandcamp's full-length stream. Calibrated on real cases: right track 0.94-1.00, other
versions/edits 0.70-0.91, a mislabeled different track 0.53.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np

from . import audio
from .text import fmt_len

ID_OK, ID_WRONG = 0.93, 0.80
SR = 11025
LENGTH_TOLERANCE = 4   # s


class Verdict(str, Enum):
    OK = "ok"            # matches official audio / length
    UNSURE = "unsure"    # probably another version or edit: listen
    WRONG = "wrong"      # different track, or not the length you pinned
    UNKNOWN = "unknown"  # no reference data to check against


@dataclass
class Reference:
    lengths: list[int] = field(default_factory=list)          # official lengths (s) of matching versions
    clips: list[np.ndarray] = field(default_factory=list)     # chroma features of official audio
    bpm: float | None = None
    sources: list[str] = field(default_factory=list)
    strict: bool = False   # the user pinned the length: any other length is wrong, not "unsure"

    def describe(self) -> str:
        ls = ", ".join(fmt_len(l) for l in self.lengths) or "?"
        bpm = f" · {self.bpm:g} BPM" if self.bpm else ""
        clips = f" · {len(self.clips)} audio clip{'s' * (len(self.clips) > 1)}" if self.clips else ""
        return f"{ls}{bpm}{clips} ({', '.join(self.sources) or 'none'})"

    def pinned(self, length: int) -> Reference:
        """Same reference, but only `length` is acceptable."""
        return Reference([length], self.clips, self.bpm, [*self.sources, "you"], strict=True)


def features(x: np.ndarray, n: int = 4096, hop: int = 1024) -> np.ndarray:
    """Per-frame chroma: 12 pitch classes, log, gain-normalised. Robust to mastering, codec and EQ differences,
    but different for a different piece of music."""
    if len(x) < n:
        return np.zeros((0, 12), np.float32)
    fr = np.lib.stride_tricks.sliding_window_view(x, n)[::hop] * np.hanning(n)
    p = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1 / SR)
    m = (f > 80) & (f < 3000)
    pc = np.round(12 * np.log2(f[m] / 440)) % 12
    c = np.log(np.stack([p[:, m][:, pc == k].sum(1) for k in range(12)], 1) + 1e-9)
    c -= c.mean(1, keepdims=True)
    return c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-9)


def clip_from_preview(url_or_path: str) -> np.ndarray:
    """A 30s preview, minus the 3s fades at each end."""
    x = audio.decode(url_or_path, sr=SR)
    return features(x[3 * SR: -3 * SR] if len(x) > 12 * SR else x)


def clip_from_stream(url: str, length: float) -> np.ndarray | None:
    """30s from the middle of a full-length stream."""
    try:
        return features(audio.decode(url, sr=SR, ss=max(0.0, length / 2 - 15), t=30))
    except Exception:
        return None


def clip_match(path: Path, clips: list[np.ndarray]) -> float:
    """Best alignment score of any clip anywhere inside the file (1.0 = identical)."""
    full = features(audio.decode(path, sr=SR))
    best = 0.0
    for clip in clips:
        if len(clip) and len(full) >= len(clip):
            win = np.lib.stride_tricks.sliding_window_view(full, clip.shape)[:, 0]
            best = max(best, float(np.einsum("ijk,jk->i", win, clip).max() / len(clip)))
    return best


def check(path: Path, ref: Reference | None) -> tuple[Verdict, str]:
    if not ref:
        return Verdict.UNKNOWN, "no reference data"
    dur = audio.duration(path)
    len_ok = None if not ref.lengths else min(abs(dur - l) for l in ref.lengths) <= LENGTH_TOLERANCE
    len_note = f"length {fmt_len(dur)}" + (f" vs official {', '.join(map(fmt_len, ref.lengths))}" if ref.lengths else "")
    if ref.strict and not len_ok:
        return Verdict.WRONG, len_note
    if ref.clips:
        score = clip_match(path, ref.clips)
        note = f"audio match {score:.2f}, {len_note}"
        if score < ID_WRONG:
            return Verdict.WRONG, note
        if score >= ID_OK and len_ok is not False:
            return Verdict.OK, note
        return Verdict.UNSURE, note + " (likely another version/edit)"
    return (Verdict.OK if len_ok else Verdict.UNSURE), len_note
