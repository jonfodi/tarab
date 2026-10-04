"""Thin client for the slskd REST API (https://github.com/slskd/slskd)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import requests


class SlskdError(Exception):
    pass


@dataclass
class Transfer:
    id: str
    username: str
    filename: str
    state: str                  # e.g. "Queued, Remotely", "InProgress", "Completed, Succeeded"
    size: int
    bytes_transferred: int
    place_in_queue: int | None
    exception: str | None
    requested_at: str = ""

    @property
    def done(self) -> bool:
        return "Completed" in self.state

    @property
    def succeeded(self) -> bool:
        return "Succeeded" in self.state


def _walk(node):
    """Transfers nested anywhere in a response ({directories: [{files: [...]}]} differs between versions)."""
    if isinstance(node, dict):
        if "filename" in node and "state" in node:
            yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


class Slskd:
    def __init__(self, url: str, api_key: str):
        self.api = url.rstrip("/") + "/api/v0"
        self.http = requests.Session()
        self.http.headers["X-API-Key"] = api_key

    def _req(self, method: str, path: str, **kw):
        r = self.http.request(method, self.api + path, timeout=30, **kw)
        r.raise_for_status()
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else None

    # connection
    def server(self) -> dict:
        return self._req("GET", "/server") or {}

    def logged_in(self) -> bool:
        try:
            return bool(self.server().get("isLoggedIn"))
        except requests.RequestException:
            return False

    # search
    def search(self, text: str, timeout_s: int = 20, tries: int = 3) -> list[dict]:
        """Responses for `text`. slskd's searchTimeout is an *inactivity* timeout, so popular searches keep running
        and their responses are only readable once finished: at our deadline we stop the search so slskd keeps
        what it has. Empty results are retried, since searches sometimes die instantly (slskd DB race, throttling)."""
        for attempt in range(tries):
            if attempt:
                time.sleep(5 * attempt)
            sid = str(uuid.uuid4())
            self._req("POST", "/searches", json={"id": sid, "searchText": text, "searchTimeout": timeout_s * 1000,
                                                 "responseLimit": 300, "fileLimit": 5000})
            deadline, stopped = time.time() + timeout_s + 10, False
            while True:
                time.sleep(2)
                if (self._req("GET", f"/searches/{sid}") or {}).get("isComplete"):
                    break
                if time.time() > deadline and not stopped:
                    self._req("PUT", f"/searches/{sid}")
                    stopped, deadline = True, time.time() + 20
                elif stopped and time.time() > deadline:
                    break
            responses = self._req("GET", f"/searches/{sid}/responses") or []
            try:
                self._req("DELETE", f"/searches/{sid}")
            except requests.HTTPError:
                pass
            if responses:
                return responses
        return []

    # transfers
    def transfers(self, username: str) -> list[Transfer]:
        try:
            data = self._req("GET", f"/transfers/downloads/{quote(username, safe='')}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return []
            raise
        return [Transfer(t["id"], username, t["filename"], t["state"], t.get("size", 0),
                         t.get("bytesTransferred", 0), t.get("placeInQueue"), t.get("exception"),
                         t.get("requestedAt", ""))
                for t in _walk(data)]

    def transfer(self, username: str, filename: str) -> Transfer | None:
        mine = [t for t in self.transfers(username) if t.filename == filename]
        return max(mine, key=lambda t: t.requested_at) if mine else None

    def enqueue(self, username: str, filename: str, size: int) -> None:
        """Queue a download, unless the same file is already pending (watch mode re-tries the same peers)."""
        if any(t.filename == filename and not t.done for t in self.transfers(username)):
            return
        try:
            self._req("POST", f"/transfers/downloads/{quote(username, safe='')}",
                      json=[{"filename": filename, "size": size}])
        except requests.HTTPError as e:
            raise SlskdError(e.response.text[:200] if e.response is not None else str(e)) from e

    def cancel(self, t: Transfer) -> None:
        try:
            self._req("DELETE", f"/transfers/downloads/{quote(t.username, safe='')}/{t.id}", params={"remove": "true"})
        except requests.HTTPError:
            pass

    # sharing
    def rescan_shares(self) -> None:
        try:
            self._req("PUT", "/shares")
        except requests.HTTPError:
            pass
