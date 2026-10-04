import shutil
import subprocess
from pathlib import Path

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real ~/Library/Application Support/rasa."""
    monkeypatch.setenv("RASA_HOME", str(tmp_path / "home"))


@pytest.fixture(scope="session")
def audio_dir(tmp_path_factory) -> Path:
    """Synthetic "masters" and transcodes, made once per test session.

    master_a / master_b: different "tracks" (different chord sequences over broadband noise, full band to 22kHz).
    fake_*: master_a pushed through lossy encoders and re-wrapped, the way fakes circulate on Soulseek.
    """
    d = tmp_path_factory.mktemp("audio")
    if not shutil.which("ffmpeg"):
        return d

    def track(name: str, notes: list[float], seconds: int = 150) -> None:
        # A chord change every 4s, plus pink noise so the spectrum extends to Nyquist like a real master.
        step = 4
        expr = "+".join(
            f"0.25*sin(2*PI*{f}*t)*between(mod(t,{step * len(notes)}),{i * step},{(i + 1) * step})"
            for i, f in enumerate(notes))
        ffmpeg("-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={seconds}",
               "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.25:d={seconds}:r=44100",
               "-filter_complex", "[0][1]amix=inputs=2,aformat=channel_layouts=stereo", "-c:a", "flac",
               str(d / f"{name}.flac"))

    track("master_a", [220.0, 261.6, 329.6, 392.0, 293.7, 349.2])
    track("master_b", [246.9, 311.1, 185.0, 415.3, 277.2, 369.9])
    a = d / "master_a.flac"
    ffmpeg("-i", str(a), "-b:a", "128k", str(d / "t128.mp3"))
    ffmpeg("-i", str(d / "t128.mp3"), str(d / "fake_flac_from128.flac"))
    ffmpeg("-i", str(a), "-b:a", "320k", str(d / "t320.mp3"))
    ffmpeg("-i", str(d / "t320.mp3"), str(d / "fake_flac_from320.flac"))
    ffmpeg("-i", str(d / "t128.mp3"), "-b:a", "320k", str(d / "fake320_from128.mp3"))
    # A different edit of track A: same music, 30s shorter
    ffmpeg("-i", str(a), "-t", "120", "-c:a", "flac", str(d / "edit_a.flac"))
    return d
