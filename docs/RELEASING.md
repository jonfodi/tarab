# Building a release

Requirements: Apple Silicon Mac, Xcode command line tools, Rust, pnpm, uv.

```sh
scripts/build.sh
```

Produces `app/src-tauri/target/release/bundle/dmg/tarab_<version>_aarch64.dmg`. The script:

1. downloads pinned third-party binaries into `build/vendor/` (static ffmpeg/ffprobe, slskd release),
2. freezes the engine (`core/`) with PyInstaller into `build/engine/dist/tarab-engine/` and smoke-tests it in an
   empty environment,
3. ad-hoc signs every bundled executable (Apple Silicon won't run unsigned code),
4. runs `tauri build` (bundles `engine/`, `bin/` and `THIRD_PARTY.md` into `Contents/Resources`).

Bump the version in `app/src-tauri/tauri.conf.json`, `app/src-tauri/Cargo.toml`, `app/package.json` and
`core/pyproject.toml` + `core/src/tarab_core/__init__.py`.

## Signing and notarization (not set up)

Builds are ad-hoc signed, so first launch needs "Open Anyway" (see INSTALL.md). With an Apple Developer ID:
set `bundle.macOS.signingIdentity` to the Developer ID Application identity, sign `build/vendor` and the engine
with it and the hardened runtime (slskd is .NET and needs `com.apple.security.cs.allow-jit` and
`allow-unsigned-executable-memory` entitlements), and export `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` for
`tauri build` to notarize.

## Gotchas

- macOS 27's dyld rejects dylibs stripped by the current Rust toolchain ("mis-aligned LINKEDIT string pool"), so
  `strip = false` is set for release builds in `app/src-tauri/Cargo.toml`.
- The static ffmpeg has no trusted CA certificates. Never pass it URLs; the engine downloads reference audio with
  Python and hands ffmpeg local files (`identity._download`, covered by a test).
