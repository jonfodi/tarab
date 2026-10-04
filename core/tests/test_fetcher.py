"""The whole pipeline against a fake slskd: search → download → identity → quality → filed in HQ."""
import shutil
from pathlib import Path

import pytest

from rasa_core import audio, fetcher as fetcher_mod, identity
from rasa_core.config import Settings
from rasa_core.fetcher import Fetcher, Options, Status
from rasa_core.identity import Reference
from rasa_core.slskd import Transfer
from rasa_core.state import State
from rasa_core.text import Query

from .conftest import needs_ffmpeg


class FakeSlskd:
    """Serves `catalog`: {(user, remote filename): (local source file or None, behaviour)}.
    behaviour: "ok" (completes), "queued" (stuck in the peer's queue), "rejected"."""

    def __init__(self, downloads: Path, catalog: dict, peers: dict | None = None):
        self.downloads, self.catalog, self.peers = downloads, catalog, peers or {}
        self.searches, self.enqueued, self.cancelled = [], [], []
        self._t: dict = {}

    def search(self, text, timeout_s=20, tries=3):
        self.searches.append(text)
        by_user: dict = {}
        for (user, name), (src, _) in self.catalog.items():
            size = src.stat().st_size if src else 1
            length = round(audio.duration(src)) if src else 300
            kbps = round(size * 8 / length / 1000) if name.endswith(".mp3") else None
            by_user.setdefault(user, []).append({"filename": name, "size": size, "length": length, "bitRate": kbps})
        return [{"username": u, "hasFreeUploadSlot": True, "uploadSpeed": self.peers.get(u, 1_000_000),
                 "queueLength": 0, "files": fs} for u, fs in by_user.items()]

    def enqueue(self, user, name, size):
        self.enqueued.append((user, name))
        src, how = self.catalog[(user, name)]
        state = {"ok": "Completed, Succeeded", "queued": "Queued, Remotely", "rejected": "Completed, Rejected"}[how]
        if how == "ok":
            dest = self.downloads / user / Path(name.replace("\\", "/")).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)
        self._t[(user, name)] = Transfer("id", user, name, state, size, size if how == "ok" else 0, 7, None)

    def transfer(self, user, name):
        return self._t.get((user, name))

    def cancel(self, t):
        self.cancelled.append(t.filename)

    def rescan_shares(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fetcher_mod.time, "sleep", lambda s: None)
    s = Settings(hq_dir=str(tmp_path / "HQ"), lq_dir=str(tmp_path / "LQ"), slskd_downloads=str(tmp_path / "dl"),
                 queue_timeout=0, stall_timeout=0, check_identity=False)
    return s, State(tmp_path / "rasa.db")


def clip_of(path: Path) -> Reference:
    c = identity.features(audio.decode(path, sr=identity.SR, ss=60, t=30))
    return Reference([150], [c], None, ["test"])


def run(s, state, slskd, line, ref=None, **opts):
    q = Query.parse(line)
    q.ref = ref
    events = []
    r = Fetcher(s, slskd, state, events.append).fetch(q, Options(**opts))
    return r, events


@needs_ffmpeg
def test_gets_verified_lossless_and_names_it(env, audio_dir):
    s, state = env
    slskd = FakeSlskd(s.downloads, {
        ("mp3er", r"x\Artist - Track A.mp3"): (audio_dir / "t320.mp3", "ok"),
        ("flacer", r"Artist\01 - Track A.flac"): (audio_dir / "master_a.flac", "ok")})
    r, _ = run(s, state, slskd, "Artist - Track A", clip_of(audio_dir / "master_a.flac"))
    assert r.status == Status.OK and r.tier == "lossless" and r.verdict == "ok"
    assert Path(r.path) == s.hq / "Artist - Track A.flac"
    assert slskd.enqueued == [("flacer", r"Artist\01 - Track A.flac")]
    assert state.track(Query.parse("Artist - Track A").key)


@needs_ffmpeg
def test_mislabeled_file_rejected_and_remembered(env, audio_dir):
    s, state = env
    slskd = FakeSlskd(s.downloads, {
        ("liar", r"x\Artist - Track A.flac"): (audio_dir / "master_b.flac", "ok"),      # wrong music
        ("honest", r"x\Artist - Track A.mp3"): (audio_dir / "t320.mp3", "ok")})
    r, events = run(s, state, slskd, "Artist - Track A", clip_of(audio_dir / "master_a.flac"))
    assert r.status == Status.OK and r.tier == "320/V0"
    assert any(e.kind == "rejected" and "wrong track" in e.message for e in events)
    assert (audio_dir / "master_b.flac").stat().st_size in state.wrong_sizes()


@needs_ffmpeg
def test_fake_flac_downgraded_then_better_source_used(env, audio_dir):
    s, state = env
    s.max_attempts = 4
    slskd = FakeSlskd(s.downloads, {
        ("faker", r"x\Artist - Track A.flac"): (audio_dir / "fake_flac_from320.flac", "ok"),
        ("real", r"y\Artist - Track A.flac"): (audio_dir / "master_a.flac", "ok")},
        peers={"faker": 9_000_000, "real": 1_000_000})
    r, _ = run(s, state, slskd, "Artist - Track A")
    assert r.tier == "lossless" and len(slskd.enqueued) == 2
    assert len(list(s.hq.iterdir())) == 1   # the fake was discarded


@needs_ffmpeg
def test_only_source_queued_is_parked_then_collected(env, audio_dir):
    s, state = env
    cat = {("busy", r"x\Repair - Page-R.flac"): (audio_dir / "master_a.flac", "queued")}
    slskd = FakeSlskd(s.downloads, cat)
    r, _ = run(s, state, slskd, "Repair - Page-R")
    assert r.status == Status.QUEUED and not slskd.cancelled
    # later: slskd finished it in the background
    (s.downloads / "busy").mkdir(parents=True)
    shutil.copy(audio_dir / "master_a.flac", s.downloads / "busy" / "Repair - Page-R.flac")
    r, _ = run(s, state, slskd, "Repair - Page-R")
    assert r.status == Status.OK and Path(r.path).parent == s.hq


@needs_ffmpeg
def test_refusing_peer_gets_a_strike(env, audio_dir):
    s, state = env
    slskd = FakeSlskd(s.downloads, {
        ("banned", r"x\Artist - Track A.flac"): (audio_dir / "master_a.flac", "rejected"),
        ("ok", r"y\Artist - Track A.mp3"): (audio_dir / "t320.mp3", "ok")})
    r, _ = run(s, state, slskd, "Artist - Track A")
    assert r.status == Status.OK and "banned" in state.flaky()


@needs_ffmpeg
def test_already_owned_then_upgrade(env, audio_dir):
    s, state = env
    slskd = FakeSlskd(s.downloads, {("u", r"x\Artist - Track A.mp3"): (audio_dir / "t320.mp3", "ok")})
    assert run(s, state, slskd, "Artist - Track A")[0].status == Status.OK
    assert run(s, state, slskd, "Artist - Track A")[0].status == Status.ALREADY
    slskd.catalog[("u2", r"y\Artist - Track A.flac")] = (audio_dir / "master_a.flac", "ok")
    r, _ = run(s, state, slskd, "Artist - Track A", upgrade=True)
    assert r.status == Status.OK and r.tier == "lossless"
    assert [p.suffix for p in s.hq.iterdir()] == [".flac"]   # the mp3 was replaced


@needs_ffmpeg
def test_low_quality_only(env, audio_dir):
    s, state = env
    slskd = FakeSlskd(s.downloads, {("u", r"x\Repair - V-Wreck.mp3"): (audio_dir / "t128.mp3", "ok")})
    slskd.search = lambda *a, **k: [{"username": "u", "hasFreeUploadSlot": True, "uploadSpeed": 1, "queueLength": 0,
                                     "files": [{"filename": r"x\Repair - V-Wreck.mp3", "size": 1, "length": 372,
                                                "bitRate": 192}]}]
    r, _ = run(s, state, slskd, "Repair - V-Wreck")
    assert r.status == Status.LOW_ONLY and not slskd.enqueued


@needs_ffmpeg
def test_lq_copy(env, audio_dir):
    s, state = env
    s.make_lq = True
    slskd = FakeSlskd(s.downloads, {("u", r"x\Artist - Track A.flac"): (audio_dir / "master_a.flac", "ok")})
    run(s, state, slskd, "Artist - Track A")
    lq = s.lq / "Artist - Track A.mp3"
    assert lq.exists() and audio.claimed_tier(lq) == audio.Tier.T320
