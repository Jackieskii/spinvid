# SpinVid for Windows

Turn any picture into a spinning-record video with your music.

## Quick start

1. Install **Python 3.9+** from https://www.python.org/downloads/ and tick **“Add python.exe to PATH”** during setup.
2. Double-click **`run.bat`**. The first time, it installs Pillow and offers to install FFmpeg through `winget`.
   If it installs FFmpeg, run `run.bat` again so Windows picks it up.

To install FFmpeg yourself instead, run `winget install Gyan.FFmpeg`, or put `ffmpeg.exe` and `ffplay.exe` in a folder named `ffmpeg` next to `spinvid.py`.

## Using it

| Step | What to do |
|---|---|
| **1 Image** | Choose a picture. Drag it in the preview to reposition it, and scroll or use the slider to zoom. |
| **2 Style** | **Record (cut out)** puts the cropped image on a blurred or solid background. **Spin in place** keeps the whole picture and turns only a circle in the middle. You also choose circle or square and the size. |
| **3 Spin** | Speed runs from 0× to 2× (1× is a gentle 7 RPM, and Reset speed goes back to it). You also choose the direction. |
| **4 Audio** | Choose a song (MP3, WAV, M4A, FLAC, or even a video file). Drag the yellow handles to trim it, or drag the middle to slide the selection. Quick buttons: 15s / 30s / 60s / Full. |
| **5 Export** | Choose Square, Portrait (TikTok/Reels/Shorts) or Landscape, at 720p or 1080p and 30 or 60 fps, then click **Export video**. |

Press **▶ Play preview** to watch the spin with the trimmed audio before you export.
If you don't add audio, set the video length in seconds instead.

## Build on GitHub (Windows + Mac, no Mac needed)

GitHub builds the app on its own Windows and Mac computers for free.

1. Sign in at https://github.com and click **New repository**. Name it `SpinVid`, make it **Public** (builds are free and unlimited for public repos), and click **Create repository**.
2. On the new repo page, click **uploading an existing file**. Open your `SpinVid` folder and drag **everything inside it**, including the `.github` folder, into the browser. Then click **Commit changes**.
3. Open the **Actions** tab. A build named "Build SpinVid" starts automatically and takes about 5–10 minutes.
4. When it finishes, open **Releases** (right side of the repo's main page) and choose **SpinVid downloads**. It has:
   - `SpinVid-windows.exe`
   - `SpinVid-mac-apple-silicon.zip` (Macs with M1/M2/M3/M4 chips)
   - `SpinVid-mac-intel.zip` (older Macs)

   Send your friends that page's link. Each file has FFmpeg inside, so there's nothing else to install.

Every time you upload changed files, GitHub rebuilds them. You can also start a build from **Actions → Build SpinVid → Run workflow**.

**Opening the Mac app the first time:** the app isn't signed by Apple, so macOS blocks it at first. Unzip it, double-click SpinVid, click **Done** on the warning, then go to **System Settings → Privacy & Security**, scroll down and click **Open Anyway**. This is only needed once.

## Build the Windows .exe on your own PC

Double-click **`build_exe.bat`**. It creates `release\SpinVid-windows.exe` and opens the folder.
(On a Mac, the equivalent is `bash build_mac.sh`.)

Notes:
- The exe is large because FFmpeg is inside. Share it with Google Drive, OneDrive, WeTransfer, or the GitHub Releases link.
- Windows may show "Windows protected your PC" because the app isn't code-signed. Click **More info → Run anyway**.
- The first launch takes a few seconds while the exe unpacks itself.
- The bundled FFmpeg is GPL-licensed open-source software (ffmpeg.org).

## Files

- `spinvid.py`: the window and controls (Tkinter)
- `engine.py`: image cropping, spin rendering, waveform, and MP4 export through FFmpeg
- `build.py`: makes the shareable app for whatever computer it runs on
- `.github/workflows/build.yml`: the GitHub cloud build for Windows and Mac
