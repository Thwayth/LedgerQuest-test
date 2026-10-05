"""Мем-карточки в стиле канала: лев-талисман, мемные графики, «так/надо так», карточка факта.

Всё рисуется локально (Pillow): никаких чужих картинок и чужих авторских прав.
"""
from __future__ import annotations

import io
import random
import textwrap
from pathlib import Path

import matplotlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SIZE = 1080
AMBER, CREAM, RED = (245, 165, 36), (241, 230, 204), (232, 90, 60)
FONT_PATH = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans-Bold.ttf"
DEFAULT_MASCOT = Path(__file__).resolve().parents[1] / "assets" / "mascot.png"
TEMPLATES = ["lion", "chart:pump_dump", "chart:bleed_then_pump", "chart:sideways", "chart:staircase", "two_panel"]
CHART_MARKERS = {"pump_dump": "Я купил", "bleed_then_pump": "Я продал", "staircase": "Я здесь", "sideways": None}


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_PATH), size)


def _background(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    d = np.sqrt((xx - SIZE / 2) ** 2 + (yy - SIZE / 2) ** 2) / (SIZE * 0.75)
    base = (14 - 9 * np.clip(d, 0, 1)).astype(np.uint8)  # лёгкая виньетка
    img = Image.fromarray(np.dstack([base, base, base]), "RGB")
    dr = ImageDraw.Draw(img)
    for _ in range(260):
        x, y = rng.integers(0, SIZE, 2)
        r = float(rng.uniform(0.6, 2.0))
        c = (245, 165, 36) if rng.random() < 0.2 else (243, 234, 216)
        a = float(rng.uniform(0.25, 0.9))
        dr.ellipse([x - r, y - r, x + r, y + r], fill=tuple(int(14 + (v - 14) * a) for v in c))
    return img


def _mascot(height: int, path: Path | str | None = None, feather: int = 36) -> Image.Image | None:
    p = Path(path) if path else DEFAULT_MASCOT
    if not p.exists():
        return None
    im = Image.open(p).convert("RGBA")
    w = int(im.width * height / im.height)
    im = im.resize((w, height), Image.LANCZOS)
    a = np.array(im).astype(np.float32)
    h, w = a.shape[:2]
    ramp = lambda n: np.clip(np.minimum(np.arange(n), np.arange(n)[::-1]) / feather, 0, 1)
    a[..., 3] *= ramp(h)[:, None] * ramp(w)[None, :]  # мягкие края со всех сторон
    return Image.fromarray(a.astype(np.uint8), "RGBA")


def _fit_text(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int], max_size: int, min_size: int = 26, stroke: int = 0):
    """Подбирает размер шрифта и переносы так, чтобы текст влез в box. Возвращает (lines, font, line_height)."""
    x0, y0, x1, y1 = box
    for size in range(max_size, min_size - 1, -2):
        f = _font(size)
        avg = (draw.textlength(text, font=f) / max(1, len(text))) or size * 0.6
        width_chars = max(8, int((x1 - x0) / avg))
        lines = textwrap.wrap(text, width=width_chars, break_long_words=False) or [text]
        lh = int(size * 1.18)
        if len(lines) * lh <= (y1 - y0) and all(draw.textlength(l, font=f) <= (x1 - x0) for l in lines):
            return lines, f, lh
    f = _font(min_size)
    return textwrap.wrap(text, width=24) or [text], f, int(min_size * 1.18)


def _caption(draw, text: str, box, max_size: int, fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke=6, upper=True, valign="middle"):
    text = text.upper() if upper else text
    lines, f, lh = _fit_text(draw, text, box, max_size, stroke=stroke)
    x0, y0, x1, y1 = box
    total = len(lines) * lh
    y = y0 + (0 if valign == "top" else (y1 - y0 - total) / 2)
    for ln in lines:
        w = draw.textlength(ln, font=f)
        draw.text(((x0 + x1 - w) / 2, y), ln, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        y += lh


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _chart_path(pattern: str, seed: int, n: int = 70) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, n)
    if pattern == "pump_dump":
        y = np.where(t < 0.55, 0.12 + 0.78 * (t / 0.55) ** 2.2, 0.9 - 0.8 * (np.clip(t - 0.55, 0, None) / 0.45) ** 0.7)
    elif pattern == "bleed_then_pump":
        y = np.where(t < 0.62, 0.72 - 0.55 * (t / 0.62), 0.17 + 0.78 * (np.clip(t - 0.62, 0, None) / 0.38) ** 1.6)
    elif pattern == "staircase":
        y = np.where(t < 0.7, 0.15 + 0.12 * np.floor(t / 0.7 * 6), 0.15 + 0.12 * 6 - 0.8 * np.clip((t - 0.7) / 0.1, 0, 1))
    else:  # sideways
        y = 0.5 + 0.04 * np.sin(t * 22)
    y = y + rng.normal(0, 0.012 if pattern != "sideways" else 0.006, n)
    return np.clip(y, 0.04, 0.96)


def _draw_chart(img: Image.Image, box, pattern: str, seed: int) -> None:
    x0, y0, x1, y1 = box
    dr = ImageDraw.Draw(img, "RGBA")
    y = _chart_path(pattern, seed)
    pts = [(x0 + (x1 - x0) * i / (len(y) - 1), y1 - (y1 - y0) * v) for i, v in enumerate(y)]
    for k in range(1, 5):  # сетка
        gy = y0 + (y1 - y0) * k / 5
        dr.line([(x0, gy), (x1, gy)], fill=(255, 255, 255, 22), width=1)
    dr.polygon(pts + [(x1, y1), (x0, y1)], fill=(245, 165, 36, 40))
    dr.line(pts, fill=AMBER + (255,), width=7, joint="curve")
    label = CHART_MARKERS[pattern]
    if label:
        i = {"pump_dump": 38, "bleed_then_pump": 43, "staircase": 48}[pattern]
        px, py = pts[i]
        dr.ellipse([px - 13, py - 13, px + 13, py + 13], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=3)
        f = _font(38)
        w = dr.textlength(label, font=f)
        bx = min(max(px - w / 2 - 14, x0), x1 - w - 28)
        by = py - 90 if pattern != "bleed_then_pump" else py + 34
        dr.rounded_rectangle([bx, by, bx + w + 28, by + 58], 14, fill=(255, 255, 255, 235))
        dr.text((bx + 14, by + 7), label, font=f, fill=(0, 0, 0, 255))


def _two_panel(img: Image.Image, bad: str, good: str, mascot_path) -> None:
    dr = ImageDraw.Draw(img, "RGBA")
    for k, (txt, ok) in enumerate(((bad, False), (good, True))):
        y0 = 40 + k * 520
        box = (40, y0, SIZE - 40, y0 + 480)
        dr.rounded_rectangle(box, 28, fill=(20, 20, 20, 255) if ok else (14, 12, 12, 255),
                             outline=(AMBER if ok else (110, 60, 50)) + (255,), width=6)
        m = _mascot(300, mascot_path, feather=26)
        if m is not None:
            if not ok:  # «плохой» лев тусклый
                a = np.array(m).astype(np.float32)
                a[..., :3] *= 0.45
                m = Image.fromarray(a.astype(np.uint8), "RGBA")
            img.paste(m, (70, y0 + 90), m)
        cx, cy, r = 920, y0 + 70, 38  # значок ✓ / ✗
        col = AMBER if ok else RED
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col + (255,), width=7)
        if ok:
            dr.line([(cx - 18, cy), (cx - 5, cy + 14), (cx + 20, cy - 16)], fill=col + (255,), width=8, joint="curve")
        else:
            dr.line([(cx - 16, cy - 16), (cx + 16, cy + 16)], fill=col + (255,), width=8)
            dr.line([(cx - 16, cy + 16), (cx + 16, cy - 16)], fill=col + (255,), width=8)
        _caption(dr, txt, (400, y0 + 110, SIZE - 80, y0 + 450), 64, fill=CREAM if ok else (170, 160, 150), stroke=0, upper=False)


def render_meme(template: str, top: str, bottom: str, seed: int = 0, mascot_path=None) -> bytes:
    """template: lion | chart:<pattern> | two_panel (для него top = «плохо», bottom = «хорошо»)."""
    img = _background(seed)
    dr = ImageDraw.Draw(img)
    if template == "two_panel":
        _two_panel(img, top, bottom, mascot_path)
        return _png(img)
    if template.startswith("chart:"):
        _draw_chart(img, (90, 300, SIZE - 90, 800), template.split(":", 1)[1], seed)
    else:
        m = _mascot(640, mascot_path)
        if m is not None:
            img.paste(m, ((SIZE - m.width) // 2, 230), m)
    _caption(dr, top, (50, 24, SIZE - 50, 250), 84, valign="top")
    _caption(dr, bottom, (50, 830, SIZE - 50, 1056), 84, valign="middle")
    return _png(img)


def render_card(title: str, text: str, seed: int = 0, mascot_path=None) -> bytes:
    """Карточка факта/шутки: рамка, плашка-заголовок, крупный текст, лев в углу."""
    img = _background(seed)
    dr = ImageDraw.Draw(img, "RGBA")
    dr.rounded_rectangle([44, 44, SIZE - 44, SIZE - 44], 34, outline=AMBER + (255,), width=5)
    f = _font(46)
    w = dr.textlength(title, font=f)
    dr.rounded_rectangle([90, 90, 90 + w + 56, 170], 22, fill=AMBER + (255,))
    dr.text((118, 100), title, font=f, fill=(15, 15, 15))
    m = _mascot(330, mascot_path, feather=30)
    if m is not None:
        img.paste(m, (SIZE - m.width - 70, SIZE - 330 - 70), m)
    _caption(dr, text, (96, 210, SIZE - 96, 700), 76, fill=CREAM, stroke=0, upper=False, valign="top")
    return _png(img)


def prepare_user_image(path: str | Path, max_side: int = 1600) -> bytes:
    """Картинка пользователя из папки -> PNG/JPEG, который точно примет Telegram."""
    im = Image.open(path)
    im.seek(0)
    im = im.convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()
