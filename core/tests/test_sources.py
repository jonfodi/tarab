from pathlib import Path

import yt_dlp

from tarab_core import sources
from tarab_core.text import Query, tokens

HITS = [
    {"duration": 252, "channel": "BRIGHTER DAYS FAMILY", "title": "P WAVEY - PARA (Official Video)", "url": "video"},
    {"duration": 252, "channel": "P Wavey - Topic", "title": "Para", "url": "topic"},
    {"duration": 8172, "channel": "Benzyme415", "title": "Para - 3h mix", "url": "mix"},
]


class FakeYDL:
    downloaded: list[str] = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def extract_info(self, query, download, process):
        return {"entries": self.hits}

    def download(self, urls):
        FakeYDL.downloaded += urls
        Path(self.opts["outtmpl"].replace("%(ext)s", "webm")).write_bytes(b"")


def same(title, artist):
    return tokens("para") <= tokens(title) and "wavey" in tokens(artist)


def test_youtube_prefers_topic_upload(monkeypatch):
    FakeYDL.hits, FakeYDL.downloaded = HITS, []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    path, dur, topic = sources._youtube(Query("P Wavey", "Para"), same)
    assert (FakeYDL.downloaded, dur, topic) == (["topic"], 252, True)
    path.unlink()
    path.parent.rmdir()


def test_youtube_falls_back_to_artist_title_upload(monkeypatch):
    FakeYDL.hits, FakeYDL.downloaded = [HITS[0], HITS[2]], []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    path, _, topic = sources._youtube(Query("P Wavey", "Para"), same)
    assert (FakeYDL.downloaded, topic) == (["video"], False)
    path.unlink()
    path.parent.rmdir()


def test_youtube_nothing_matching(monkeypatch):
    FakeYDL.hits = [HITS[2], {"duration": 200, "channel": "x", "title": "Someone - Other", "url": "o"}]
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    assert sources._youtube(Query("P Wavey", "Para"), same) is None
