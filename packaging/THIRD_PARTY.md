# Third-party software bundled with rasa

rasa (MIT) ships these programs unmodified, as separate executables inside `rasa.app/Contents/Resources/bin/`.

## slskd

- Version 0.26.0, https://github.com/slskd/slskd
- License: GNU Affero General Public License v3.0 (with additional terms), https://github.com/slskd/slskd/blob/master/LICENSE
- Source: https://github.com/slskd/slskd/tree/0.26.0
- Used unmodified; rasa only writes its configuration file and talks to its HTTP API.

## FFmpeg / FFprobe

- Static macOS arm64 build from https://ffmpeg.martin-riedl.de (FFmpeg 9.0.2)
- License: GNU General Public License v3 (build configured with `--enable-gpl --enable-version3`, includes libmp3lame)
- Source: https://ffmpeg.org/download.html and the build scripts at https://ffmpeg.martin-riedl.de
- Used unmodified as separate programs.

## Python runtime and libraries (inside `Resources/engine/`)

- CPython 3.13 (PSF License), NumPy (BSD-3-Clause), Requests (Apache-2.0), urllib3 (MIT), certifi (MPL-2.0),
  idna (BSD-3-Clause), charset-normalizer (MIT), packaged with PyInstaller (GPL with bootloader exception).
