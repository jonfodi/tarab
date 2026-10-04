"""The local API: auth, job queue, events stream, wishlist."""
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from rasa_core import server
from rasa_core.fetcher import Result, Status


class FakeFetcher:
    def __init__(self, sink):
        self.sink = sink

    def fetch(self, q, opts):
        from rasa_core.events import Event
        self.sink(Event("start", str(q), str(q)))
        if "Rare" in q.title:
            return Result(str(q), Status.NOT_FOUND, note="no matching files")
        return Result(str(q), Status.OK, f"/HQ/{q}.flac", "lossless", "ok")


@pytest.fixture
def api(monkeypatch):
    app = server.App()
    app.fetcher = lambda sink: FakeFetcher(sink)
    app.ready = lambda: True
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, token=app.token):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield call, app, base
    httpd.shutdown()


def test_requires_token(api):
    call, _, _ = api
    assert call("GET", "/status", token="nope")[0] == 401
    status, body = call("GET", "/status")
    assert status == 200 and body["setup_done"] is False


def test_settings_hide_api_key(api):
    call, app, _ = api
    app.settings.slskd_api_key = "secret"
    assert "slskd_api_key" not in call("GET", "/settings")[1]
    assert call("PUT", "/settings", {"make_lq": True, "slskd_api_key": "x"})[1]["make_lq"] is True
    assert app.settings.slskd_api_key == "secret"


def test_get_queues_jobs_and_rare_ones_go_to_wishlist(api):
    call, app, _ = api
    status, body = call("POST", "/get", {"text": "TITLE - ARTIST\nElvism - Burger/Ink\nRare One - Repair | 6:51"})
    assert status == 200 and [j["query"] for j in body["jobs"]] == ["Burger/Ink - Elvism", "Repair - Rare One"]
    for _ in range(50):
        jobs = call("GET", "/jobs")[1]["jobs"]
        if all(j["status"] not in ("queued", "running") for j in jobs):
            break
        time.sleep(0.05)
    assert [j["status"] for j in jobs] == ["ok", "not_found"]
    assert [w["line"] for w in call("GET", "/wishlist")[1]["wishes"]] == ["Repair - Rare One | 6:51"]


def test_bad_input(api):
    call, _, _ = api
    assert call("POST", "/get", {"text": "no dash here"})[0] == 400
    assert call("GET", "/nope")[0] == 404


def test_events_stream(api):
    call, _, base = api
    got = []

    def listen():
        with urllib.request.urlopen(f"{base}/events?token={api[1].token}", timeout=5) as r:
            for line in r:
                if line.startswith(b"data:"):
                    got.append(json.loads(line[5:]))
                    if any(m.get("kind") == "event" for m in got):
                        return

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(0.3)
    call("POST", "/get", {"text": "Leod - Untitled 09"})
    t.join(5)
    assert any(m["kind"] == "event" and m["event"]["kind"] == "start" for m in got)
