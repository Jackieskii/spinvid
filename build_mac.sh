#!/bin/bash
# Builds release/SpinVid-mac-<chip>.zip on a Mac (contains SpinVid.app with FFmpeg inside).
# Use Python from python.org (it includes Tkinter).  Run:  bash build_mac.sh
set -e
cd "$(dirname "$0")"
python3 -c "import tkinter" || { echo "This Python has no Tkinter. Install Python from python.org."; exit 1; }
python3 -m pip install --upgrade -r requirements.txt pyinstaller imageio-ffmpeg
python3 build.py
open release
