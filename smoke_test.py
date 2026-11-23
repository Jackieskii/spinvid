"""Opens the real SpinVid window, renders a preview and a short export, then closes.
Run by the GitHub build before packaging so startup crashes never ship."""
import os
import sys
import tempfile

from PIL import Image

import engine as E
import spinvid

tmp = tempfile.mkdtemp()
img_path = os.path.join(tmp, "test.png")
Image.new("RGB", (800, 600), (200, 40, 40)).save(img_path)

app = spinvid.App()
app.update()
app.src = E.load_image(img_path)
app.src_preview = app.src.copy()
app.image_path = img_path
for mode in ("cutout", "inplace"):
    app.v_mode.set(mode)
    app._rebuild_preview()
    app.update()
print("window + preview OK:", sys.platform, "font", spinvid.UI_FONT)

s = app.settings()
s.duration, s.quality, s.fps = 1.0, "720p", 30
E.export(app.src, s, os.path.join(tmp, "out.mp4"))
print("export OK, ffmpeg =", E.find_tool("ffmpeg"))
app.destroy()
