"""
SpinVid for Windows — turn an image into a spinning video with music.

Run:  python spinvid.py
Needs: Python 3.9+, Pillow, and FFmpeg (ffmpeg.exe + ffplay.exe on PATH or
in an "ffmpeg" folder next to this file).
"""
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

import engine as E

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
UI_FONT = "Segoe UI" if IS_WIN else ("Helvetica Neue" if IS_MAC else "DejaVu Sans")

IMAGE_TYPES = [("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff"),
               ("All files", "*.*")]
AUDIO_TYPES = [("Audio or video", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma "
                                  "*.mp4 *.mov *.mkv *.webm"),
               ("All files", "*.*")]

DARK = "#16161c"
ACCENT = "#7c5cff"


# ==========================================================================
# Waveform with draggable start/end handles
# ==========================================================================
class WaveformView(tk.Canvas):
    HANDLE_GRAB = 10
    MIN_LEN = 0.5

    def __init__(self, master, on_change, height=90, **kw):
        super().__init__(master, height=height, bg=DARK, highlightthickness=0,
                         cursor="hand2", **kw)
        self.wf = None
        self.start = 0.0
        self.end = 0.0
        self.message = "No audio yet — click “Choose audio…”"
        self.on_change = on_change
        self.playhead = None
        self._drag = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    # -- public --------------------------------------------------------------
    def set_message(self, msg):
        self.wf = None
        self.message = msg
        self.redraw()

    def set_waveform(self, wf, start, end):
        self.wf = wf
        self.start, self.end = start, end
        self.redraw()

    def set_range(self, start, end):
        if not self.wf:
            return
        d = self.wf.duration
        end = min(max(end, self.MIN_LEN), d)
        start = min(max(start, 0.0), max(0.0, end - self.MIN_LEN))
        self.start, self.end = start, end
        self.redraw()
        self.on_change(self.start, self.end)

    def set_playhead(self, t):
        self.playhead = t
        self.delete("playhead")
        if self.wf and t is not None:
            x = self._t2x(t)
            self.create_line(x, 0, x, self.winfo_height(), fill="#ffffff",
                             width=2, tags="playhead")

    # -- drawing -------------------------------------------------------------
    def _t2x(self, t):
        w = max(1, self.winfo_width())
        return 0 if not self.wf else t / self.wf.duration * w

    def _x2t(self, x):
        w = max(1, self.winfo_width())
        return 0 if not self.wf else min(max(x / w, 0), 1) * self.wf.duration

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if not self.wf:
            self.create_text(w / 2, h / 2, text=self.message, fill="#8a8a99")
            return
        mid = h / 2
        xs, xe = self._t2x(self.start), self._t2x(self.end)
        self.create_rectangle(xs, 0, xe, h, fill="#2a2440", outline="")
        peaks = self.wf.peaks
        n = len(peaks)
        for x in range(0, w, 2):
            i0 = int(x / w * n)
            i1 = max(i0 + 1, int((x + 2) / w * n))
            p = max(peaks[i0:i1])
            amp = max(1, p * (h / 2 - 6))
            inside = xs <= x <= xe
            self.create_line(x, mid - amp, x, mid + amp,
                             fill=ACCENT if inside else "#4a4a58")
        for x in (xs, xe):
            self.create_line(x, 0, x, h, fill="#ffd34d", width=2)
            self.create_rectangle(x - 5, 0, x + 5, 14, fill="#ffd34d", outline="")
            self.create_rectangle(x - 5, h - 14, x + 5, h, fill="#ffd34d", outline="")
        self.set_playhead(self.playhead)

    # -- mouse ---------------------------------------------------------------
    def _press(self, e):
        if not self.wf:
            return
        xs, xe = self._t2x(self.start), self._t2x(self.end)
        ds, de = abs(e.x - xs), abs(e.x - xe)
        if min(ds, de) <= self.HANDLE_GRAB:
            self._drag = ("start", 0) if ds <= de else ("end", 0)
        elif xs < e.x < xe:
            self._drag = ("move", self._x2t(e.x) - self.start)
        else:
            # click outside the selection: jump the nearest handle there
            self._drag = ("start", 0) if ds < de else ("end", 0)
            self._motion(e)

    def _motion(self, e):
        if not self.wf or not self._drag:
            return
        kind, off = self._drag
        t = self._x2t(e.x)
        if kind == "start":
            self.set_range(min(t, self.end - self.MIN_LEN), self.end)
        elif kind == "end":
            self.set_range(self.start, max(t, self.start + self.MIN_LEN))
        else:
            length = self.end - self.start
            s = min(max(t - off, 0), self.wf.duration - length)
            self.set_range(s, s + length)


# ==========================================================================
# Main window
# ==========================================================================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SpinVid")
        # Windows reports real DPI; macOS always reports 72, so don't shrink there
        self.ui = 1.0 if IS_MAC else max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.geometry(f"{int(1100 * self.ui)}x{int(740 * self.ui)}")
        self.minsize(int(980 * self.ui), int(680 * self.ui))
        self._setup_style()

        self.ffmpeg = E.find_tool("ffmpeg")
        self.ffplay = E.find_tool("ffplay")

        self.src = None            # full-resolution source image
        self.src_preview = None    # downscaled copy for fast preview
        self.renderer = None
        self.photo = None
        self.wf = None
        self.audio_path = None
        self.playing = False
        self.play_t0 = 0.0
        self.player = None
        self.angle = 0.0
        self.export_thread = None
        self.cancel_evt = None
        self.q = queue.Queue()
        self._preview_job = None
        self._drag_from = None

        self._vars()
        self._build()
        self._refresh_audio_ui()
        self.after(50, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not self.ffmpeg:
            self.after(300, lambda: messagebox.showwarning(
                "FFmpeg not found",
                "SpinVid needs FFmpeg to read audio and save videos.\n\n"
                "Easiest fix: open a terminal and run\n"
                "    winget install Gyan.FFmpeg\n"
                "You can keep SpinVid open while it installs.\n\n"
                "Or put ffmpeg.exe and ffplay.exe in a folder named “ffmpeg” "
                "next to SpinVid."))

    # ------------------------------------------------------------------ setup
    def _setup_style(self):
        st = ttk.Style(self)
        if IS_MAC:
            pass  # keep the native "aqua" look
        elif "vista" in st.theme_names():
            st.theme_use("vista")
        elif "clam" in st.theme_names():
            st.theme_use("clam")
        st.configure("Section.TLabelframe.Label", font=(UI_FONT, 10, "bold"))
        st.configure("Big.TButton", font=(UI_FONT, 11, "bold"), padding=8)
        st.configure("Hint.TLabel", foreground="#777")
        st.configure("Title.TLabel", font=(UI_FONT, 16, "bold"))

    def _vars(self):
        self.v_zoom = tk.DoubleVar(value=1.0)
        self.v_mode = tk.StringVar(value="cutout")
        self.v_shape = tk.StringVar(value="circle")
        self.v_size = tk.DoubleVar(value=80)
        self.v_bg = tk.StringVar(value="blur")
        self.bg_color = "#101014"
        self.v_hole = tk.BooleanVar(value=False)
        self.v_speed = tk.DoubleVar(value=1.0)   # multiple of BASE_RPM (0–2×)
        self.v_dir = tk.StringVar(value="cw")
        self.v_fade = tk.BooleanVar(value=True)
        self.v_len = tk.DoubleVar(value=10.0)
        self.v_aspect = tk.StringVar(value="Square 1:1")
        self.v_quality = tk.StringVar(value="1080p")
        self.v_fps = tk.StringVar(value="30 fps")
        self.pan = [0.0, 0.0]
        for v in (self.v_zoom, self.v_mode, self.v_shape, self.v_size, self.v_bg, self.v_hole,
                  self.v_dir, self.v_aspect):
            v.trace_add("write", lambda *a: self.schedule_preview())
        self.v_speed.trace_add("write", lambda *a: self._update_labels())
        self.v_mode.trace_add("write", lambda *a: self._mode_changed())
        self.v_zoom.trace_add("write", lambda *a: self._update_labels())
        self.v_size.trace_add("write", lambda *a: self._update_labels())

    def _build(self):
        pad = dict(padx=10, pady=6)
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        head = ttk.Frame(root)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(head, text="SpinVid", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="   Pick an image → pick a song → export. That's it.",
                  style="Hint.TLabel").pack(side="left")

        # ---------------- left: preview ----------------
        left = ttk.Frame(root)
        left.grid(row=1, column=0, sticky="n", padx=(0, 14))
        self.box = int(460 * self.ui)
        self.canvas = tk.Canvas(left, width=self.box, height=self.box, bg=DARK,
                                highlightthickness=0, cursor="fleur")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_by(1.1))   # Linux
        self.canvas.bind("<Button-5>", lambda e: self._zoom_by(1 / 1.1))
        self.canvas.bind("<Double-Button-1>", lambda e: self._choose_image())

        ctl = ttk.Frame(left)
        ctl.pack(fill="x", pady=(8, 0))
        self.btn_play = ttk.Button(ctl, text="▶  Play preview", command=self.toggle_play)
        self.btn_play.pack(side="left")
        self.lbl_time = ttk.Label(ctl, text="")
        self.lbl_time.pack(side="left", padx=10)
        ttk.Label(left, text="Drag the picture to move it · Scroll to zoom",
                  style="Hint.TLabel").pack(anchor="w", pady=(6, 0))

        # ---------------- right: steps ----------------
        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        # 1 Image
        f1 = ttk.Labelframe(right, text=" 1   Image ", style="Section.TLabelframe")
        f1.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        f1.columnconfigure(1, weight=1)
        ttk.Button(f1, text="Choose image…", command=self._choose_image).grid(
            row=0, column=0, **pad)
        self.lbl_img = ttk.Label(f1, text="No image selected", style="Hint.TLabel")
        self.lbl_img.grid(row=0, column=1, columnspan=2, sticky="w")
        ttk.Label(f1, text="Zoom").grid(row=1, column=0, sticky="e", padx=10)
        ttk.Scale(f1, from_=1.0, to=5.0, variable=self.v_zoom).grid(
            row=1, column=1, sticky="ew", pady=(0, 8))
        self.lbl_zoom = ttk.Label(f1, width=6)
        self.lbl_zoom.grid(row=1, column=2, padx=10)

        # 2 Style
        f2 = ttk.Labelframe(right, text=" 2   Style ", style="Section.TLabelframe")
        f2.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        f2.columnconfigure(1, weight=1)
        ttk.Label(f2, text="Mode").grid(row=0, column=0, sticky="e", padx=10, pady=4)
        r = ttk.Frame(f2)
        r.grid(row=0, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(r, text="Record (cut out)", value="cutout",
                        variable=self.v_mode).pack(side="left")
        ttk.Radiobutton(r, text="Spin in place (keep the picture)", value="inplace",
                        variable=self.v_mode).pack(side="left", padx=12)

        ttk.Label(f2, text="Shape").grid(row=1, column=0, sticky="e", padx=10, pady=4)
        r = ttk.Frame(f2)
        r.grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(r, text="Circle", value="circle", variable=self.v_shape).pack(side="left")
        ttk.Radiobutton(r, text="Square", value="square", variable=self.v_shape).pack(side="left", padx=12)
        self.chk_hole = ttk.Checkbutton(r, text="Record hole in center", variable=self.v_hole)
        self.chk_hole.pack(side="left", padx=12)

        ttk.Label(f2, text="Size").grid(row=2, column=0, sticky="e", padx=10, pady=4)
        ttk.Scale(f2, from_=20, to=100, variable=self.v_size).grid(row=2, column=1, sticky="ew")
        self.lbl_size = ttk.Label(f2, width=6)
        self.lbl_size.grid(row=2, column=2, padx=10)

        ttk.Label(f2, text="Background").grid(row=3, column=0, sticky="e", padx=10, pady=(4, 8))
        r = ttk.Frame(f2)
        r.grid(row=3, column=1, columnspan=2, sticky="w", pady=(4, 8))
        self.rb_blur = ttk.Radiobutton(r, text="Blurred image", value="blur", variable=self.v_bg)
        self.rb_blur.pack(side="left")
        self.rb_color = ttk.Radiobutton(r, text="Solid color", value="color", variable=self.v_bg)
        self.rb_color.pack(side="left", padx=12)
        # a Label, because macOS ignores background colors on buttons
        self.swatch = tk.Label(r, width=3, bg=self.bg_color, relief="groove", bd=2,
                               cursor="hand2")
        self.swatch.bind("<Button-1>", lambda e: self._pick_color())
        self.swatch.pack(side="left")
        self.lbl_bg_note = ttk.Label(r, text="", style="Hint.TLabel")
        self.lbl_bg_note.pack(side="left", padx=10)

        # 3 Spin
        f3 = ttk.Labelframe(right, text=" 3   Spin ", style="Section.TLabelframe")
        f3.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        f3.columnconfigure(1, weight=1)
        ttk.Label(f3, text="Speed").grid(row=0, column=0, sticky="e", padx=10, pady=4)
        sp = ttk.Frame(f3)
        sp.grid(row=0, column=1, sticky="ew")
        sp.columnconfigure(1, weight=1)
        ttk.Label(sp, text="Slower", style="Hint.TLabel").grid(row=0, column=0, padx=(0, 6))
        ttk.Scale(sp, from_=0.0, to=2.0, variable=self.v_speed).grid(row=0, column=1, sticky="ew")
        ttk.Label(sp, text="Faster", style="Hint.TLabel").grid(row=0, column=2, padx=(6, 0))
        self.lbl_speed = ttk.Label(f3, width=8)
        self.lbl_speed.grid(row=0, column=2, padx=10)
        r = ttk.Frame(f3)
        r.grid(row=1, column=1, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(r, text="Reset speed", command=lambda: self.v_speed.set(1.0)).pack(side="left")
        ttk.Radiobutton(r, text="↻ Clockwise", value="cw", variable=self.v_dir).pack(side="left", padx=(14, 0))
        ttk.Radiobutton(r, text="↺ Counter", value="ccw", variable=self.v_dir).pack(side="left", padx=10)

        # 4 Audio
        f4 = ttk.Labelframe(right, text=" 4   Audio ", style="Section.TLabelframe")
        f4.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        f4.columnconfigure(0, weight=1)
        r = ttk.Frame(f4)
        r.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 4))
        ttk.Button(r, text="Choose audio…", command=self._choose_audio).pack(side="left")
        self.btn_rm_audio = ttk.Button(r, text="Remove", command=self._remove_audio)
        self.btn_rm_audio.pack(side="left", padx=6)
        self.lbl_audio = ttk.Label(r, text="", style="Hint.TLabel")
        self.lbl_audio.pack(side="left", padx=6)

        self.wave = WaveformView(f4, on_change=self._range_changed, height=int(84 * self.ui))
        self.wave.grid(row=1, column=0, sticky="ew", padx=10)

        self.audio_row = ttk.Frame(f4)
        self.audio_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 8))
        self.lbl_range = ttk.Label(self.audio_row, text="")
        self.lbl_range.pack(side="left")
        ttk.Checkbutton(self.audio_row, text="Fade in/out", variable=self.v_fade).pack(side="right")
        for label, secs in (("Full", None), ("60s", 60), ("30s", 30), ("15s", 15)):
            ttk.Button(self.audio_row, text=label, width=5,
                       command=lambda s=secs: self._quick_len(s)).pack(side="right", padx=2)

        self.noaudio_row = ttk.Frame(f4)
        ttk.Label(self.noaudio_row, text="Video length (seconds):").pack(side="left")
        ttk.Spinbox(self.noaudio_row, from_=1, to=600, increment=1, width=6,
                    textvariable=self.v_len).pack(side="left", padx=6)

        # 5 Export
        f5 = ttk.Labelframe(right, text=" 5   Export ", style="Section.TLabelframe")
        f5.grid(row=4, column=0, sticky="ew")
        f5.columnconfigure(5, weight=1)
        ttk.Label(f5, text="Format").grid(row=0, column=0, padx=(10, 4), pady=8)
        ttk.Combobox(f5, textvariable=self.v_aspect, values=list(E.ASPECTS), state="readonly",
                     width=14).grid(row=0, column=1)
        ttk.Label(f5, text="Quality").grid(row=0, column=2, padx=(14, 4))
        ttk.Combobox(f5, textvariable=self.v_quality, values=list(E.QUALITIES), state="readonly",
                     width=7).grid(row=0, column=3)
        ttk.Combobox(f5, textvariable=self.v_fps, values=list(E.FPS_CHOICES), state="readonly",
                     width=7).grid(row=0, column=4, padx=(8, 0))
        self.btn_export = ttk.Button(f5, text="Export video", style="Big.TButton",
                                     command=self._export_clicked)
        self.btn_export.grid(row=0, column=5, sticky="e", padx=10)
        self.progress = ttk.Progressbar(f5, maximum=1.0)
        self.progress.grid(row=1, column=0, columnspan=6, sticky="ew", padx=10)
        self.lbl_status = ttk.Label(f5, text="Ready.", style="Hint.TLabel")
        self.lbl_status.grid(row=2, column=0, columnspan=6, sticky="w", padx=10, pady=(4, 8))

        self._update_labels()
        self._mode_changed()
        self.draw_preview()

    # ------------------------------------------------------------- settings
    def settings(self) -> E.Settings:
        try:
            length = float(self.v_len.get())
        except (tk.TclError, ValueError):
            length = 10.0
        return E.Settings(
            mode=self.v_mode.get(),
            shape=self.v_shape.get(),
            disc_scale=self.v_size.get() / 100.0,
            zoom=self.v_zoom.get(),
            pan_x=self.pan[0], pan_y=self.pan[1],
            bg_mode=self.v_bg.get(), bg_color=self.bg_color,
            hole=self.v_hole.get(),
            rpm=self.v_speed.get() * E.BASE_RPM,
            clockwise=self.v_dir.get() == "cw",
            aspect=self.v_aspect.get(), quality=self.v_quality.get(),
            fps=E.FPS_CHOICES.get(self.v_fps.get(), 30),
            audio_path=self.audio_path,
            start=self.wave.start, end=self.wave.end,
            fade=self.v_fade.get(),
            duration=max(1.0, min(600.0, length)),
        )

    def _update_labels(self):
        self.lbl_speed.config(text=f"{self.v_speed.get():.1f}×")
        self.lbl_zoom.config(text=f"{self.v_zoom.get():.1f}×")
        self.lbl_size.config(text=f"{self.v_size.get():.0f}%")

    def _mode_changed(self):
        inplace = self.v_mode.get() == "inplace"
        flag = ["disabled"] if inplace else ["!disabled"]
        for w in (self.rb_blur, self.rb_color, self.chk_hole):
            w.state(flag)
        self.swatch.config(relief="flat" if inplace else "groove")
        self.lbl_bg_note.config(text="(the picture itself)" if inplace else "")

    # --------------------------------------------------------------- preview
    def schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(25, self._rebuild_preview)

    def _rebuild_preview(self):
        self._preview_job = None
        if not self.src_preview:
            self.renderer = None
            self.draw_preview()
            return
        s = self.settings()
        aw, ah = E.ASPECTS[s.aspect]
        if aw >= ah:
            pw, ph = self.box, int(self.box * ah / aw)
        else:
            pw, ph = int(self.box * aw / ah), self.box
        self.renderer = E.Renderer(self.src_preview, s, pw, ph)
        self.pan = list(self.renderer.pan)
        self.draw_preview()

    def draw_preview(self):
        c = self.canvas
        c.delete("all")
        if not self.renderer:
            c.create_text(self.box / 2, self.box / 2 - 12, text="Click “Choose image…”",
                          fill="#d0d0dd", font=(UI_FONT, 13, "bold"))
            c.create_text(self.box / 2, self.box / 2 + 14,
                          text="or double-click here to start", fill="#8a8a99")
            return
        self.renderer.s.rpm = self.v_speed.get() * E.BASE_RPM
        self.renderer.s.clockwise = self.v_dir.get() == "cw"
        img = self.renderer.frame(self.angle)
        self.photo = ImageTk.PhotoImage(img)
        c.create_image(self.box / 2, self.box / 2, image=self.photo)

    def _pan_start(self, e):
        if not self.renderer:
            self._choose_image()
            return
        if self.playing:
            self.stop_play()
        self.angle = 0.0
        self._drag_from = (e.x, e.y, list(self.pan))

    def _pan_move(self, e):
        if not self._drag_from or not self.renderer:
            return
        x0, y0, (px, py) = self._drag_from
        k = self.renderer.pan_per_px
        self.pan = [px - (e.x - x0) * k, py - (e.y - y0) * k]
        self.schedule_preview()

    def _wheel(self, e):
        # Windows sends ±120 per notch; macOS sends small values (±1, ±2…)
        steps = e.delta if IS_MAC else e.delta / 120
        self._zoom_by(1.1 ** max(-3, min(3, steps)))

    def _zoom_by(self, f):
        self.v_zoom.set(min(5.0, max(1.0, self.v_zoom.get() * f)))

    # --------------------------------------------------------------- image
    def _choose_image(self):
        path = filedialog.askopenfilename(title="Choose an image", filetypes=IMAGE_TYPES)
        if not path:
            return
        try:
            img = E.load_image(path)
        except Exception as ex:
            messagebox.showerror("Couldn't open image", str(ex))
            return
        self.src = img
        prev = img.copy()
        prev.thumbnail((1000, 1000), Image.LANCZOS)
        self.src_preview = prev
        self.image_path = path
        self.pan = [0.0, 0.0]
        self.v_zoom.set(1.0)
        self.lbl_img.config(text=f"{os.path.basename(path)}   ({img.width}×{img.height})")
        self.angle = 0.0
        self.schedule_preview()

    def _pick_color(self):
        if self.v_mode.get() == "inplace":
            return
        c = colorchooser.askcolor(color=self.bg_color, title="Background color")[1]
        if c:
            self.bg_color = c
            self.swatch.config(bg=c)
            self.v_bg.set("color")
            self.schedule_preview()

    # --------------------------------------------------------------- audio
    def _refind_ffmpeg(self):
        self.ffmpeg = self.ffmpeg or E.find_tool("ffmpeg")
        self.ffplay = self.ffplay or E.find_tool("ffplay")
        return self.ffmpeg

    def _choose_audio(self):
        if not self._refind_ffmpeg():
            messagebox.showerror("FFmpeg needed",
                                 "SpinVid still can't find FFmpeg.\n\nOpen a terminal and run:\n"
                                 "    winget install Gyan.FFmpeg\n\n"
                                 "then try again (no restart needed).")
            return
        path = filedialog.askopenfilename(title="Choose a song", filetypes=AUDIO_TYPES)
        if not path:
            return
        self.stop_play()
        self.wave.set_message("Loading audio…")
        self.lbl_audio.config(text=os.path.basename(path))

        def work():
            try:
                self.q.put(("wave", path, E.load_waveform(path)))
            except Exception as ex:
                self.q.put(("wave_err", path, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def _audio_loaded(self, path, wf):
        self.audio_path = path
        self.wf = wf
        self.wave.set_waveform(wf, 0.0, min(wf.duration, 30.0))
        self._refresh_audio_ui()

    def _remove_audio(self):
        self.stop_play()
        self.audio_path = None
        self.wf = None
        self.wave.set_message("No audio yet — click “Choose audio…”")
        self.lbl_audio.config(text="")
        self._refresh_audio_ui()

    def _refresh_audio_ui(self):
        if self.audio_path:
            self.noaudio_row.grid_forget()
            self.audio_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 8))
            self.btn_rm_audio.state(["!disabled"])
            self._range_changed(self.wave.start, self.wave.end)
        else:
            self.audio_row.grid_forget()
            self.noaudio_row.grid(row=2, column=0, sticky="w", padx=10, pady=(6, 8))
            self.btn_rm_audio.state(["disabled"])

    def _range_changed(self, start, end):
        self.lbl_range.config(
            text=f"Start {E.fmt_time(start)}   End {E.fmt_time(end)}   "
                 f"Length {end - start:.1f}s")

    def _quick_len(self, secs):
        if not self.wf:
            return
        if secs is None:
            self.wave.set_range(0, self.wf.duration)
        else:
            s = self.wave.start
            if s + secs > self.wf.duration:
                s = max(0.0, self.wf.duration - secs)
            self.wave.set_range(s, s + secs)

    # --------------------------------------------------------------- playback
    def toggle_play(self):
        if self.playing:
            self.stop_play()
        else:
            self.start_play()

    def start_play(self):
        if not self.renderer:
            messagebox.showinfo("SpinVid", "Choose an image first.")
            return
        s = self.settings()
        self.play_len = s.length()
        self._refind_ffmpeg()
        if self.audio_path and self.ffplay:
            cmd = [self.ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet",
                   "-ss", f"{s.start:.3f}", "-t", f"{self.play_len:.3f}", self.audio_path]
            try:
                self.player = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x08000000 if os.name == "nt" else 0)
            except OSError:
                self.player = None
        elif self.audio_path:
            self._play_fallback(s)
        self.playing = True
        self.play_t0 = time.monotonic()
        self.btn_play.config(text="■  Stop")
        self._tick()

    def _play_fallback(self, s):
        """No ffplay: cut the selection to a WAV and play it with the OS player."""
        import tempfile
        wav = os.path.join(tempfile.gettempdir(), "spinvid_preview.wav")
        try:
            self._stop_winsound()
            E.extract_clip(self.audio_path, s.start, self.play_len, wav, s.fade)
            if IS_WIN:
                import winsound
                winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
                self._winsound_on = True
            elif IS_MAC:
                self.player = subprocess.Popen(["afplay", wav], stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                self.lbl_status.config(text="Previewing without sound (ffplay not found).")
        except Exception:
            self.lbl_status.config(text="Couldn't play audio — previewing without sound.")

    def _stop_winsound(self):
        if getattr(self, "_winsound_on", False):
            try:
                import winsound
                winsound.PlaySound(None, 0)
            except Exception:
                pass
            self._winsound_on = False

    def stop_play(self):
        self.playing = False
        self._stop_winsound()
        if self.player and self.player.poll() is None:
            try:
                self.player.terminate()
            except OSError:
                pass
        self.player = None
        self.btn_play.config(text="▶  Play preview")
        self.lbl_time.config(text="")
        self.wave.set_playhead(None)

    def _tick(self):
        if not self.playing:
            return
        t = time.monotonic() - self.play_t0
        if t >= self.play_len:
            self.stop_play()
            return
        self.angle = 360.0 * self.v_speed.get() * E.BASE_RPM / 60.0 * t
        self.draw_preview()
        self.lbl_time.config(text=f"{E.fmt_time(t)} / {E.fmt_time(self.play_len)}")
        if self.audio_path:
            self.wave.set_playhead(self.wave.start + t)
        self.after(33, self._tick)

    # --------------------------------------------------------------- export
    def _export_clicked(self):
        if self.export_thread and self.export_thread.is_alive():
            self.cancel_evt.set()
            self.lbl_status.config(text="Cancelling…")
            return
        if not self.src:
            messagebox.showinfo("SpinVid", "Choose an image first.")
            return
        if not self._refind_ffmpeg():
            messagebox.showerror("FFmpeg needed",
                                 "SpinVid still can't find FFmpeg.\n\nOpen a terminal and run:\n"
                                 "    winget install Gyan.FFmpeg\n\n"
                                 "then try again (no restart needed).")
            return
        base = os.path.splitext(os.path.basename(self.image_path))[0]
        out = filedialog.asksaveasfilename(
            title="Save video as", defaultextension=".mp4",
            initialfile=f"{base}_spin.mp4", filetypes=[("MP4 video", "*.mp4")])
        if not out:
            return
        self.stop_play()
        s = self.settings()
        self.cancel_evt = threading.Event()
        self.btn_export.config(text="Cancel")
        self.progress["value"] = 0

        def work():
            try:
                E.export(self.src, s, out,
                         progress=lambda f, t: self.q.put(("prog", f, t)),
                         cancel=self.cancel_evt)
                self.q.put(("done", out, None))
            except E.Cancelled:
                self.q.put(("cancelled", None, None))
            except Exception as ex:
                self.q.put(("error", str(ex), None))
        self.export_thread = threading.Thread(target=work, daemon=True)
        self.export_thread.start()

    def _poll_queue(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "wave":
                    self._audio_loaded(a, b)
                elif kind == "wave_err":
                    self._remove_audio()
                    messagebox.showerror("Couldn't load audio", b)
                elif kind == "prog":
                    self.progress["value"] = a
                    self.lbl_status.config(text=b)
                elif kind == "done":
                    self.btn_export.config(text="Export video")
                    self.lbl_status.config(text=f"Saved: {a}")
                    if messagebox.askyesno("Done!", "Your video is ready.\n\nOpen the folder?"):
                        self._reveal(a)
                elif kind == "cancelled":
                    self.btn_export.config(text="Export video")
                    self.progress["value"] = 0
                    self.lbl_status.config(text="Export cancelled.")
                elif kind == "error":
                    self.btn_export.config(text="Export video")
                    self.lbl_status.config(text="Export failed.")
                    messagebox.showerror("Export failed", a)
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    def _reveal(self, path):
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError:
            pass

    def _on_close(self):
        self.stop_play()
        if self.cancel_evt:
            self.cancel_evt.set()
        self.destroy()


def main():
    if os.name == "nt":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
