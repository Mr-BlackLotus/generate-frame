"""Logika gambar: deteksi area frame, ekstrak foto utama, tempel ke frame."""
import io

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps

try:  # opsional: hasil lebih bagus untuk foto dengan background rumit
    from rembg import remove as _rembg_remove
except Exception:  # pragma: no cover
    _rembg_remove = None


# --------------------------------------------------------------------------
# Frame
# --------------------------------------------------------------------------
def find_content_rect(frame: Image.Image):
    """Cari persegi panjang putih terbesar di frame (area kosong untuk foto).

    Return (x, y, w, h) dalam piksel frame.
    """
    gray = np.array(frame.convert("L"))
    H, W = gray.shape
    f = 4
    h2, w2 = H // f, W // f
    blocks = (gray[: h2 * f, : w2 * f] >= 242).reshape(h2, f, w2, f).all(axis=(1, 3))

    best = (0, 0, 0, 0, 0)  # area, x, y, w, h
    heights = np.zeros(w2, dtype=int)
    for r in range(h2):
        heights = np.where(blocks[r], heights + 1, 0)
        stack = []  # (start, height)
        for c in range(w2 + 1):
            cur = heights[c] if c < w2 else 0
            start = c
            while stack and stack[-1][1] >= cur:
                s, hh = stack.pop()
                area = hh * (c - s)
                if area > best[0]:
                    best = (area, s, r - hh + 1, c - s, hh)
                start = s
            stack.append((start, cur))
    area, bx, by, bw, bh = best
    if area < 0.1 * h2 * w2:  # frame tidak berlatar putih -> margin default
        return int(W * 0.1), int(H * 0.1), int(W * 0.8), int(H * 0.8)
    pad = 2
    return int(bx * f + pad), int(by * f + pad), int(bw * f - 2 * pad), int(bh * f - 2 * pad)


# --------------------------------------------------------------------------
# Foto utama
# --------------------------------------------------------------------------
def load_rgb(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("RGB")


def _border_color(a: np.ndarray):
    h, w, _ = a.shape
    t = max(2, int(min(h, w) * 0.02))
    strips = np.concatenate(
        [a[:t].reshape(-1, 3), a[-t:].reshape(-1, 3), a[:, :t].reshape(-1, 3), a[:, -t:].reshape(-1, 3)]
    ).astype(int)
    med = np.median(strips, axis=0)
    dev = np.abs(strips - med).max(axis=1)
    return med, bool((dev < 30).mean() > 0.9)


def _clean_mask(mask: np.ndarray) -> np.ndarray:
    """Tutup celah, buang noise kecil, isi lubang."""
    h, w = mask.shape
    k = max(3, int(min(h, w) * 0.012) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return np.zeros_like(m)
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep_min = max(0.0008 * h * w, 0.02 * areas.max())
    out = np.zeros_like(m)
    for i, ar in enumerate(areas, start=1):
        if ar >= keep_min:
            out[labels == i] = 255
    cnts, _ = cv2.findContours(out, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(out)
    cv2.drawContours(filled, cnts, -1, 255, thickness=cv2.FILLED)
    return filled


def _grabcut_mask(a: np.ndarray) -> np.ndarray:
    h, w, _ = a.shape
    s = 400 / max(h, w)
    small = cv2.resize(a, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    sh, sw, _ = small.shape
    m = np.zeros((sh, sw), np.uint8)
    rect = (int(sw * 0.04), int(sh * 0.04), int(sw * 0.92), int(sh * 0.92))
    bgd, fgd = np.zeros((1, 65)), np.zeros((1, 65))
    cv2.grabCut(cv2.cvtColor(small, cv2.COLOR_RGB2BGR), m, rect, bgd, fgd, 4, cv2.GC_INIT_WITH_RECT)
    fg = np.where((m == 1) | (m == 3), 255, 0).astype(np.uint8)
    return cv2.resize(fg, (w, h), interpolation=cv2.INTER_NEAREST)


def extract_subject(im: Image.Image, remove_bg: bool = True):
    """Return (crop_rgb, mask_L, mode) ; mode: 'white' | 'cutout' | 'full'."""
    a = np.array(im)
    h, w, _ = a.shape
    full = (im, Image.new("L", im.size, 255), "full")
    if not remove_bg:
        return full

    med, uniform = _border_color(a)
    mode = "cutout"
    if uniform:
        diff = np.abs(a.astype(int) - med).max(axis=2)
        mask = _clean_mask((diff > 22).astype(np.uint8) * 255)
        if med.min() >= 225:
            mode = "white"
    elif _rembg_remove is not None:
        out = _rembg_remove(im)
        mask = _clean_mask((np.array(out.split()[-1]) > 128).astype(np.uint8) * 255)
    else:
        mask = _clean_mask(_grabcut_mask(a))

    ratio = (mask > 0).mean()
    if ratio < 0.03 or ratio > 0.96:
        return full
    x, y, bw, bh = cv2.boundingRect(mask)
    m = int(max(bw, bh) * 0.01)
    x0, y0 = max(0, x - m), max(0, y - m)
    x1, y1 = min(w, x + bw + m), min(h, y + bh + m)
    crop = im.crop((x0, y0, x1, y1))
    mk = Image.fromarray(mask).crop((x0, y0, x1, y1))
    if mode == "white":
        # samakan background crop dengan putih murni supaya menyatu dengan frame
        arr = np.array(crop).astype(np.float32)
        arr = np.clip(arr * 255.0 / np.maximum(med, 1), 0, 255).astype(np.uint8)
        crop = Image.fromarray(arr)
    return crop, mk, mode


# --------------------------------------------------------------------------
# Komposisi
# --------------------------------------------------------------------------
def compose(frame: Image.Image, rect, subject, padding=0.06, shadow=True) -> Image.Image:
    crop, mask, mode = subject
    x, y, w, h = rect
    aw, ah = w * (1 - 2 * padding), h * (1 - 2 * padding)
    if shadow:
        ah *= 0.97
    cw, ch = crop.size
    s = min(aw / cw, ah / ch, 3.0)
    nw, nh = max(1, int(cw * s)), max(1, int(ch * s))
    crop = crop.resize((nw, nh), Image.LANCZOS)
    mask = mask.resize((nw, nh), Image.LANCZOS)
    px = int(x + (w - nw) / 2)
    py = int(y + (h - nh) / 2)

    canvas = frame.convert("RGB").copy()

    if shadow and mode != "full":
        blur = max(4, int(max(nw, nh) * 0.018))
        layer = Image.new("L", canvas.size, 0)
        layer.paste(mask, (px, py + int(nh * 0.014)))
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        solid = Image.new("L", canvas.size, 0)
        solid.paste(mask, (px, py))
        solid = solid.filter(ImageFilter.GaussianBlur(1.2))
        # bayangan hanya di luar siluet produk
        layer = ImageChops.multiply(layer, ImageChops.invert(solid)).point(lambda v: int(v * 0.30))
        canvas = ImageChops.multiply(canvas, ImageChops.invert(layer).convert("RGB"))

    if mode == "white":
        region = canvas.crop((px, py, px + nw, py + nh))
        canvas.paste(ImageChops.multiply(region, crop), (px, py))
    elif mode == "cutout":
        soft = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
        canvas.paste(crop, (px, py), soft)
    else:  # foto utuh (tidak ada background yang bisa dipisah)
        canvas.paste(crop, (px, py))
    return canvas
