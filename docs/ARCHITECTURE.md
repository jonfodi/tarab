# Architecture

rasa runs entirely on the user's Mac. Each user logs in with their own Soulseek account; there is no rasa server.

```
┌──────────────────────── rasa.app ────────────────────────┐
│  Tauri shell (Rust + web UI)                              │
│     │  local HTTP + server-sent events (127.0.0.1)        │
│  rasa-core (Python, bundled)  ── ffmpeg/ffprobe (bundled) │
│     │  REST (X-API-Key)                                   │
│  slskd (bundled .NET binary) ──── Soulseek network        │
└───────────────────────────────────────────────────────────┘
State: ~/Library/Application Support/rasa/{settings.json, rasa.db, slskd/}
Music: ~/Music/rasa/HQ (shared by default), ~/Music/rasa/LQ (optional MP3s)
```

## core/ modules

| module | job |
|---|---|
| `text` | normalisation, matching heuristics, `Query` parsing (`Artist - Title [CAT] \| m:ss`) |
| `ranking` | Soulseek responses → filtered, ranked `Candidate`s |
| `sources` | Deezer / MusicBrainz / Bandcamp: official lengths, BPM, audio, tracklists |
| `identity` | chroma fingerprint match vs official audio → ok / unsure / wrong / unknown |
| `audio` | ffmpeg helpers, transcode (lowpass cliff) detection, quality tiers |
| `slskd` | REST client: search, enqueue, transfers, shares |
| `fetcher` | one track end to end; emits `events` |
| `library` | naming, filing into HQ, LQ MP3 export |
| `state` | SQLite: library index, flaky peers, known-wrong files, wishlist, history |
| `cli` | `rasa get / ep / watch / lq / verify / import / config` |

Network services are wrapped so tests swap in fakes (`tests/test_fetcher.py` runs the full pipeline offline).

## Phase 2: native slskd

- Ship the official slskd osx-arm64 release (self-contained .NET) unmodified in `Contents/Resources/bin`.
- rasa writes `slskd.yml` (generated API key bound to 127.0.0.1, downloads/incomplete dirs, shares = HQ +
  extras, upload limits) and passes the Soulseek login via env vars, then starts/stops slskd as a child process.
- Login state and conflicts ("logged in elsewhere") surface in the UI.

## Phase 3: app

Screens: onboarding (Soulseek account, folders, sharing, "old CDJs?"), Get (paste list / Bandcamp link, live
progress), Listen (tracks whose identity couldn't be confirmed), Wishlist, Library, Settings.

## Licensing

- rasa: MIT proposed (open source, free).
- slskd is AGPL-3.0. Shipping it unmodified as a separate program alongside rasa is "mere aggregation";
  we include its license and a link to its source. Modifying slskd would require publishing those changes.
- ffmpeg: ship an LGPL build (no `--enable-gpl` components needed except libmp3lame, which is LGPL).

## Known issues

- When MusicBrainz lists many lengths for a title (compilation edits), "official length" stops discriminating
  versions (e.g. "On & On" picked a 6:15 edit over the 7:54 album version). Deezer lengths, which come with
  audio, should outrank MusicBrainz when they disagree.
- Bandcamp's search endpoint is unofficial and may change.
- Fetches run one track at a time.
