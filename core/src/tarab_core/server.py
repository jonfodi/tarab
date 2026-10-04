"""Local HTTP API for the desktop app.

Binds 127.0.0.1 on a free port and requires a random bearer token. On start it prints one JSON line
{"port": ..., "token": ...} to stdout, which the Tauri shell reads. Live progress is streamed as server-sent events
on GET /events.

Stdlib only (http.server), so the bundled engine stays small.
"""
from __future__ import annotations

import itertools
import json
import queue
import secrets
import signal
import sys
import threading
import time
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, audio, daemon, library, sources
from .config import Settings
from .events import Event
from .fetcher import Fetcher, Options, Status
from .slskd import Slskd
from .state import State
from .text import Query, parse_list


class Hub:
    """Fan-out of events to every connected /events stream."""

    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, data: dict) -> None:
        msg = {"kind": kind, **data, "at": time.time()}
        with self._lock:
            for q in self._subs:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


class Jobs:
    """Queue of tracks to fetch, worked through by one background thread."""

    def __init__(self, app: App):
        self.app = app
        self.items: list[dict] = []
        self._ids = itertools.count(1)
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True, name="jobs").start()

    def add(self, queries: list[Query], opts: Options, group: str | None = None, buy_url: str | None = None) -> list[dict]:
        added = []
        with self._lock:
            for q in queries:
                item = {"id": next(self._ids), "query": str(q), "status": "queued", "message": "", "group": group,
                        "buy_url": buy_url, "result": None, "added_at": time.time()}
                self.items.append(item)
                self._q.put((item, q, opts))
                added.append(item)
        for item in added:
            self.app.hub.publish("job", item)
        return added

    def clear_finished(self) -> None:
        with self._lock:
            self.items = [i for i in self.items if i["status"] in ("queued", "running")]

    def _update(self, item: dict, **kw) -> None:
        item.update(kw)
        self.app.hub.publish("job", item)

    def _worker(self) -> None:
        while True:
            item, q, opts = self._q.get()
            if not self.app.ready():
                self._update(item, message="waiting for the Soulseek connection")
                while not self.app.ready():
                    time.sleep(3)
            self._update(item, status="running", message="starting")

            def sink(e: Event, item=item):
                self.app.hub.publish("event", {"job": item["id"], "event": e.as_dict()})
                if e.kind not in ("done",):
                    item["message"] = e.message

            try:
                f = self.app.fetcher(sink)
                r = f.fetch(q, opts)
                if r.status in (Status.QUEUED, Status.LOW_ONLY, Status.NOT_FOUND) and opts is not None:
                    self.app.state.wish(q.key, _line(q))   # rare: keep looking automatically
                self._update(item, status=r.status.value, message=r.note or (r.path or ""), result=r.as_dict())
            except Exception as e:   # one bad track must not kill the queue
                traceback.print_exc(file=sys.stderr)
                self._update(item, status="error", message=str(e))


def _line(q: Query) -> str:
    line = f"{q.artist} - {q.title}"
    if q.catalog:
        line += f" [{q.catalog}]"
    if q.length:
        line += f" | {q.length // 60}:{q.length % 60:02d}"
    return line


class App:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.state = State()
        self.hub = Hub()
        self.jobs = Jobs(self)
        self.token = secrets.token_urlsafe(24)
        self._wish_stop = threading.Event()
        self.slskd_paused = False   # the user stopped slskd on purpose: don't revive it
        threading.Thread(target=self._wishlist_loop, daemon=True, name="wishlist").start()

    def supervise(self, every: float = 5) -> None:
        """Keep slskd running while the app is open: covers crashes and a previous instance still shutting
        down when we started (quit + quick relaunch)."""
        def loop():
            while not self._wish_stop.wait(every):
                if self.slskd_paused or not self.settings.soulseek_username or not daemon.binary():
                    continue
                if not daemon.status(self.settings).running:
                    try:
                        daemon.start(self.settings)
                        self.hub.publish("slskd", {"message": "started slskd"})
                    except RuntimeError as e:
                        self.hub.publish("slskd", {"message": str(e)})
        threading.Thread(target=loop, daemon=True, name="supervisor").start()

    def ready(self) -> bool:
        return self.slskd().logged_in()

    def slskd(self) -> Slskd:
        return Slskd(self.settings.slskd_url, self.settings.slskd_api_key)

    def fetcher(self, sink) -> Fetcher:
        return Fetcher(self.settings, self.slskd(), self.state, sink)

    def status(self) -> dict:
        st = daemon.status(self.settings)
        return {"version": __version__, "setup_done": bool(self.settings.soulseek_username),
                "username": self.settings.soulseek_username, "slskd_installed": daemon.binary() is not None,
                "slskd": asdict(st), "queue": sum(1 for i in self.jobs.items if i["status"] in ("queued", "running"))}

    def _wishlist_loop(self, every: int = 20 * 60) -> None:
        """Re-check the wishlist every 20 minutes while the app runs."""
        while not self._wish_stop.wait(60):
            due = [w for w in self.state.wishes() if not w["last_check"] or time.time() - w["last_check"] > every]
            busy = {i["query"] for i in self.jobs.items if i["status"] in ("queued", "running")}
            if not due or not daemon.status(self.settings).logged_in:
                continue
            for w in due:
                q = Query.parse(w["line"])
                if str(q) not in busy:
                    self.state.wish_checked(w["key"], "checking")
                    self.jobs.add([q], Options(), group="wishlist")


# ---------------------------------------------------------------- HTTP

ROUTES: dict[tuple[str, str], callable] = {}


def route(method: str, path: str):
    def deco(fn):
        ROUTES[(method, path)] = fn
        return fn
    return deco


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@route("GET", "/status")
def get_status(app: App, body: dict) -> dict:
    return app.status()


@route("GET", "/settings")
def get_settings(app: App, body: dict) -> dict:
    d = asdict(app.settings)
    d.pop("slskd_api_key", None)
    return d


@route("PUT", "/settings")
def put_settings(app: App, body: dict) -> dict:
    restart_needed = False
    for k, v in body.items():
        if k == "slskd_api_key" or k not in app.settings.__dataclass_fields__:
            continue
        if k in ("share_hq", "share_extra", "max_uploads", "max_upload_kbps", "listen_port", "hq_dir"):
            restart_needed = restart_needed or getattr(app.settings, k) != v
        setattr(app.settings, k, v)
    app.settings.save()
    if restart_needed and daemon.status(app.settings).running:
        daemon.stop()
        daemon.start(app.settings)
    return get_settings(app, {})


@route("POST", "/setup")
def post_setup(app: App, body: dict) -> dict:
    """First run: Soulseek account (+ password → Keychain), folders, sharing; installs and starts slskd."""
    username, password = (body.get("username") or "").strip(), body.get("password") or ""
    if not username or not password:
        raise HTTPError(400, "username and password are required")
    for k in ("hq_dir", "lq_dir", "make_lq", "share_hq"):
        if k in body:
            setattr(app.settings, k, body[k])
    daemon.save_login(username, password)
    app.settings.soulseek_username = username
    app.settings.save()
    daemon.install(progress=lambda m: app.hub.publish("setup", {"message": m}))
    daemon.stop()
    daemon.start(app.settings)
    st = daemon.wait_ready(app.settings, timeout=60)
    return {"logged_in": st.logged_in, "error": st.error, "state": st.state}


@route("POST", "/daemon/start")
def post_daemon_start(app: App, body: dict) -> dict:
    app.slskd_paused = False
    daemon.start(app.settings)
    st = daemon.wait_ready(app.settings, timeout=60)
    return asdict(st)


@route("POST", "/daemon/stop")
def post_daemon_stop(app: App, body: dict) -> dict:
    app.slskd_paused = True
    daemon.stop()
    return asdict(daemon.status(app.settings))


@route("POST", "/get")
def post_get(app: App, body: dict) -> dict:
    """Body: {"text": "Artist - Title\\n..."} or {"url": "https://x.bandcamp.com/album/..."} or
    {"release": "Artist - Release [CAT]"}; options: title_first, upgrade, allow_low, make_lq."""
    opts = Options(upgrade=bool(body.get("upgrade")), allow_low=bool(body.get("allow_low")),
                   make_lq=body.get("make_lq"))
    if url := body.get("url"):
        rel = sources.bandcamp_release(url)
        if not rel:
            raise HTTPError(422, "couldn't read a tracklist from that link")
        return {"release": _rel(rel), "jobs": app.jobs.add(rel.queries(), opts, f"{rel.artist} - {rel.title}", rel.url)}
    if release := body.get("release"):
        q = Query.parse(release)
        rel = sources.find_release(q.artist, q.title, q.catalog)
        if not rel:
            raise HTTPError(404, f"no tracklist found for {release} on Bandcamp, Deezer or MusicBrainz")
        return {"release": _rel(rel), "jobs": app.jobs.add(rel.queries(), opts, f"{rel.artist} - {rel.title}", rel.url)}
    try:
        queries = parse_list(body.get("text", ""), bool(body.get("title_first")))
    except ValueError as e:
        raise HTTPError(400, str(e))
    if not queries:
        raise HTTPError(400, "no tracks given")
    return {"jobs": app.jobs.add(queries, opts)}


def _rel(rel) -> dict:
    return {"artist": rel.artist, "title": rel.title, "source": rel.source, "url": rel.url,
            "tracks": [{"title": t.title, "length": t.length} for t in rel.tracks]}


@route("GET", "/jobs")
def get_jobs(app: App, body: dict) -> dict:
    return {"jobs": app.jobs.items}


@route("POST", "/jobs/clear")
def post_jobs_clear(app: App, body: dict) -> dict:
    app.jobs.clear_finished()
    return {"jobs": app.jobs.items}


@route("GET", "/library")
def get_library(app: App, body: dict) -> dict:
    return {"tracks": [dict(r) for r in app.state.tracks()]}


@route("POST", "/library/verdict")
def post_verdict(app: App, body: dict) -> dict:
    """The user listened: {"key": ..., "verdict": "ok" | "wrong"}. Wrong → delete the file and look again."""
    row = app.state.track(body["key"])
    if not row:
        raise HTTPError(404, "not in library")
    if body["verdict"] == "ok":
        app.state.put_track(row["key"], row["artist"], row["title"], Path(row["path"]), row["tier"], "ok",
                            "confirmed by ear", row["length"], row["source"])
    else:
        Path(row["path"]).unlink(missing_ok=True)
        app.state._exec("DELETE FROM tracks WHERE key = ?", (row["key"],))
        app.jobs.add([Query(row["artist"], row["title"])], Options(), group="re-fetch")
    return {"ok": True}


@route("POST", "/library/import")
def post_import(app: App, body: dict) -> dict:
    folder = Path(body.get("folder") or app.settings.hq).expanduser()
    if not folder.is_dir():
        raise HTTPError(400, f"not a folder: {folder}")
    n = sum(1 for _ in library.import_folder(folder, app.state))
    return {"imported": n, "folder": str(folder)}


@route("POST", "/lq")
def post_lq(app: App, body: dict) -> dict:
    out = []
    for p in sorted(app.settings.hq.glob("*")):
        if p.suffix.lower().lstrip(".") in audio.AUDIO_EXT:
            dest, note = library.export_lq(p, app.settings.lq)
            out.append({"file": dest.name, "note": note})
    return {"files": out}


@route("GET", "/wishlist")
def get_wishlist(app: App, body: dict) -> dict:
    return {"wishes": [dict(w) for w in app.state.wishes()]}


@route("POST", "/wishlist")
def post_wishlist(app: App, body: dict) -> dict:
    for q in parse_list(body.get("text", "")):
        app.state.wish(q.key, _line(q))
    return get_wishlist(app, {})


@route("DELETE", "/wishlist")
def delete_wishlist(app: App, body: dict) -> dict:
    app.state.unwish(body["key"])
    return get_wishlist(app, {})


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):   # quiet
            pass

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

        def _send(self, status: int, data: dict):
            raw = json.dumps(data, default=str).encode()
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _authed(self) -> bool:
            # EventSource can't set headers, so /events also accepts ?token=
            path = urlparse(self.path)
            given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if not given and path.query.startswith("token="):
                given = path.query.split("=", 1)[1]
            return secrets.compare_digest(given, app.token)

        def _handle(self, method: str):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            path = urlparse(self.path).path.rstrip("/") or "/"
            if method == "GET" and path == "/events":
                return self._events()
            fn = ROUTES.get((method, path))
            if not fn:
                return self._send(404, {"error": f"no route {method} {path}"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                self._send(200, fn(app, body))
            except HTTPError as e:
                self._send(e.status, {"error": str(e)})
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                self._send(500, {"error": str(e)})

        def _events(self):
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            sub = app.hub.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = sub.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(msg, default=str)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                app.hub.unsubscribe(sub)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

    return Handler


def serve(port: int = 0, start_slskd: bool = True, watch_stdin: bool = False) -> None:
    app = App()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    if start_slskd and app.settings.soulseek_username and daemon.binary():
        try:
            daemon.start(app.settings)
        except RuntimeError as e:
            print(f"slskd not started: {e}", file=sys.stderr)
    if start_slskd:
        app.supervise()
    print(json.dumps({"port": httpd.server_address[1], "token": app.token}), flush=True)

    def shutdown(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    if watch_stdin:   # the app holds our stdin open; if it dies without telling us, stdin closes and we exit
        threading.Thread(target=lambda: (sys.stdin.read(), shutdown()), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop()
