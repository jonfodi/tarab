# tarab

Find, verify and file DJ-quality tracks from Soulseek. Paste a track list or a Bandcamp link; tarab finds the best
copy on the network, checks it's real lossless (not a transcode) and the right track (not a mislabeled rip or a
different edit), and files it as `Artist - Title` in your library. Optional 320k MP3 copies for old CDJs.

Free and open source. macOS.

## Status

- **Phase 1 (done):** `core/`: the engine as a Python package with tests and a CLI.
- **Phase 2 (done):** slskd runs natively (no Docker), managed by tarab; login in the macOS Keychain.
- **Phase 3 (working, unstyled):** Tauri desktop app driving the engine over a local API.
- **Phase 4 (done for beta):** self-contained `tarab.app` + DMG (ad-hoc signed). Not yet: Developer ID signing/notarization, auto-update, Intel build.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/INSTALL.md](docs/INSTALL.md) (for users) and
[docs/RELEASING.md](docs/RELEASING.md).

## App (development)

Needs Rust, pnpm, uv and ffmpeg.

```sh
cd app && pnpm install && pnpm tauri dev
```

The app starts the engine (`uv run tarab serve` in `core/`), which installs and runs slskd. First launch asks for
your Soulseek login, music folder and sharing preference.

## Core (CLI)

Needs `ffmpeg` on PATH. tarab installs and runs [slskd](https://github.com/slskd/slskd) itself.

```sh
cd core
uv sync
uv run tarab daemon install              # official slskd release for this Mac
uv run tarab login                       # Soulseek account; password goes in the Keychain
uv run tarab daemon start
uv run tarab get "Leod - Untitled 09" "Repair - Page-R | 6:51" "Roger Gerressen - Untitled 1 [SUSH31]"
uv run tarab get -f tracks.txt           # one per line; a "TITLE - ARTIST" header flips the order
uv run tarab ep https://label.bandcamp.com/album/some-ep
uv run tarab watch add "Repair - Page-R | 6:51" && uv run tarab watch run
uv run tarab lq                          # 320k MP3 copies for old CDJs
uv run tarab verify file.flac            # transcode check
uv run pytest
```

Line syntax: `Artist - Title [CATALOG] | m:ss`. Catalog and length are optional; a length pins the exact
version.

## How it decides

1. **Reference:** official lengths, BPM and audio (Deezer previews, Bandcamp streams, MusicBrainz lengths).
2. **Match:** title words in the filename, artist words in the path; remixes/edits/dubs only if asked for.
3. **Rank:** catalog match › official length › the length most copies agree on › quality (lossless › 320/V0 ›
   256) › reliable peer › free slot › speed.
4. **Download:** gives up on queues/stalls/slow transfers when another good source exists; parks the transfer
   when it's the only source (a later run collects it).
5. **Verify:** chroma match against official audio (wrong track → rejected, its size remembered); spectral
   check for encoder lowpass cliffs (a "FLAC" with a 20 kHz wall is really a 320).

## License

MIT (proposed; see docs/ARCHITECTURE.md › Licensing).
