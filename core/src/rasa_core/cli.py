"""rasa command line.

  rasa get "Artist - Title" "Artist - Title [CAT] | 6:51"
  rasa get -f tracks.txt                 # one per line; a "TITLE - ARTIST" header flips the order
  rasa ep "Artist - Release [CAT]"       # or a Bandcamp album link
  rasa watch add "Repair - Page-R | 6:51" ; rasa watch run
  rasa lq                                # 320k MP3 copies of HQ for old CDJs
  rasa verify file.flac ...              # fake-file + quality check
  rasa import ~/Music/HQ                 # index an existing "Artist - Title" folder
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import getpass

from . import audio, daemon, library, server, sources
from .audio import Tier
from .config import Settings
from .events import Event
from .fetcher import Fetcher, Options, Result, Status
from .identity import Verdict
from .slskd import Slskd
from .state import State
from .text import Query, parse_list


class Printer:
    """Events → terminal lines; progress updates overwrite one line."""

    def __init__(self, verbose: bool = True):
        self.verbose, self._progress = verbose, False

    def __call__(self, e: Event) -> None:
        if e.kind == "progress":
            print(f"\r    {e.message:<40}", end="", flush=True)
            self._progress = True
            return
        if self._progress:
            print("\r" + " " * 48 + "\r", end="")
            self._progress = False
        line = {"start": f"\n▶ {e.message}", "try": f"  ↓ {e.message}", "rejected": f"    ✗ {e.message}",
                "identity": f"    {'✓' if e.data.get('verdict') == 'ok' else '?'} identity: {e.message}",
                "quality": f"    {'✓' if e.data.get('claimed') == e.data.get('verified') else '⚠'} {e.message}",
                "parked": f"    ⏸ {e.message}", "done": None}.get(e.kind, f"  {e.kind}: {e.message}")
        if line and (self.verbose or e.kind in ("start", "try", "rejected", "parked")):
            print(line, flush=True)


MARK = {Status.OK: "✓", Status.ALREADY: "=", Status.QUEUED: "⏸", Status.DRY_RUN: "·",
        Status.LOW_ONLY: "✗", Status.NOT_FOUND: "✗", Status.FAILED: "✗"}


def summary(results: list[Result], buy_url: str | None = None) -> None:
    print("\n" + "─" * 70)
    for r in results:
        mark = "~" if r.needs_listen else MARK[r.status]
        where = r.path or r.note
        listen = "  (listen: identity not confirmed)" if r.needs_listen else ""
        print(f"{mark} {r.query}  →  {r.tier + '  ' if r.tier else ''}{where}{listen}")
        if r.status == Status.DRY_RUN:
            for c in r.candidates:
                print(f"     {c['tier']:8} {c['ext']:4} {c['kbps'] or '':>5} {c['username']}: {c['name']}")
    if buy_url and any(r.status in (Status.NOT_FOUND, Status.FAILED, Status.LOW_ONLY) for r in results):
        print(f"\nNot (all) on Soulseek right now. Buy it: {buy_url}")


def _engine(args) -> tuple[Settings, Fetcher]:
    s = Settings.load()
    if args.lq:
        s.make_lq = True
    slskd = Slskd(s.slskd_url, s.slskd_api_key)
    if not args.dry_run and not slskd.logged_in():
        sys.exit(f"slskd at {s.slskd_url} isn't reachable or not logged in to Soulseek.")
    return s, Fetcher(s, slskd, State(), Printer(not args.quiet))


def _opts(args) -> Options:
    return Options(dry_run=args.dry_run, upgrade=args.upgrade, allow_low=args.allow_low)


def cmd_get(args):
    queries = [Query.parse(t, args.title_first) for t in args.tracks]
    if args.file:
        queries += parse_list(Path(args.file).read_text(), args.title_first)
    if not queries:
        sys.exit("no tracks given")
    _, f = _engine(args)
    summary([f.fetch(q, _opts(args)) for q in queries])


def cmd_ep(args):
    if args.release.startswith("http"):
        rel = sources.bandcamp_release(args.release)
    else:
        q = Query.parse(args.release)
        rel = sources.find_release(q.artist, q.title, q.catalog)
    if not rel:
        sys.exit(f"no tracklist found for {args.release} (Bandcamp, Deezer, MusicBrainz)")
    print(f"{rel.artist} - {rel.title}: {len(rel.tracks)} tracks ({rel.source}: {rel.url})")
    for t in rel.tracks:
        print(f"  {t.length // 60}:{t.length % 60:02d}  {t.title}")
    _, f = _engine(args)
    summary([f.fetch(q, _opts(args)) for q in rel.queries()], buy_url=rel.url)


def cmd_watch(args):
    state = State()
    if args.action == "add":
        for line in args.tracks:
            q = Query.parse(line, args.title_first)
            state.wish(q.key, line)
            print(f"+ {q}")
        return
    if args.action == "remove":
        for line in args.tracks:
            state.unwish(Query.parse(line, args.title_first).key)
        return
    if args.action == "list":
        for w in state.wishes():
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(w["last_check"])) if w["last_check"] else "never"
            print(f"  {w['line']:50} last checked {last}: {w['status'] or '-'}")
        return
    # run: check every wish, forever (or once)
    _, f = _engine(args)
    while wishes := state.wishes():
        print(f"\n[{time.strftime('%Y-%m-%d %H:%M')}] checking {len(wishes)} wished track(s)", flush=True)
        for w in wishes:
            r = f.fetch(Query.parse(w["line"]), _opts(args))
            state.wish_checked(w["key"], r.status.value)
            if r.status in (Status.OK, Status.ALREADY):
                state.unwish(w["key"])
                print(f"  ✓ got {r.query}: {r.path}", flush=True)
        if args.once:
            break
        time.sleep(args.every * 60)


def cmd_lq(args):
    s = Settings.load()
    src = Path(args.source).expanduser() if args.source else s.hq
    files = sorted(p for p in src.iterdir() if p.suffix.lower().lstrip(".") in audio.AUDIO_EXT)
    for p in files:
        dest, what = library.export_lq(p, s.lq, args.force)
        print(f"  {'·' if what == 'up to date' else '✓'} {dest.name:60} {what}")
    print(f"\n{len(files)} tracks → {s.lq}")


def cmd_verify(args):
    for p in map(Path, args.files):
        claimed, tier, cliff = audio.check_quality(p)
        ok = "✓" if tier == claimed and tier > Tier.TOO_LOW else "✗"
        print(f"{ok} claims {claimed.label:8} → is {tier.label:8} "
              f"({'cutoff %.1fkHz' % (cliff / 1000) if cliff else 'no lowpass'})  {p.name}")


def cmd_import(args):
    """Index an existing folder of "Artist - Title.ext" files so they count as already owned."""
    s, state = Settings.load(), State()
    folder = Path(args.folder).expanduser() if args.folder else s.hq
    n = 0
    for p, tier in library.import_folder(folder, state, args.verify):
        n += 1
        print(f"  + {p.name} ({tier.label})")
    print(f"\nindexed {n} tracks from {folder}")


def cmd_config(args):
    s = Settings.load()
    if args.set:
        for kv in args.set:
            k, v = kv.split("=", 1)
            if k not in s.__dataclass_fields__:
                sys.exit(f"unknown setting {k}")
            cur = getattr(s, k)
            setattr(s, k, v.lower() in ("1", "true", "yes") if isinstance(cur, bool) else type(cur)(v)
                    if not isinstance(cur, list) else v.split(","))
        s.save()
    for k, v in vars(s).items():
        print(f"  {k} = {'***' if 'key' in k and v else v}")


def cmd_login(args):
    s = Settings.load()
    username = args.username or input("Soulseek username (a new name creates an account): ").strip()
    password = getpass.getpass(f"password for {username}: ")
    daemon.save_login(username, password)
    s.soulseek_username = username
    s.save()
    print(f"saved {username} (password in the macOS Keychain)")
    if daemon.status(s).running:
        daemon.stop()
        _start(s)


def _start(s: Settings) -> None:
    daemon.start(s)
    print("starting slskd…", flush=True)
    st = daemon.wait_ready(s)
    if st.logged_in:
        print(f"✓ logged in to Soulseek as {s.soulseek_username}")
    else:
        print(f"✗ not logged in ({st.error or st.state or 'slskd exited'}); log: {daemon.log_path()}")


def cmd_daemon(args):
    s = Settings.load()
    if args.action == "install":
        print(f"slskd: {daemon.install()}")
    elif args.action == "start":
        _start(s)
    elif args.action == "stop":
        print("stopped" if daemon.stop() else "not running")
    elif args.action == "restart":
        daemon.stop()
        _start(s)
    else:
        st = daemon.status(s)
        print(f"running: {st.running} (pid {st.pid})  logged in: {st.logged_in}  state: {st.state or '-'}"
              + (f"  error: {st.error}" if st.error else ""))


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="rasa", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def fetch_flags(p):
        p.add_argument("--dry-run", action="store_true", help="show ranked candidates, download nothing")
        p.add_argument("--lq", action="store_true", help="also make 320k MP3 copies (old CDJs)")
        p.add_argument("--upgrade", action="store_true", help="re-fetch owned tracks that aren't lossless yet")
        p.add_argument("--allow-low", action="store_true", help="accept below-256k copies if nothing better exists")
        p.add_argument("--title-first", action="store_true", help="input is 'Title - Artist'")
        p.add_argument("-q", "--quiet", action="store_true")

    g = sub.add_parser("get", help="fetch tracks")
    g.add_argument("tracks", nargs="*")
    g.add_argument("-f", "--file")
    fetch_flags(g)
    g.set_defaults(fn=cmd_get)

    e = sub.add_parser("ep", help='fetch a release: "Artist - Release [CAT]" or a Bandcamp link')
    e.add_argument("release")
    fetch_flags(e)
    e.set_defaults(fn=cmd_ep)

    w = sub.add_parser("watch", help="wishlist for rare tracks: add/remove/list/run")
    w.add_argument("action", choices=["add", "remove", "list", "run"])
    w.add_argument("tracks", nargs="*")
    w.add_argument("--every", type=int, default=20, help="minutes between checks (run)")
    w.add_argument("--once", action="store_true", help="check once and exit (run)")
    fetch_flags(w)
    w.set_defaults(fn=cmd_watch)

    l = sub.add_parser("lq", help="320k MP3 copies of a folder (default HQ) for old CDJs")
    l.add_argument("source", nargs="?")
    l.add_argument("--force", action="store_true")
    l.set_defaults(fn=cmd_lq)

    v = sub.add_parser("verify", help="fake-file / quality check")
    v.add_argument("files", nargs="+")
    v.set_defaults(fn=cmd_verify)

    i = sub.add_parser("import", help='index an existing "Artist - Title" folder')
    i.add_argument("folder", nargs="?")
    i.add_argument("--verify", action="store_true", help="run the quality check on each file (slow)")
    i.set_defaults(fn=cmd_import)

    c = sub.add_parser("config", help="show or change settings: rasa config --set hq_dir=~/Music/HQ")
    c.add_argument("--set", nargs="*")
    c.set_defaults(fn=cmd_config)

    sv = sub.add_parser("serve", help="run the local API for the desktop app")
    sv.add_argument("--port", type=int, default=0)
    sv.add_argument("--no-slskd", action="store_true", help="don't start slskd")
    sv.add_argument("--watch-stdin", action="store_true", help="exit when stdin closes (used by the app)")
    sv.set_defaults(fn=lambda a: server.serve(a.port, not a.no_slskd, a.watch_stdin))

    lg = sub.add_parser("login", help="set the Soulseek account (password stored in the macOS Keychain)")
    lg.add_argument("username", nargs="?")
    lg.set_defaults(fn=cmd_login)

    d = sub.add_parser("daemon", help="manage the bundled slskd: install/start/stop/restart/status")
    d.add_argument("action", choices=["install", "start", "stop", "restart", "status"])
    d.set_defaults(fn=cmd_daemon)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
