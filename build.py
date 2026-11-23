"""
Build a shareable SpinVid for the computer you run this on.

  Windows -> release/SpinVid-windows.exe      (one file, FFmpeg inside)
  macOS   -> release/SpinVid-mac-<chip>.zip   (contains SpinVid.app, FFmpeg inside)

Needs:  pip install -r requirements.txt pyinstaller imageio-ffmpeg
Use --dry-run to only print what would happen.
"""
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
RELEASE = os.path.join(HERE, "release")
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
DRY = "--dry-run" in sys.argv


def log(msg):
    print(f"[build] {msg}", flush=True)


def _is_shared_build(exe_path):
    """Builds that can't travel alone: gyan.dev 'shared' builds (need avcodec-*.dll)
    and package-manager shims (chocolatey/scoop) that just point somewhere else."""
    import glob
    low = exe_path.lower()
    if "chocolatey" in low or os.path.join("scoop", "shims") in low:
        return True
    return bool(glob.glob(os.path.join(os.path.dirname(exe_path), "avcodec-*.dll")))


def portable_ffmpeg():
    """An FFmpeg that runs on other people's computers (no Homebrew/DLL dependencies)."""
    override = os.environ.get("SPINVID_FFMPEG")
    if override and os.path.isfile(override):
        return override
    if IS_WIN and not os.environ.get("SPINVID_USE_IMAGEIO"):
        # gyan.dev builds (winget install Gyan.FFmpeg) are static -> fine to ship
        sys.path.insert(0, HERE)
        import engine
        p = engine.find_tool("ffmpeg")
        if p and "imageio_ffmpeg" not in p and not _is_shared_build(p):
            return p
    try:
        import imageio_ffmpeg  # static builds for Windows, macOS (Intel + Apple Silicon), Linux
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("Install imageio-ffmpeg first:  pip install imageio-ffmpeg")


def portable_ffplay():
    # Only bundle ffplay on Windows when it comes from a static gyan.dev build.
    # Without it, SpinVid previews sound with winsound (Windows) or afplay (Mac).
    if not IS_WIN or os.environ.get("SPINVID_USE_IMAGEIO"):
        return None
    sys.path.insert(0, HERE)
    import engine
    p = engine.find_tool("ffplay")
    if p and _is_shared_build(p):
        return None
    return p


def main():
    ext = ".exe" if IS_WIN else ""
    ffbin = os.path.join(BUILD, "ffbin")
    ffmpeg_src = portable_ffmpeg()
    ffplay_src = portable_ffplay()
    log(f"FFmpeg: {ffmpeg_src}")
    log(f"ffplay: {ffplay_src or '(not bundled - preview uses the system player)'}")

    args = ["--noconfirm", "--clean", "--windowed", "--name", "SpinVid",
            "--distpath", DIST, "--workpath", os.path.join(BUILD, "work"),
            "--specpath", BUILD]
    if IS_WIN:
        args.append("--onefile")             # one .exe
    if IS_MAC:
        args += ["--osx-bundle-identifier", "com.spinvid.app"]

    staged = [(ffmpeg_src, "ffmpeg" + ext)]
    if ffplay_src:
        staged.append((ffplay_src, "ffplay" + ext))
    for src, name in staged:
        dst = os.path.join(ffbin, name)
        args += ["--add-binary", f"{dst}{os.pathsep}ffmpeg"]
    args.append(os.path.join(HERE, "spinvid.py"))

    log("PyInstaller " + " ".join(args))
    if DRY:
        return

    shutil.rmtree(BUILD, ignore_errors=True)
    os.makedirs(ffbin, exist_ok=True)
    for src, name in staged:
        dst = os.path.join(ffbin, name)
        shutil.copy2(src, dst)
        os.chmod(dst, 0o755)

    import PyInstaller.__main__
    PyInstaller.__main__.run(args)

    os.makedirs(RELEASE, exist_ok=True)
    if IS_WIN:
        out = os.path.join(RELEASE, "SpinVid-windows.exe")
        shutil.copy2(os.path.join(DIST, "SpinVid.exe"), out)
    elif IS_MAC:
        chip = "apple-silicon" if platform.machine() == "arm64" else "intel"
        out = os.path.join(RELEASE, f"SpinVid-mac-{chip}.zip")
        if os.path.exists(out):
            os.remove(out)
        # ditto keeps the app's permissions and signature intact
        subprocess.run(["ditto", "-c", "-k", "--keepParent",
                        os.path.join(DIST, "SpinVid.app"), out], check=True)
    else:
        out = os.path.join(DIST, "SpinVid")
    log(f"Done -> {out}")


if __name__ == "__main__":
    main()
