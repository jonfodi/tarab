"""Run slskd as a managed child process (no Docker): install, configure, start, stop, watch login state.

slskd ships unmodified (AGPL-3.0, https://github.com/slskd/slskd). tarab only writes its config file and passes
the Soulseek login through environment variables.
"""
from __future__ import annotations

import os
import platform
import secrets
import shutil
import signal
import stat
import subprocess
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Settings, app_dir, bundled_tool
from .slskd import Slskd

SLSKD_VERSION = "0.26.0"
KEYCHAIN_SERVICE = "tarab-soulseek"


# ---------------------------------------------------------------- credentials (macOS Keychain)

def save_login(username: str, password: str) -> None:
    subprocess.run(["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", username, "-w", password],
                   check=True, capture_output=True)


def load_password(username: str) -> str | None:
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", username, "-w"],
                       capture_output=True, text=True)
    return r.stdout.rstrip("\n") if r.returncode == 0 else None


def forget_login(username: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", username], capture_output=True)


# ---------------------------------------------------------------- binary

def slskd_dir() -> Path:
    d = app_dir() / "slskd"
    d.mkdir(parents=True, exist_ok=True)
    return d


def binary() -> Path | None:
    """The bundled slskd when running as the app, else the one `install()` downloaded."""
    if b := bundled_tool("slskd/slskd") or bundled_tool("slskd"):
        return Path(b)
    p = app_dir() / "bin" / f"slskd-{SLSKD_VERSION}" / "slskd"
    return p if p.exists() else None


def install(progress=print) -> Path:
    """Download the official slskd release for this Mac (development; the packaged app bundles it)."""
    if b := binary():
        return b
    arch = "arm64" if platform.machine() == "arm64" else "x64"
    name = f"slskd-{SLSKD_VERSION}-osx-{arch}.zip"
    url = f"https://github.com/slskd/slskd/releases/download/{SLSKD_VERSION}/{name}"
    dest = app_dir() / "bin" / f"slskd-{SLSKD_VERSION}"
    dest.mkdir(parents=True, exist_ok=True)
    zpath = dest.parent / name
    progress(f"downloading {url}")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(zpath, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(dest)
    zpath.unlink()
    exe = dest / "slskd"
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


# ---------------------------------------------------------------- config

def _yaml_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def write_config(s: Settings) -> Path:
    """slskd.yml from tarab settings. Called on every start, so settings changes apply on restart."""
    if not s.slskd_api_key:
        s.slskd_api_key = secrets.token_urlsafe(32)
        s.save()
    shares = []
    if s.share_hq:
        s.hq.mkdir(parents=True, exist_ok=True)
        shares.append(f"[tarab]{s.hq}")          # alias hides the local path (it contains your Mac username)
    for i, extra in enumerate(s.share_extra, 1):
        shares.append(f"[shared{i}]{Path(extra).expanduser()}")
    web_user, web_pass = "tarab", secrets.token_urlsafe(16)   # slskd's own web UI: not used, locked down
    port = int(s.slskd_url.rsplit(":", 1)[1].split("/")[0])
    lines = [
        "# Written by tarab on every start; edit settings in tarab instead.",
        "remote_configuration: false",
        "directories:",
        f"  downloads: {_yaml_str(str(s.downloads))}",
        f"  incomplete: {_yaml_str(str(slskd_dir() / 'incomplete'))}",
        "shares:",
        "  directories:" + ("" if shares else " []"),
        *[f"    - {_yaml_str(d)}" for d in shares],
        "transfers:",
        "  upload:",
        f"    slots: {max(1, s.max_uploads)}",
        *([f"    speed_limit: {s.max_upload_kbps}"] if s.max_upload_kbps else []),
        "soulseek:",
        f"  listen_port: {s.listen_port}",
        "web:",
        f"  port: {port}",
        "  ip_address: 127.0.0.1",
        "  https:",
        "    disabled: true",
        "  authentication:",
        f"    username: {web_user}",
        f"    password: {_yaml_str(web_pass)}",
        "    api_keys:",
        "      tarab:",
        f"        key: {_yaml_str(s.slskd_api_key)}",
        "        role: readwrite",
        "        cidr: 127.0.0.1/32,::1/128",
    ]
    path = slskd_dir() / "slskd.yml"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


# ---------------------------------------------------------------- process

@dataclass
class Status:
    running: bool
    pid: int | None
    connected: bool = False
    logged_in: bool = False
    state: str = ""
    error: str = ""


def _pidfile() -> Path:
    return slskd_dir() / "slskd.pid"


def _pid() -> int | None:
    try:
        pid = int(_pidfile().read_text())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def log_path() -> Path:
    return slskd_dir() / "slskd.log"


def start(s: Settings, username: str | None = None, password: str | None = None) -> int:
    """Start slskd (if not running) logged in as `username` (password from the Keychain if not given)."""
    if pid := _pid():
        return pid
    username = username or s.soulseek_username
    if not username:
        raise RuntimeError("no Soulseek account set up: run `tarab login`")
    password = password or load_password(username)
    if not password:
        raise RuntimeError(f"no password for {username} in the Keychain: run `tarab login`")
    exe = binary()
    if not exe:
        raise RuntimeError("slskd isn't installed: run `tarab daemon install`")
    write_config(s)
    env = {**os.environ, "SLSKD_SLSK_USERNAME": username, "SLSKD_SLSK_PASSWORD": password, "SLSKD_NO_LOGO": "true"}
    log = open(log_path(), "ab")
    proc = subprocess.Popen([str(exe), "--app-dir", str(slskd_dir())], env=env, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True, cwd=exe.parent)
    _pidfile().write_text(str(proc.pid))
    return proc.pid


def stop(timeout: float = 15) -> bool:
    pid = _pid()
    if not pid:
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline and _pid():
        time.sleep(0.3)
    if _pid():
        os.kill(pid, signal.SIGKILL)
    _pidfile().unlink(missing_ok=True)
    return True


def _login_error() -> str:
    """The last Soulseek login failure in slskd's log, in plain words."""
    try:
        tail = log_path().read_text(errors="ignore")[-20000:]
    except FileNotFoundError:
        return ""
    for needle, msg in (("INVALIDPASS", "wrong Soulseek password for that username"),
                        ("INVALIDUSERNAME", "that Soulseek username isn't allowed"),
                        ("Kicked from server", "logged in somewhere else with the same account (close SoulseekQt/"
                                               "Nicotine+ or use another account)"),
                        ("address already in use", "a port slskd needs is in use (is another slskd running?)")):
        if needle.lower() in tail.lower().rsplit("Logged in", 1)[-1].lower():
            return msg
    return ""


def status(s: Settings) -> Status:
    pid = _pid()
    st = Status(running=bool(pid), pid=pid)
    if not pid:
        return st
    try:
        srv = Slskd(s.slskd_url, s.slskd_api_key).server()
        st.connected, st.logged_in, st.state = bool(srv.get("isConnected")), bool(srv.get("isLoggedIn")), srv.get("state", "")
    except requests.RequestException:
        st.state = "starting"
    if not st.logged_in:
        st.error = _login_error()
    return st


def wait_ready(s: Settings, timeout: float = 90) -> Status:
    deadline = time.time() + timeout
    st = status(s)
    while time.time() < deadline:
        st = status(s)
        if st.logged_in or st.error or not st.running:
            return st
        time.sleep(1.5)
    return st
