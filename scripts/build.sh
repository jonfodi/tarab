#!/usr/bin/env bash
# Build tarab.app + DMG for Apple Silicon. Usage: scripts/build.sh
# Downloads pinned third-party binaries into build/vendor, freezes the Python engine with PyInstaller,
# ad-hoc signs every executable, then runs `tauri build`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
VENDOR="$BUILD/vendor"
SLSKD_VERSION="0.26.0"
PY="${TARAB_BUILD_PYTHON:-3.13}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

[ "$(uname -m)" = "arm64" ] || { echo "builds for Apple Silicon only (for now)"; exit 1; }
for tool in uv pnpm cargo codesign; do command -v "$tool" >/dev/null || { echo "missing: $tool"; exit 1; }; done
mkdir -p "$VENDOR"

say "ffmpeg / ffprobe (static, arm64)"
for t in ffmpeg ffprobe; do
  if [ ! -x "$VENDOR/$t" ]; then
    curl -fsSL -o "$BUILD/$t.zip" "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/$t.zip"
    unzip -o -q "$BUILD/$t.zip" -d "$VENDOR" && rm "$BUILD/$t.zip"
  fi
  # must only depend on system frameworks, or it won't run on other Macs
  if otool -L "$VENDOR/$t" | tail -n +2 | grep -vqE '^\s+/(System|usr/lib)/'; then echo "$t isn't static"; exit 1; fi
done
"$VENDOR/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -q libmp3lame || { echo "ffmpeg lacks libmp3lame"; exit 1; }

say "slskd $SLSKD_VERSION"
if [ ! -x "$VENDOR/slskd/slskd" ]; then
  curl -fsSL -o "$BUILD/slskd.zip" "https://github.com/slskd/slskd/releases/download/$SLSKD_VERSION/slskd-$SLSKD_VERSION-osx-arm64.zip"
  rm -rf "$VENDOR/slskd" && mkdir -p "$VENDOR/slskd" && unzip -o -q "$BUILD/slskd.zip" -d "$VENDOR/slskd" && rm "$BUILD/slskd.zip"
  chmod +x "$VENDOR/slskd/slskd"
fi

say "engine (PyInstaller, Python $PY)"
rm -rf "$BUILD/engine"
uv venv -q --python "$PY" "$BUILD/engine/venv"
uv pip install -q --python "$BUILD/engine/venv/bin/python" "$ROOT/core" pyinstaller
(cd "$BUILD/engine" && venv/bin/pyinstaller --noconfirm --clean --log-level WARN --onedir --name tarab-engine \
  --target-arch arm64 --distpath dist --workpath work --specpath . "$ROOT/core/packaging/engine.py")
# smoke test in an empty environment: must not need Homebrew, uv or a system Python
TMPHOME="$(mktemp -d)"
env -i HOME="$TMPHOME" TARAB_HOME="$TMPHOME/tarab" TARAB_BIN_DIR="$VENDOR" PATH=/usr/bin:/bin \
  "$BUILD/engine/dist/tarab-engine/tarab-engine" config >/dev/null || { echo "engine smoke test failed"; exit 1; }
rm -rf "$TMPHOME"

say "ad-hoc signing bundled executables"
# Apple Silicon refuses to run unsigned code. Without a Developer ID we sign ad hoc ("-").
find "$VENDOR" "$BUILD/engine/dist" -type f \( -perm -u+x -o -name "*.dylib" -o -name "*.so" \) -print0 |
  while IFS= read -r -d '' f; do
    file -b "$f" | grep -q "Mach-O" && codesign --force --sign - "$f" 2>/dev/null
  done

say "app"
cd "$ROOT/app"
pnpm install --silent
pnpm tauri build 2>&1 | grep -vE "^\s+(Compiling|Building)" | tail -15

APP="$ROOT/app/src-tauri/target/release/bundle/macos/tarab.app"
DMG="$(ls -t "$ROOT"/app/src-tauri/target/release/bundle/dmg/*.dmg 2>/dev/null | head -1)"
codesign --verify --deep "$APP" && echo "signature ok (ad hoc)"
say "done"
du -sh "$APP" ${DMG:+"$DMG"}
