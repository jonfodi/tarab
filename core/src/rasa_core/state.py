"""Local state in SQLite: the library index, flaky peers, known-wrong files, the wishlist and history."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .config import app_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (          -- what's in the HQ folder, and how sure we are about it
    key        TEXT PRIMARY KEY,             -- Query.key: artist + title/version
    artist     TEXT NOT NULL,
    title      TEXT NOT NULL,
    path       TEXT NOT NULL,
    tier       INTEGER NOT NULL,             -- verified quality (audio.Tier)
    verdict    TEXT NOT NULL,                -- identity.Verdict
    note       TEXT,
    length     REAL,
    source     TEXT,                         -- soulseek user it came from
    added_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flaky_peers (     -- stalled, refused or too slow: tried last, never skipped
    username   TEXT PRIMARY KEY,
    strikes    INTEGER NOT NULL DEFAULT 1,
    reason     TEXT,
    last_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wrong_files (     -- failed the audio identity check; mislabeled rips circulate as
    size       INTEGER PRIMARY KEY,          -- byte-identical copies, so the size marks every copy of the fake
    username   TEXT, filename TEXT, note TEXT, added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wishlist (        -- keep looking until a good copy appears
    key        TEXT PRIMARY KEY,
    line       TEXT NOT NULL,                -- original "Artist - Title [CAT] | m:ss"
    added_at   REAL NOT NULL,
    last_check REAL,
    status     TEXT
);
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         REAL NOT NULL,
    query      TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT
);
"""


class State:
    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / "rasa.db"
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # library
    def track(self, key: str) -> sqlite3.Row | None:
        rows = self._exec("SELECT * FROM tracks WHERE key = ?", (key,))
        if rows and not Path(rows[0]["path"]).exists():   # deleted/moved outside the app
            self._exec("DELETE FROM tracks WHERE key = ?", (key,))
            return None
        return rows[0] if rows else None

    def put_track(self, key: str, artist: str, title: str, path: Path, tier: int, verdict: str, note: str,
                  length: float, source: str) -> None:
        self._exec("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (key, artist, title, str(path), int(tier), verdict, note, length, source, time.time()))

    def tracks(self) -> list[sqlite3.Row]:
        return self._exec("SELECT * FROM tracks ORDER BY added_at DESC")

    # peers
    def flaky(self) -> set[str]:
        return {r["username"] for r in self._exec("SELECT username FROM flaky_peers")}

    def strike(self, username: str, reason: str) -> None:
        self._exec("INSERT INTO flaky_peers VALUES (?,1,?,?) ON CONFLICT(username) DO UPDATE SET "
                   "strikes = strikes + 1, reason = excluded.reason, last_at = excluded.last_at",
                   (username, reason, time.time()))

    # fakes
    def wrong_sizes(self) -> set[int]:
        return {r["size"] for r in self._exec("SELECT size FROM wrong_files")}

    def mark_wrong(self, size: int, username: str, filename: str, note: str) -> None:
        self._exec("INSERT OR REPLACE INTO wrong_files VALUES (?,?,?,?,?)", (size, username, filename, note, time.time()))

    # wishlist
    def wish(self, key: str, line: str) -> None:
        self._exec("INSERT OR IGNORE INTO wishlist (key, line, added_at) VALUES (?,?,?)", (key, line, time.time()))

    def wishes(self) -> list[sqlite3.Row]:
        return self._exec("SELECT * FROM wishlist ORDER BY added_at")

    def wish_checked(self, key: str, status: str) -> None:
        self._exec("UPDATE wishlist SET last_check = ?, status = ? WHERE key = ?", (time.time(), status, key))

    def unwish(self, key: str) -> None:
        self._exec("DELETE FROM wishlist WHERE key = ?", (key,))

    # history
    def log(self, query: str, status: str, detail: str = "") -> None:
        self._exec("INSERT INTO history (at, query, status, detail) VALUES (?,?,?,?)", (time.time(), query, status, detail))
