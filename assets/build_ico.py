import sys, struct
sys.path.insert(0, "/home/alsaher/Projects/dbisam-tool/venv/lib/python3.14/site-packages")
from PIL import Image
import io

assets = "/home/alsaher/Projects/dbisam-tool/assets"
specs = [
    ("snitchql_exe_preview.png", "snitchql_exe.ico"),
    ("snitchql_app_preview.png", "snitchql_app.ico"),
]
sizes = [16, 24, 32, 48, 64, 128, 256]

def make_ico(src, dst):
    im = Image.open(f"{assets}/{src}").convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS)
    png_blobs = []
    for s in sizes:
        small = im.resize((s, s), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        small.save(buf, format="PNG")
        png_blobs.append(buf.getvalue())

    # ICONDIR: reserved(2)=0, type(2)=1, count(2)
    header = struct.pack("<HHH", 0, 1, len(png_blobs))
    dir_entries = b""
    offset = 6 + 16 * len(png_blobs)
    image_data = b""
    for png in png_blobs:
        w = h = sizes[0]  # placeholder, fixed below per-image
        # Each entry references its real size
        # ICONDIRENTRY: width(1), height(1), colors(1)=0, reserved(1)=0,
        #               planes(2)=1, bpp(2)=32, size(4), offset(4)
        pass
    # Build properly: one entry per size
    entries = []
    data_offset = 6 + 16 * len(sizes)
    body = b""
    for (s, png) in zip(sizes, png_blobs):
        entries.append(struct.pack("<BBBBHHII",
            s if s < 256 else 0,   # width (0 means 256)
            s if s < 256 else 0,   # height
            0,                     # colors in palette
            0,                     # reserved
            1,                     # color planes
            32,                    # bits per pixel
            len(png),              # size of image data
            data_offset + len(body)))
        body += png
    header = struct.pack("<HHH", 0, 1, len(sizes))
    out = header + b"".join(entries) + body
    with open(f"{assets}/{dst}", "wb") as f:
        f.write(out)
    # verify by re-opening
    with Image.open(f"{assets}/{dst}") as chk:
        print(dst, "OK", chk.info.get("sizes"))

for src, dst in specs:
    make_ico(src, dst)
