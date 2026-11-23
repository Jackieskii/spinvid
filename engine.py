"""
SpinVid engine: image preparation, frame rendering, audio waveform loading
and MP4 export via FFmpeg. No GUI code lives here.
"""
import array
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

FPS = 30            # default
FPS_CHOICES = {"30 fps": 30, "60 fps": 60}
BASE_RPM = 7.0       # "1.0×" speed
ASPECTS = {
    "Square 1:1": (1, 1),
    "Portrait 9:16": (9, 16),
    "Landscape 16:9": (16, 9),
}
QUALITIES = {"720p": 720, "1080p": 1080}

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


# --------------------------------------------------------------------------
# FFmpeg helpers
# --------------------------------------------------------------------------
def _search_dirs():
    dirs = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        dirs.append(exe_dir)
        if hasattr(sys, "_MEIPASS"):
            dirs.append(sys._MEIPASS)
        # macOS .app bundle: Contents/MacOS/SpinVid -> Contents/Frameworks, Contents/Resources
        dirs += [os.path.join(exe_dir, "..", "Frameworks"),
                 os.path.join(exe_dir, "..", "Resources")]
    here = os.path.dirname(os.path.abspath(__file__))
    dirs.append(here)
    out = []
    for d in dirs:
        out += [d, os.path.join(d, "ffmpeg"), os.path.join(d, "ffmpeg", "bin")]
    return out


def _windows_install_dirs():
    """Common FFmpeg install spots, plus the *current* PATH from the registry
    (apps started from Explorer don't see PATH changes until you sign out)."""
    import glob
    dirs = []
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        dirs.append(os.path.join(la, "Microsoft", "WinGet", "Links"))
        dirs += glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages",
                                       "*FFmpeg*", "*", "bin"))
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        pf = os.environ.get(env)
        if pf:
            dirs.append(os.path.join(pf, "ffmpeg", "bin"))
    dirs += [r"C:\ffmpeg\bin", r"C:\ProgramData\chocolatey\bin",
             os.path.join(os.path.expanduser("~"), "scoop", "shims")]
    try:
        import winreg
        for root, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(root, key) as k:
                    val = winreg.QueryValueEx(k, "Path")[0]
                dirs += [os.path.expandvars(p) for p in val.split(";") if p.strip()]
            except OSError:
                pass
    except ImportError:
        pass
    return dirs


def _mac_install_dirs():
    # apps opened from Finder get a minimal PATH that skips Homebrew/MacPorts
    return ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin",
            os.path.expanduser("~/bin")]


def find_tool(name: str) -> Optional[str]:
    """Find ffmpeg / ffplay next to the app, then on PATH, then in common install spots."""
    exe = name + (".exe" if os.name == "nt" else "")
    for d in _search_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    found = shutil.which(name)
    if found:
        return found
    extra = (_windows_install_dirs() if os.name == "nt"
             else _mac_install_dirs() if sys.platform == "darwin" else [])
    for d in extra:
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    if name == "ffmpeg":
        try:  # pip install imageio-ffmpeg ships a portable ffmpeg
            import imageio_ffmpeg
            p = imageio_ffmpeg.get_ffmpeg_exe()
            if p and os.path.isfile(p):
                return p
        except Exception:
            pass
    return None


def extract_clip(path: str, start: float, length: float, out_wav: str, fade: bool = False):
    """Cut the selected part of a song to a WAV file (used for preview when ffplay is missing)."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found.")
    cmd = [ffmpeg, "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-i", path, "-vn", "-ac", "2", "-ar", "44100"]
    if fade and length > 1.0:
        fi, fo = min(0.5, length / 4), min(1.5, length / 4)
        cmd += ["-af", f"afade=t=in:st=0:d={fi:.2f},afade=t=out:st={length - fo:.3f}:d={fo:.2f}"]
    cmd += ["-c:a", "pcm_s16le", out_wav]
    subprocess.run(cmd, check=True, capture_output=True, **_popen_kwargs())


def _popen_kwargs():
    return {"creationflags": _NO_WINDOW} if os.name == "nt" else {}


def output_size(aspect: str, quality: str):
    aw, ah = ASPECTS[aspect]
    short = QUALITIES[quality]
    if aw >= ah:
        h, w = short, short * aw / ah
    else:
        w, h = short, short * ah / aw
    # libx264 + yuv420p needs even dimensions
    return int(round(w / 2)) * 2, int(round(h / 2)) * 2


def fmt_time(t: float) -> str:
    t = max(0.0, t)
    m = int(t // 60)
    return f"{m}:{t - m * 60:04.1f}"


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------
@dataclass
class Waveform:
    duration: float
    peaks: list  # values 0..1, evenly spaced over the duration


def load_waveform(path: str, buckets: int = 1500, rate: int = 4000) -> Waveform:
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found.")
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate),
         "-f", "s16le", "-"],
        capture_output=True, **_popen_kwargs())
    data = proc.stdout
    if not data:
        err = proc.stderr.decode(errors="ignore").strip().splitlines()
        raise RuntimeError(err[-1] if err else "Could not read audio from this file.")
    samples = array.array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    duration = n / rate
    buckets = max(1, min(buckets, n))
    step = n / buckets
    peaks = []
    for i in range(buckets):
        chunk = samples[int(i * step): max(int((i + 1) * step), int(i * step) + 1)]
        peaks.append(max(max(chunk), -min(chunk)) / 32768.0)
    top = max(peaks) or 1.0
    return Waveform(duration, [p / top for p in peaks])


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
@dataclass
class Settings:
    image_path: str = ""
    mode: str = "cutout"           # "cutout" (disc on a background) | "inplace" (spin the centre of the picture)
    shape: str = "circle"          # "circle" | "square"
    disc_scale: float = 0.80       # disc size relative to the frame's short side
    zoom: float = 1.0              # 1 = largest square crop of the image
    pan_x: float = 0.0             # crop offset, as a fraction of the image's short side
    pan_y: float = 0.0
    bg_mode: str = "blur"          # "blur" | "color"
    bg_color: str = "#101014"
    hole: bool = False             # vinyl-style centre hole
    rpm: float = BASE_RPM
    clockwise: bool = True
    aspect: str = "Square 1:1"
    quality: str = "1080p"
    fps: int = FPS
    audio_path: Optional[str] = None
    start: float = 0.0
    end: float = 10.0
    fade: bool = True
    duration: float = 10.0          # used when there is no audio

    def length(self) -> float:
        if self.audio_path:
            return max(0.1, self.end - self.start)
        return max(0.1, self.duration)


def clamp_pan(img_size, crop_size, pan_x, pan_y):
    """Keep the crop rectangle inside the image. Pan is a fraction of the image's short side."""
    w, h = img_size
    cw, ch = crop_size
    short = min(w, h)
    max_x = max(0.0, (w - cw) / 2 / short)
    max_y = max(0.0, (h - ch) / 2 / short)
    return (min(max(pan_x, -max_x), max_x), min(max(pan_y, -max_y), max_y))


def crop_rect(img, aspect, zoom, pan_x, pan_y):
    """Largest rectangle of the given aspect (w/h), shrunk by zoom and shifted by pan.
    Returns (cropped image, clamped pan, crop width in source pixels)."""
    w, h = img.size
    if w / h > aspect:
        ch, cw = h, h * aspect
    else:
        cw, ch = w, w / aspect
    z = max(zoom, 1.0)
    cw, ch = cw / z, ch / z
    pan = clamp_pan((w, h), (cw, ch), pan_x, pan_y)
    short = min(w, h)
    cx, cy = w / 2 + pan[0] * short, h / 2 + pan[1] * short
    box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    return img.crop(tuple(int(round(v)) for v in box)), pan, cw


def load_image(path: str) -> Image.Image:
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGBA")


class Renderer:
    """Pre-computes the background and the disc so each frame is one rotate + paste."""

    def __init__(self, src: Image.Image, s: Settings, width: int, height: int):
        self.w, self.h = width, height
        self.s = s
        short = min(width, height)
        src_short = min(src.size)
        target = max(8, int(short * s.disc_scale))
        side = target if s.shape == "circle" else max(8, int(target / math.sqrt(2)))
        self.expand = s.shape != "circle"

        if s.mode == "inplace":
            # the whole frame is the picture; only a centre region turns
            framed, self.pan, cw = crop_rect(src, width / height, s.zoom, s.pan_x, s.pan_y)
            framed = framed.convert("RGB").resize((width, height), Image.LANCZOS)
            self.pan_per_px = (cw / src_short) / width
            x0, y0 = (width - side) // 2, (height - side) // 2
            disc = framed.crop((x0, y0, x0 + side, y0 + side)).convert("RGBA")
            mask = _circle_mask(side, False) if s.shape == "circle" \
                else Image.new("L", (side, side), 255)
            disc.putalpha(mask)
            self.disc = disc
            self.bg = framed.convert("RGBA")
            return

        square, self.pan, cw = crop_rect(src, 1.0, s.zoom, s.pan_x, s.pan_y)
        self.pan_per_px = (cw / src_short) / side
        disc = square.resize((side, side), Image.LANCZOS)
        if s.shape == "circle":
            mask = _circle_mask(side, s.hole)
        else:
            mask = Image.new("L", (side, side), 255)
            if s.hole:
                mask = _punch_hole(mask)
        disc.putalpha(Image.composite(disc.getchannel("A"), Image.new("L", disc.size, 0), mask))
        self.disc = disc
        self.bg = self._background(src, target, side)

    def _background(self, src, target, side):
        w, h = self.w, self.h
        if self.s.bg_mode == "blur":
            bg = ImageOps.fit(src.convert("RGB"), (w, h), Image.LANCZOS)
            small = bg.resize((max(1, w // 8), max(1, h // 8)), Image.BILINEAR)
            small = small.filter(ImageFilter.GaussianBlur(max(2, w // 120)))
            bg = small.resize((w, h), Image.BICUBIC)
            bg = ImageEnhance.Brightness(bg).enhance(0.55)
        else:
            bg = Image.new("RGB", (w, h), self.s.bg_color)
        bg = bg.convert("RGBA")

        # soft drop shadow under the disc (static, does not spin)
        shadow_size = target if self.s.shape == "circle" else int(side * 1.25)
        pad = max(4, shadow_size // 10)
        sh = Image.new("L", (shadow_size + pad * 4, shadow_size + pad * 4), 0)
        d = ImageDraw.Draw(sh)
        o = pad * 2
        d.ellipse((o, o + pad // 2, o + shadow_size, o + shadow_size + pad // 2), fill=150)
        sh = sh.filter(ImageFilter.GaussianBlur(pad))
        black = Image.new("RGBA", sh.size, (0, 0, 0, 255))
        black.putalpha(sh)
        bg.alpha_composite(black, ((w - sh.width) // 2, (h - sh.height) // 2))
        return bg

    def frame_at(self, t: float) -> Image.Image:
        angle = 360.0 * self.s.rpm / 60.0 * t
        return self.frame(angle)

    def frame(self, angle: float) -> Image.Image:
        # PIL rotates counter-clockwise for positive angles
        a = -angle if self.s.clockwise else angle
        disc = self.disc.rotate(a % 360, resample=Image.BICUBIC, expand=self.expand)
        out = self.bg.copy()
        out.alpha_composite(disc, ((self.w - disc.width) // 2, (self.h - disc.height) // 2))
        return out.convert("RGB")


def _circle_mask(side: int, hole: bool) -> Image.Image:
    ss = 4
    big = Image.new("L", (side * ss, side * ss), 0)
    ImageDraw.Draw(big).ellipse((0, 0, side * ss - 1, side * ss - 1), fill=255)
    mask = big.resize((side, side), Image.LANCZOS)
    return _punch_hole(mask) if hole else mask


def _punch_hole(mask: Image.Image) -> Image.Image:
    side = mask.width
    ss = 4
    big = mask.resize((side * ss, side * ss), Image.NEAREST)
    d = ImageDraw.Draw(big)
    c = side * ss / 2
    r = side * ss * 0.035
    d.ellipse((c - r, c - r, c + r, c + r), fill=0)
    return big.resize((side, side), Image.LANCZOS)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
class Cancelled(Exception):
    pass


def export(src: Image.Image, s: Settings, out_path: str, progress=None, cancel=None):
    """
    Render one full revolution, then let FFmpeg loop it for the whole length and
    mux in the trimmed audio. progress(fraction, text) is called along the way.
    """
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg was not found.")
    progress = progress or (lambda f, t: None)
    is_cancelled = (lambda: cancel.is_set()) if cancel is not None else (lambda: False)

    w, h = output_size(s.aspect, s.quality)
    length = s.length()
    renderer = Renderer(src, s, w, h)

    fps = int(s.fps) or FPS
    if s.rpm <= 0.01:
        n_frames = 1
    else:
        n_frames = max(1, round(fps * 60.0 / s.rpm))
    n_frames = min(n_frames, int(math.ceil(length * fps)) + 1)
    step = 360.0 / n_frames if s.rpm > 0 else 0.0

    tmp = tempfile.mkdtemp(prefix="spinvid_")
    loop_path = os.path.join(tmp, "loop.mp4")
    err1 = os.path.join(tmp, "pass1.log")
    err2 = os.path.join(tmp, "pass2.log")
    try:
        # ---- pass 1: one revolution -------------------------------------
        progress(0.0, "Rendering spin…")
        with open(err1, "wb") as ef:
            p = subprocess.Popen(
                [ffmpeg, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                 "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                 "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
                 "-pix_fmt", "yuv420p", loop_path],
                stdin=subprocess.PIPE, stderr=ef, **_popen_kwargs())
            try:
                for i in range(n_frames):
                    if is_cancelled():
                        raise Cancelled()
                    p.stdin.write(renderer.frame(i * step).tobytes())
                    progress(0.4 * (i + 1) / n_frames, f"Rendering spin… {i + 1}/{n_frames}")
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass
                p.wait()
        if p.returncode != 0:
            raise RuntimeError("FFmpeg failed while rendering:\n" + _tail(err1))

        # ---- pass 2: loop + audio ----------------------------------------
        progress(0.4, "Encoding video…")
        cmd = [ffmpeg, "-y", "-v", "error", "-stream_loop", "-1", "-i", loop_path]
        if s.audio_path:
            cmd += ["-ss", f"{s.start:.3f}", "-t", f"{length:.3f}", "-i", s.audio_path]
        cmd += ["-map", "0:v:0"]
        if s.audio_path:
            cmd += ["-map", "1:a:0"]
            if s.fade and length > 1.0:
                fi = min(0.5, length / 4)
                fo = min(1.5, length / 4)
                cmd += ["-af", f"afade=t=in:st=0:d={fi:.2f},"
                               f"afade=t=out:st={length - fo:.3f}:d={fo:.2f}"]
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-t", f"{length:.3f}", "-r", str(fps),
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats", out_path]
        with open(err2, "wb") as ef:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ef,
                                 text=True, **_popen_kwargs())
            for line in p.stdout:
                if is_cancelled():
                    p.kill()
                    p.wait()
                    raise Cancelled()
                m = re.match(r"out_time_(?:us|ms)=(\d+)", line.strip())
                if m:
                    secs = int(m.group(1)) / 1_000_000
                    frac = min(1.0, secs / length)
                    progress(0.4 + 0.6 * frac, f"Encoding video… {int(frac * 100)}%")
            p.wait()
        if p.returncode != 0:
            raise RuntimeError("FFmpeg failed while encoding:\n" + _tail(err2))
        progress(1.0, "Done!")
    except Cancelled:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tail(path, n=6):
    try:
        with open(path, "r", errors="ignore") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        return "\n".join(lines[-n:]) or "(no details)"
    except OSError:
        return "(no details)"
