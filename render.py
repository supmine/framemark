"""Frame rendering engine: reads EXIF, draws the white/black caption frame with Pillow.

No UI code here, so it can also be used from the command line:
    python render.py photo1.jpg photo2.jpg --shape 4:5 --out ./out
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

try:  # optional: open HEIC/HEIF straight from iPhone / newer cameras
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

# inside a PyInstaller build the bundled files live under sys._MEIPASS
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOGO = os.path.join(HERE, "assets", "logo-sony.webp")

# macOS: Helvetica Neue from the system. Windows / Linux: bundled Inter.
HELVETICA = "/System/Library/Fonts/HelveticaNeue.ttc"
INTER = {w: os.path.join(HERE, "assets", "fonts", f"Inter-{n}.ttf") for w, n in ((400, "Regular"), (600, "SemiBold"))}
if os.path.exists(HELVETICA):
    FONT_FACES = {400: (HELVETICA, 0), 500: (HELVETICA, 10), 600: (HELVETICA, 1), 700: (HELVETICA, 1)}
else:
    FONT_FACES = {400: (INTER[400], 0), 500: (INTER[600], 0), 600: (INTER[600], 0), 700: (INTER[600], 0)}

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".heif")


# ---------------------------------------------------------------- options

@dataclass
class Options:
    shape: str = "4:5"         # "orig" | "1:1" | "4:5" (Instagram feed)
    layout: str = "lr"         # "lr" settings left / logo right · "pro" logo left / settings right · "center"
    color: str = "white"       # key of PALETTE
    pad: float = 0.035         # border thickness relative to photo size
    ts: float = 1.3            # caption text scale
    show_date: bool = True
    show_lens: bool = True
    show_focal: bool = False   # focal length in the settings line
    show_eq: bool = False      # 35mm-equivalent focal length (APS-C bodies)
    alpha_name: bool = False   # ILCE-6700 -> α6700
    use_logo: bool = True
    quality: str = "jpg95"     # "jpg95" | "jpg100" | "png"


@dataclass
class Meta:
    found: bool = False
    make: str = ""
    model: str = ""
    lens: str = ""
    settings: str = ""         # typed override; empty -> built from the values below
    date: str = ""
    mm: float | None = None
    mm35: float | None = None
    f: float | None = None
    t: float | None = None
    iso: float | None = None

    def settings_text(self, o: "Options") -> str:
        if self.settings:
            return self.settings
        focal = ""
        if o.show_focal and self.mm:
            focal = f"{round(self.mm)}mm"
            if o.show_eq and self.mm35 and round(self.mm35) != round(self.mm):
                focal += f" ({round(self.mm35)}mm eq.)"
        parts = [f"ISO {int(self.iso)}" if self.iso else "", fmt_f(self.f), fmt_shutter(self.t), focal]
        return " ".join(p for p in parts if p)


# ---------------------------------------------------------------- EXIF

SONY_NAMES = {
    "ILCE-1": "α1", "ILCE-1M2": "α1 II", "ILCE-9": "α9", "ILCE-9M2": "α9 II", "ILCE-9M3": "α9 III",
    "ILCE-7": "α7", "ILCE-7M2": "α7 II", "ILCE-7M3": "α7 III", "ILCE-7M4": "α7 IV", "ILCE-7M5": "α7 V",
    "ILCE-7R": "α7R", "ILCE-7RM2": "α7R II", "ILCE-7RM3": "α7R III", "ILCE-7RM3A": "α7R IIIA",
    "ILCE-7RM4": "α7R IV", "ILCE-7RM4A": "α7R IVA", "ILCE-7RM5": "α7R V",
    "ILCE-7S": "α7S", "ILCE-7SM2": "α7S II", "ILCE-7SM3": "α7S III",
    "ILCE-7C": "α7C", "ILCE-7CM2": "α7C II", "ILCE-7CR": "α7CR",
    "ILCE-6000": "α6000", "ILCE-6100": "α6100", "ILCE-6300": "α6300", "ILCE-6400": "α6400",
    "ILCE-6500": "α6500", "ILCE-6600": "α6600", "ILCE-6700": "α6700", "ILCE-5100": "α5100",
    "ILME-FX3": "FX3", "ILME-FX30": "FX30", "ZV-E10": "ZV-E10", "ZV-E10M2": "ZV-E10 II", "ZV-E1": "ZV-E1",
}

BRANDS = r"SONY|CANON|NIKON|FUJIFILM|PANASONIC|OLYMPUS|OM SYSTEM|LEICA|RICOH|PENTAX|HASSELBLAD|SIGMA|APPLE|GOOGLE|SAMSUNG|DJI"


def crop_factor(model: str) -> float:
    m = (model or "").strip().upper()
    # Sony APS-C bodies; full-frame and anything unknown fall back to 1.0
    return 1.5 if re.match(r"^(ILCE-[3456]\d{3}|NEX-|ZV-E10|ILME-FX30)", m) else 1.0


def pretty_model(model: str) -> str:
    return SONY_NAMES.get((model or "").strip().upper(), model)


def _num(v):
    if v is None:
        return None
    if isinstance(v, (tuple, list)):
        if not v:
            return None
        if len(v) == 2 and all(isinstance(x, int) for x in v):
            return v[0] / v[1] if v[1] else None
        v = v[0]
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _str(v) -> str:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return (v or "").replace("\x00", "").strip() if isinstance(v, str) else ""


def fmt_shutter(t):
    if not t or t <= 0:
        return ""
    if t >= 1:
        return f"{round(t, 1):g}s"
    return f"1/{round(1 / t)}s"


def fmt_f(f):
    return f"ƒ/{round(f, 1):g}" if f else ""


def fmt_date(d: datetime | None) -> str:
    if not d:
        return ""
    h = d.hour % 12 or 12
    ap = "PM" if d.hour >= 12 else "AM"
    return f"{d.day} {d.strftime('%b')} {d.year} at {h}:{d.minute:02d} {ap}"


def read_meta(img: Image.Image) -> Meta:
    try:
        exif = img.getexif()
    except Exception:
        return Meta()
    try:
        sub = exif.get_ifd(0x8769)
    except Exception:
        sub = {}
    get = lambda tag: sub.get(tag, exif.get(tag))

    make = _str(exif.get(0x010F))
    model = _str(exif.get(0x0110))
    make = re.sub(r"\s+(CORPORATION|CORP\.?|CO\.,? ?LTD\.?|INC\.?)$", "", make, flags=re.I).strip().upper()
    if not make:
        m = re.match(rf"^({BRANDS})\b\s*", model, re.I)
        if m:
            make, model = m.group(1).upper(), model[m.end():].strip()
    elif model.upper().startswith(make + " "):
        model = model[len(make):].strip()

    lens = _str(get(0xA434)) or _str(get(0xFDEA))
    f = _num(get(0x829D))
    t = _num(get(0x829A))
    iso = _num(get(0x8827))
    mm = _num(get(0x920A))
    mm35 = _num(get(0xA405))
    if mm and not mm35:
        mm35 = mm * crop_factor(model)

    date = None
    raw = _str(get(0x9003)) or _str(get(0x9004)) or _str(exif.get(0x0132))
    if raw:
        try:
            date = datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
        except ValueError:
            date = None

    return Meta(
        found=bool(model or f or t),
        make=make,
        model=model,
        lens=lens,
        date=fmt_date(date),
        mm=mm, mm35=mm35, f=f, t=t, iso=iso,
    )


# ---------------------------------------------------------------- loading

PREVIEW_LONG = 1800


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA", "P", "PA"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        return bg
    return img.convert("RGB")


@dataclass
class Photo:
    """Keeps only a preview in memory; the full-resolution pixels are read again when saving."""
    path: str
    size: tuple                   # (w, h) after orientation
    meta: Meta
    icc: bytes | None
    exif: bytes | None            # original EXIF, copied into the output
    preview: Image.Image

    def preview_source(self) -> Image.Image:
        return self.preview

    def full_image(self) -> Image.Image:
        with Image.open(self.path) as src:
            src.load()
            return _to_rgb(ImageOps.exif_transpose(src))


def load_photo(path: str) -> Photo:
    with Image.open(path) as src:
        meta = read_meta(src)
        try:
            exif_bytes = src.getexif().tobytes()
            orient = src.getexif().get(0x0112, 1)
        except Exception:
            exif_bytes, orient = None, 1
        icc = src.info.get("icc_profile")
        w, h = src.size
        if orient in (5, 6, 7, 8):
            w, h = h, w
        try:
            src.draft("RGB", (PREVIEW_LONG, PREVIEW_LONG))  # fast JPEG decode at reduced size
        except Exception:
            pass
        src.load()
        prev = _to_rgb(ImageOps.exif_transpose(src))
        prev.thumbnail((PREVIEW_LONG, PREVIEW_LONG), Image.LANCZOS)
    return Photo(path=path, size=(w, h), meta=meta, icc=icc, exif=exif_bytes, preview=prev)


# ---------------------------------------------------------------- logo

LOGO_UPSCALE = 6


def build_logo_mask(path: str) -> Image.Image | None:
    """Any logo (dark on white, or on transparent) -> trimmed, high-resolution 'L' alpha mask.

    The source is enlarged once with a soft re-threshold, which rebuilds clean anti-aliased
    edges at ~6x the original size. Drawing then only ever shrinks the mask, so the logo
    stays sharp even on 60MP frames instead of being blown up from a small bitmap.
    """
    with Image.open(path) as im:
        im.load()
        im = im.convert("RGBA")
    alpha = im.getchannel("A")
    if alpha.getextrema()[0] >= 250:
        lum = im.convert("RGB").convert("L")
        mask = lum.point(lambda v: max(0, min(255, round((235 - v) * 255 / 200))))
    else:
        mask = alpha
    bbox = mask.point(lambda v: 255 if v > 24 else 0).getbbox()
    if not bbox:
        return None
    mask = mask.crop(bbox)
    target_w = min(8000, max(mask.width, mask.width * LOGO_UPSCALE))
    if target_w > mask.width:
        big = mask.resize((target_w, round(mask.height * target_w / mask.width)), Image.BICUBIC)
        mask = big.point(lambda v: max(0, min(255, round((v - 88) * 255 / 80))))
        bbox = mask.getbbox()
        if bbox:
            mask = mask.crop(bbox)
    return mask


# ---------------------------------------------------------------- drawing

def _pal(bg, dark=True):
    if dark:
        return {"bg": bg, "main": (18, 18, 18), "muted": (128, 128, 128), "rule": (200, 200, 200), "logo": (0, 0, 0)}
    return {"bg": bg, "main": (242, 242, 242), "muted": (150, 150, 150), "rule": (72, 72, 72), "logo": (255, 255, 255)}


PALETTE = {
    "white": _pal((255, 255, 255)),
    "ivory": _pal((249, 246, 238)),
    "gray": _pal((230, 230, 230)),
    "sakura": _pal((252, 236, 240)),
    "latte": _pal((237, 225, 210)),
    "black": _pal((12, 12, 12), dark=False),
}


@lru_cache(maxsize=64)
def _font(weight: int, size_q: int) -> ImageFont.FreeTypeFont:
    path, index = FONT_FACES[weight]
    return ImageFont.truetype(path, max(1, size_q / 4), index=index)


def font(weight, px):
    return _font(weight, max(4, round(px * 4)))


def bar_scale(ts):
    return 1 + (ts - 1) * 0.6


@dataclass
class Geom:
    pad: int
    bar: int
    bw: int
    bh: int
    W: int
    H: int
    ox: int
    oy: int


def geom(iw, ih, o: Options) -> Geom:
    # square / 4:5 size the caption from the long side, so wide photos don't get tiny text
    s = min(iw, ih) if o.shape == "orig" else max(iw, ih) * 0.85
    pad = round(s * o.pad)
    bar = round(s * 0.11 * bar_scale(o.ts) * (1.45 if o.layout == "center" else 1))
    bw, bh = iw + pad * 2, ih + pad + bar
    W, H = bw, bh
    if o.shape == "1:1":
        W = H = max(bw, bh)
    elif o.shape == "4:5":
        r = 4 / 5
        if bw / bh > r:
            W, H = bw, round(bw / r)
        else:
            W, H = round(bh * r), bh
    return Geom(pad, bar, bw, bh, W, H, round((W - bw) / 2), round((H - bh) / 2))


def output_size(photo: Photo, o: Options):
    g = geom(*photo.size, o)
    return g.W, g.H


def draw_frame(photo: Photo, meta: Meta, o: Options, logo_mask: Image.Image | None, k: float = 1.0) -> Image.Image:
    """Lay out in full-resolution units, then draw everything scaled by k (k<1 for previews)."""
    iw, ih = photo.size
    g = geom(iw, ih, o)
    c = PALETTE.get(o.color, PALETTE["white"])
    S = lambda v: v * k

    canvas = Image.new("RGB", (max(1, round(g.W * k)), max(1, round(g.H * k))), c["bg"])
    pw, ph = round(S(iw)), round(S(ih))
    src = photo.full_image() if k >= 0.999 else photo.preview_source()
    if src.size != (pw, ph):
        src = src.resize((pw, ph), Image.LANCZOS)
    canvas.paste(src, (round(S(g.ox + g.pad)), round(S(g.oy + g.pad))))

    d = ImageDraw.Draw(canvas)
    bar = g.bar
    cy = g.oy + g.pad + ih + bar / 2

    settings = meta.settings_text(o)
    date = meta.date if o.show_date else ""
    model = (pretty_model(meta.model) if o.alpha_name else meta.model) or ""
    lens = meta.lens if o.show_lens else ""
    logo = logo_mask if (o.use_logo and logo_mask is not None) else None
    make = "" if logo else (meta.make or "SONY")
    ratio = logo.width / logo.height if logo else 1

    def meas(t, w, size):
        return font(w, size * 10).getlength(t) / 10 if t else 0

    def text(x, y, t, w, size, fill, anchor):
        if t:
            d.text((S(x), S(y)), t, font=font(w, S(size)), fill=fill, anchor=anchor)

    def draw_mark(x_right, y_mid, fm):
        """Logo (or brand text) whose right edge sits at x_right."""
        if logo:
            lh = fm * 0.78
            lw = lh * ratio
            th, tw = max(1, round(S(lh))), max(1, round(S(lw)))
            m = logo.resize((tw, th), Image.LANCZOS)  # always a downscale from the hi-res mask
            canvas.paste(Image.new("RGB", (tw, th), c["logo"]), (round(S(x_right - lw)), round(S(y_mid - lh / 2))), m)
        else:
            text(x_right, y_mid, make, 700, fm, c["main"], "rm")

    def mark_width(fm):
        return fm * 0.78 * ratio if logo else meas(make, 700, fm)

    tf = o.ts / bar_scale(o.ts)

    if o.layout == "center":
        b1, b2, bm = bar * 0.11 * tf, bar * 0.085 * tf, bar * 0.2 * tf
        line3 = "  ·  ".join(x for x in (model, lens, date) if x)
        avail = g.bw - g.pad * 2
        need = max(meas(settings, 600, b1), meas(line3, 400, b2), mark_width(bm)) + bar * 0.3
        s = 1.0 if need <= avail else max(0.45, avail / need)
        f1, f2, fm = b1 * s, b2 * s, bm * s
        cx = g.ox + g.bw / 2
        rows = [("mark", fm * 0.78)] + ([("s", f1)] if settings else []) + ([("l", f2)] if line3 else [])
        gap = bar * 0.075 * s
        total = sum(h for _, h in rows) + gap * (len(rows) - 1)
        y = cy - total / 2
        for kind, h in rows:
            mid = y + h / 2
            if kind == "mark":
                draw_mark(cx + mark_width(fm) / 2, mid, fm)
            elif kind == "s":
                text(cx, mid, settings, 600, f1, c["main"], "mm")
            else:
                text(cx, mid, line3, 400, f2, c["muted"], "mm")
            y += h + gap
        return canvas

    b1, b2, bm = bar * 0.16 * tf, bar * 0.115 * tf, bar * 0.24 * tf

    def widths(s):
        sw = max(meas(settings, 600, b1 * s), meas(date, 400, b2 * s))
        rcol = max(meas(model, 600, b1 * s), meas(lens, 400, b2 * s))
        mk = mark_width(bm * s)
        sep = bar * 0.24 * s if (model or lens) else 0
        return sw, rcol, mk, sep, sw + rcol + mk + sep + bar * 0.5

    s = 1.0
    sw, rcol, mk, sep, total = widths(1)
    avail = g.bw - g.pad * 2
    if total > avail:
        s = max(0.45, avail / total)
        sw, rcol, mk, sep, total = widths(s)
    f1, f2, fm, gap = b1 * s, b2 * s, bm * s, bar * 0.07 * s

    def two_lines(x, a, b, align):
        h = "l" if align == "left" else "r"
        if a and b:
            text(x, cy - gap / 2, a, 600, f1, c["main"], h + "s")
            text(x, cy + gap / 2 + f2 * 0.78, b, 400, f2, c["muted"], h + "s")
        elif a or b:
            text(x, cy, a or b, 600 if a else 400, f1 if a else f2, c["main"] if a else c["muted"], h + "m")

    def camera_block(x_left):
        """[mark] | model / lens, starting at x_left; returns nothing."""
        draw_mark(x_left + mk, cy, fm)
        if model or lens:
            dx = x_left + mk + sep / 2
            lw = max(1, round(S(max(1, bar * 0.008))))
            d.line([(S(dx), S(cy - bar * 0.2 * s)), (S(dx), S(cy + bar * 0.2 * s))], fill=c["rule"], width=lw)
            two_lines(x_left + mk + sep, model, lens, "left")

    left = g.ox + g.pad
    right = g.ox + g.bw - g.pad
    if o.layout == "pro":
        camera_block(left)
        two_lines(right, settings, date, "right")
    else:
        two_lines(left, settings, date, "left")
        camera_block(right - rcol - sep - mk)
    return canvas


# ---------------------------------------------------------------- saving

def out_name(path: str, o: Options) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    suffix = "" if o.shape == "orig" else "-" + o.shape.replace(":", "x")
    ext = "png" if o.quality == "png" else "jpg"
    return f"{stem}-frame{suffix}.{ext}"


def save_framed(photo: Photo, meta: Meta, o: Options, logo_mask, out_dir: str, keep_exif=True) -> str:
    img = draw_frame(photo, meta, o, logo_mask, 1.0)
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, out_name(photo.path, o))
    kw = {}
    if photo.icc:
        kw["icc_profile"] = photo.icc
    if keep_exif and photo.exif:
        try:
            e = Image.Exif()
            e.load(photo.exif)
            e[0x0112] = 1  # pixels are already upright
            kw["exif"] = e.tobytes()
        except Exception:
            pass
    if o.quality == "png":
        img.save(dest, "PNG", optimize=False, **kw)
    else:
        q = 100 if o.quality == "jpg100" else 95
        img.save(dest, "JPEG", quality=q, subsampling=0, optimize=True, **kw)
    return dest


# ---------------------------------------------------------------- CLI

def _cli():
    import argparse

    ap = argparse.ArgumentParser(description="Add an EXIF caption frame to photos")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default="framed")
    ap.add_argument("--shape", choices=["orig", "1:1", "4:5"], default="4:5")
    ap.add_argument("--color", choices=list(PALETTE), default="white")
    ap.add_argument("--layout", choices=["lr", "pro", "center"], default="lr")
    ap.add_argument("--quality", choices=["jpg95", "jpg100", "png"], default="jpg95")
    ap.add_argument("--logo", default=DEFAULT_LOGO)
    ap.add_argument("--no-logo", action="store_true")
    ap.add_argument("--alpha", action="store_true", help="show Sony bodies as α names")
    a = ap.parse_args()
    o = Options(shape=a.shape, color=a.color, layout=a.layout, quality=a.quality, use_logo=not a.no_logo, alpha_name=a.alpha)
    mask = None if a.no_logo or not os.path.exists(a.logo) else build_logo_mask(a.logo)
    for f in a.files:
        p = load_photo(f)
        print(save_framed(p, p.meta, o, mask, a.out))


if __name__ == "__main__":
    _cli()
