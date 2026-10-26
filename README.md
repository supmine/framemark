# Sony Exif Frame

Local desktop app (macOS) that adds a Cameramark-style caption frame to Sony photos: ISO · ƒ · shutter, date, a sharp SONY logo, camera model and lens, all read from EXIF.

- Layouts: settings | logo, logo | settings, centred
- Shapes: original, 1:1, 4:5 · six frame colours · border / text size
- Optional focal length, 35mm equivalent (APS-C), α model names
- Full-resolution output (JPEG 95/100, PNG), original EXIF and colour profile kept
- Batch: drop many photos or a folder

## Download

Binaries for macOS (Apple Silicon), Windows and Linux are on the Releases page. They are built by GitHub Actions whenever a `v*` tag is pushed:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

- **macOS**: unzip, move `Framemark.app` to Applications. The app is unsigned, so the first time right-click → Open (or run `xattr -dr com.apple.quarantine /Applications/Framemark.app`).
- **Windows**: run `Framemark-windows-x64.exe`. SmartScreen may warn about an unknown publisher → More info → Run anyway.
- **Linux**: `tar -xzf Framemark-linux-x64.tar.gz && ./Framemark`. Needs the usual Qt/X11 libraries (on Ubuntu: `sudo apt install libxcb-cursor0 libegl1 libxkbcommon-x11-0`).

The binary also has a headless mode: `Framemark render photo.jpg --shape 4:5 --out framed`.

## Build a binary locally

```bash
.venv/bin/pip install pyinstaller==6.22.3
.venv/bin/pyinstaller --noconfirm framemark.spec   # -> dist/
```

## Run from source

Double-click `run.command`, or:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Command line, without the UI:

```bash
.venv/bin/python render.py photos/*.jpg --shape 4:5 --layout pro --out framed
```

Text uses Helvetica Neue on macOS and the bundled Inter font (SIL Open Font License, `assets/fonts/`) on Windows and Linux. The SONY logo in `assets/` is a trademark of Sony Group Corporation, included for personal use only.
