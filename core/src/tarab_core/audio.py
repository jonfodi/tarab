"""ffmpeg/ffprobe helpers and the fake-file (transcode) detector."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from enum import IntEnum
from pathlib import Path

import numpy as np

LOSSLESS_EXT = {"flac", "wav", "aiff", "aif"}
AUDIO_EXT = LOSSLESS_EXT | {"mp3", "m4a"}
LOSSLESS_CODECS = {"flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_s16be", "pcm_s24be", "pcm_s32be",
                   "pcm_f32le", "pcm_f32be"}


class Tier(IntEnum):
    TOO_LOW = 0
    T256 = 1      # 256k MP3/AAC
    T320 = 2      # 320 CBR / V0
    LOSSLESS = 3

    @property
    def label(self) -> str:
        return {0: "too low", 1: "256", 2: "320/V0", 3: "lossless"}[self.value]


def _bin(name: str) -> str:
    """ffmpeg/ffprobe: bundled next to the app if present, else from PATH."""
    from .config import bundled_tool
    return bundled_tool(name) or shutil.which(name) or name


def ffprobe(path: Path | str, *args: str) -> str:
    return subprocess.run([_bin("ffprobe"), "-v", "error", *args, str(path)],
                          capture_output=True, text=True).stdout


def _num(text: str) -> float | None:
    m = re.search(r"\d+(\.\d+)?", text)
    return float(m.group()) if m else None


def duration(path: Path) -> float:
    return _num(ffprobe(path, "-show_entries", "format=duration", "-of", "default=nw=1:nk=1")) or 0.0


def sample_rate(path: Path) -> int:
    return int(_num(ffprobe(path, "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                            "-of", "default=nw=1:nk=1")) or 44100)


def codec(path: Path) -> str:
    out = ffprobe(path, "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1")
    return out.split()[0] if out.split() else ""


def tags(path: Path) -> dict[str, str]:
    out = ffprobe(path, "-show_entries", "format_tags", "-of", "json")
    raw = (json.loads(out or "{}").get("format", {}) or {}).get("tags") or {}
    return {k.lower(): v for k, v in raw.items()}


def has_cover(path: Path) -> bool:
    return bool(ffprobe(path, "-select_streams", "v", "-show_entries", "stream=index", "-of", "csv=p=0").strip())


def decode(src: str | Path, sr: int | None = 11025, ss: float | None = None, t: float | None = None) -> np.ndarray:
    """Mono float32 PCM. `src` may be a file or a URL (Bandcamp/Deezer streams). sr=None keeps the native rate."""
    cmd = [_bin("ffmpeg"), "-nostdin", "-v", "error", *(["-ss", f"{ss:.2f}"] if ss else []),
           *(["-t", str(t)] if t else []), "-i", str(src), "-map", "0:a:0", "-ac", "1",
           *(["-ar", str(sr)] if sr else []), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32)


# ---------------------------------------------------------------- transcode detection

def find_cliff(path: Path, seconds: int = 60) -> float | None:
    """Frequency of an encoder lowpass "cliff", or None if the top end fades out naturally.

    A lossy encode leaves a brick wall: a ≥20 dB drop within 750 Hz into near-silence that stays silent up to
    Nyquist (128k ≈ 16-17 kHz, 192k ≈ 19 kHz, 320k ≈ 20 kHz). A real master rolls off gradually.
    """
    sr = sample_rate(path)
    start = max(0.0, duration(path) / 2 - seconds / 2)
    x = decode(path, sr=None, ss=start, t=seconds)
    n = 8192
    if len(x) < n:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[:: n // 2]
    p = (np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)) ** 2).mean(0)
    f = np.fft.rfftfreq(n, 1 / sr)
    ref = 10 * np.log10(p[(f > 2000) & (f < 8000)].mean() + 1e-30)
    edges = np.arange(12000, min(sr / 2, 22050) - 250, 250)
    lv = np.array([10 * np.log10(p[(f >= e) & (f < e + 250)].mean() + 1e-30) - ref for e in edges])
    for i in range(len(lv) - 1):
        above = lv[i + 1:]
        if lv[i] > -60 and above.max() < -55 and lv[i] - above[:3].max() >= 20:
            return float(edges[i] + 250)
    return None


def verified_tier(claimed: Tier, cliff: float | None) -> Tier:
    """What a file really is, given what it claims and where (if anywhere) its spectrum hits a wall."""
    if cliff is None or cliff >= 20800:
        return claimed
    if cliff >= 19200:
        return min(claimed, Tier.T320)    # e.g. a FLAC that was really a 320
    if cliff >= 18000:
        return min(claimed, Tier.T256)
    return Tier.TOO_LOW                    # 128/192-grade source


def claimed_tier(path: Path) -> Tier:
    """What a local file claims to be, from its headers."""
    if path.suffix.lower().lstrip(".") in LOSSLESS_EXT or codec(path) in LOSSLESS_CODECS:
        return Tier.LOSSLESS
    br = _num(ffprobe(path, "-select_streams", "a:0", "-show_entries", "stream=bit_rate", "-of", "default=nw=1:nk=1"))
    br = br or _num(ffprobe(path, "-show_entries", "format=bit_rate", "-of", "default=nw=1:nk=1")) or 0   # VBR: average
    return Tier.T320 if br >= 315000 else (Tier.T256 if br >= 220000 else Tier.TOO_LOW)


def check_quality(path: Path, claimed: Tier | None = None) -> tuple[Tier, Tier, float | None]:
    """(claimed, verified, cliff_hz)."""
    claimed = claimed if claimed is not None else claimed_tier(path)
    cliff = find_cliff(path)
    return claimed, verified_tier(claimed, cliff), cliff
