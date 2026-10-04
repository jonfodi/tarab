"""Fake-file detection and identity matching on synthetic audio."""
from tarab_core import audio, identity
from tarab_core.audio import Tier
from tarab_core.identity import Reference, Verdict

from .conftest import needs_ffmpeg


@needs_ffmpeg
def test_real_lossless_passes(audio_dir):
    assert audio.check_quality(audio_dir / "master_a.flac")[1] == Tier.LOSSLESS


@needs_ffmpeg
def test_fake_flacs_are_caught(audio_dir):
    assert audio.check_quality(audio_dir / "fake_flac_from128.flac")[1] == Tier.TOO_LOW
    assert audio.check_quality(audio_dir / "fake_flac_from320.flac")[1] == Tier.T320


@needs_ffmpeg
def test_real_and_fake_320(audio_dir):
    assert audio.check_quality(audio_dir / "t320.mp3")[1] == Tier.T320
    assert audio.check_quality(audio_dir / "fake320_from128.mp3")[1] == Tier.TOO_LOW


@needs_ffmpeg
def test_identity_same_track_matches(audio_dir):
    clip = identity.features(audio.decode(audio_dir / "master_a.flac", sr=identity.SR, ss=60, t=30))
    ref = Reference([150], [clip], None, ["test"])
    assert identity.check(audio_dir / "t320.mp3", ref)[0] == Verdict.OK   # survives lossy encoding


@needs_ffmpeg
def test_identity_different_track_is_wrong(audio_dir):
    clip = identity.features(audio.decode(audio_dir / "master_a.flac", sr=identity.SR, ss=60, t=30))
    ref = Reference([150], [clip], None, ["test"])
    verdict, note = identity.check(audio_dir / "master_b.flac", ref)
    assert verdict == Verdict.WRONG, note


@needs_ffmpeg
def test_identity_other_edit_is_unsure(audio_dir):
    clip = identity.features(audio.decode(audio_dir / "master_a.flac", sr=identity.SR, ss=60, t=30))
    ref = Reference([150], [clip], None, ["test"])
    assert identity.check(audio_dir / "edit_a.flac", ref)[0] == Verdict.UNSURE   # same music, wrong length


@needs_ffmpeg
def test_pinned_length_is_strict(audio_dir):
    assert identity.check(audio_dir / "edit_a.flac", Reference().pinned(150))[0] == Verdict.WRONG
    assert identity.check(audio_dir / "master_a.flac", Reference().pinned(150))[0] == Verdict.OK


def test_no_reference_is_unknown(tmp_path):
    assert identity.check(tmp_path / "x.flac", None)[0] == Verdict.UNKNOWN


@needs_ffmpeg
def test_low_match_on_a_different_length_is_an_edit_not_a_fake(audio_dir):
    clip = identity.features(audio.decode(audio_dir / "master_a.flac", sr=identity.SR, ss=60, t=30))
    # clip from the 150s version; master_b is 150s too -> same length, different music -> wrong (mislabeled)
    ref = Reference([150], [clip], None, ["test"], clip_lengths=[150])
    assert identity.check(audio_dir / "master_b.flac", ref)[0] == Verdict.WRONG
    # if the clip came from a 300s version, a 150s file that doesn't contain it is just another edit
    ref = Reference([150, 300], [clip], None, ["test"], clip_lengths=[300])
    assert identity.check(audio_dir / "master_b.flac", ref)[0] == Verdict.UNSURE


@needs_ffmpeg
def test_reference_audio_is_downloaded_by_python_not_ffmpeg(audio_dir, monkeypatch):
    """The bundled ffmpeg can't verify TLS certificates; URLs must never reach it."""
    import functools
    import http.server
    import threading

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(audio_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    real_decode = audio.decode

    def guarded(src, *a, **k):
        assert not str(src).startswith("http"), "ffmpeg was handed a URL"
        return real_decode(src, *a, **k)

    monkeypatch.setattr(audio, "decode", guarded)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/t320.mp3"
    assert len(identity.clip_from_preview(url)) > 0
    assert identity.clip_from_stream(url, 150) is not None
    httpd.shutdown()
