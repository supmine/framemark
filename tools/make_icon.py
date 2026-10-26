"""Draws the Framemark app icon and writes assets/icon.png, icon.icns (macOS) and icon.ico (Windows).

    .venv/bin/python tools/make_icon.py
"""
import math
import os
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SS = 4            # supersampling factor for smooth edges
N = 1024 * SS


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def vgradient(w, h, stops):
    """Vertical gradient through (position, color) stops."""
    im = Image.new("RGB", (1, h))
    px = im.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                px[0, y] = lerp(c0, c1, (t - p0) / (p1 - p0))
                break
    return im.resize((w, h))


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


def draw():
    s = lambda v: round(v * SS)
    icon = Image.new("RGBA", (N, N), (0, 0, 0, 0))

    # macOS grid: 824px tile centered on a 1024 canvas
    tile, off = s(824), s(100)
    bg = vgradient(tile, tile, [(0, (38, 44, 52)), (1, (12, 14, 17))]).convert("RGBA")
    icon.paste(bg, (off, off), rounded_mask(tile, tile, s(185)))

    # the white print: 3:4 card with the caption bar at the bottom
    cw, ch = s(470), s(627)
    cx, cy = (N - cw) // 2, s(186)
    shadow = Image.new("L", (N, N), 0)
    ImageDraw.Draw(shadow).rounded_rectangle([cx, cy + s(18), cx + cw, cy + ch + s(18)], s(14), fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(s(26)))
    icon.paste((0, 0, 0, 255), (0, 0), shadow)
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle([cx, cy, cx + cw, cy + ch], s(14), fill=(250, 250, 248, 255))

    # photo: sunset sky, sun, dark tree line
    pad = s(26)
    pw, ph = cw - pad * 2, s(470)
    px, py = cx + pad, cy + pad
    photo = vgradient(pw, ph, [(0, (235, 120, 40)), (0.55, (214, 82, 30)), (1, (120, 38, 18))]).convert("RGBA")
    pd = ImageDraw.Draw(photo)
    sun_r, sun_y = s(78), round(ph * 0.66)
    glow = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(glow).ellipse([pw // 2 - sun_r * 2, sun_y - sun_r * 2, pw // 2 + sun_r * 2, sun_y + sun_r * 2], fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(s(40)))
    photo.paste((255, 214, 120, 255), (0, 0), glow)
    pd.ellipse([pw // 2 - sun_r, sun_y - sun_r, pw // 2 + sun_r, sun_y + sun_r], fill=(255, 244, 200, 255))
    hill = [(0, ph)]
    for i in range(0, pw + 1, s(2)):
        t = i / pw
        y = ph * (0.72 + 0.025 * math.sin(t * 7.5 + 0.6) + 0.012 * math.sin(t * 29) + 0.006 * math.sin(t * 71))
        hill.append((i, y))
    hill.append((pw, ph))
    pd.polygon(hill, fill=(22, 10, 8, 255))
    icon.paste(photo, (px, py))

    # caption bar: two text lines left, logo block | two lines right
    by = py + ph + s(40)
    ink, grey = (24, 24, 24, 255), (170, 170, 170, 255)
    lh = s(13)
    d.rounded_rectangle([px, by, px + s(130), by + lh], lh // 2, fill=ink)
    d.rounded_rectangle([px, by + s(30), px + s(92), by + s(30) + s(10)], s(5), fill=grey)
    rx = cx + cw - pad
    d.rounded_rectangle([rx - s(78), by, rx, by + lh], lh // 2, fill=ink)
    d.rounded_rectangle([rx - s(78), by + s(30), rx - s(14), by + s(30) + s(10)], s(5), fill=grey)
    d.line([(rx - s(100), by - s(6)), (rx - s(100), by + s(46))], fill=(205, 205, 205, 255), width=s(3))
    d.rounded_rectangle([rx - s(196), by + s(8), rx - s(118), by + s(32)], s(5), fill=ink)

    return icon.resize((1024, 1024), Image.LANCZOS)


def main():
    icon = draw()
    png = os.path.join(ASSETS, "icon.png")
    icon.save(png)
    icon.save(os.path.join(ASSETS, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = os.path.join(tmp, "icon.iconset")
            os.makedirs(iconset)
            for size in (16, 32, 128, 256, 512):
                icon.resize((size, size), Image.LANCZOS).save(os.path.join(iconset, f"icon_{size}x{size}.png"))
                icon.resize((size * 2, size * 2), Image.LANCZOS).save(os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
            subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(ASSETS, "icon.icns")], check=True)
    print("wrote", png)


if __name__ == "__main__":
    main()
