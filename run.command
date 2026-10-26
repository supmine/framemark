#!/bin/zsh
# Double-click in Finder to open Sony Exif Frame
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q PySide6 Pillow pillow-heif
fi
exec .venv/bin/python app.py "$@"
