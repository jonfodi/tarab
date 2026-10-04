"""Settings and app paths. Stored as JSON in ~/Library/Application Support/rasa/settings.json."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "rasa"


def app_dir() -> Path:
    d = Path(os.environ.get("RASA_HOME") or Path.home() / "Library" / "Application Support" / APP_NAME)
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_tool(name: str) -> str | None:
    """A binary shipped inside the .app (Contents/MacOS or Resources/bin), when running packaged."""
    roots = [Path(sys.executable).parent, Path(sys.executable).parent.parent / "Resources" / "bin"]
    if env := os.environ.get("RASA_BIN_DIR"):
        roots.insert(0, Path(env))
    for r in roots:
        if (p := r / name).is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


@dataclass
class Settings:
    # Where verified tracks go, named "Artist - Title.ext"
    hq_dir: str = str(Path.home() / "Music" / "rasa" / "HQ")
    # 320k MP3 copies for players without FLAC/AIFF support (old CDJs)
    lq_dir: str = str(Path.home() / "Music" / "rasa" / "LQ")
    make_lq: bool = False
    # Soulseek, via slskd (password lives in the macOS Keychain, not here)
    soulseek_username: str = ""
    slskd_url: str = "http://127.0.0.1:5130"   # not slskd's default 5030, so a separate slskd can coexist
    listen_port: int = 50300                   # incoming peer connections
    slskd_api_key: str = ""
    slskd_downloads: str = field(default_factory=lambda: str(app_dir() / "slskd" / "downloads"))
    # Sharing (the HQ folder by default)
    share_hq: bool = True
    share_extra: list[str] = field(default_factory=list)
    max_uploads: int = 2
    max_upload_kbps: int = 0          # 0 = unlimited
    # Fetching
    min_tier: int = 1                 # Tier.T256: nothing below 256k
    max_attempts: int = 4
    search_timeout: int = 20          # s
    queue_timeout: int = 90           # s in a peer's queue before trying the next source
    stall_timeout: int = 60           # s without progress
    max_eta: int = 300                # s: drop a slow transfer when an equally good source exists
    check_identity: bool = True       # Deezer / MusicBrainz / Bandcamp lookups

    @property
    def hq(self) -> Path:
        return Path(self.hq_dir).expanduser()

    @property
    def lq(self) -> Path:
        return Path(self.lq_dir).expanduser()

    @property
    def downloads(self) -> Path:
        return Path(self.slskd_downloads).expanduser()

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or app_dir() / "settings.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path | None = None) -> None:
        path = path or app_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2))
        path.chmod(0o600)   # holds the slskd API key
