# Sony Exif Frame

Local desktop app (macOS) that adds a Cameramark-style caption frame to Sony photos: ISO · ƒ · shutter, date, a sharp SONY logo, camera model and lens, all read from EXIF.

- Layouts: settings | logo, logo | settings, centred
- Shapes: original, 1:1, 4:5 · six frame colours · border / text size
- Optional focal length, 35mm equivalent (APS-C), α model names
- Full-resolution output (JPEG 95/100, PNG), original EXIF and colour profile kept
- Batch: drop many photos or a folder

## Run

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

Text uses Helvetica Neue from macOS. The SONY logo in `assets/` is a trademark of Sony Group Corporation, included for personal use only.
