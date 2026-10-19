"""Рисует значки приложения (орб Scout на тёмном фоне). Запуск: python scripts/make_icons.py"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "leadscout" / "web" / "static" / "icons"
BG = np.array([6, 6, 12], dtype=float)
STOPS = [(0.0, (142, 123, 255)), (0.38, (79, 216, 232)), (0.7, (255, 178, 122)), (1.0, (142, 123, 255))]


def ramp(t: np.ndarray) -> np.ndarray:
    out = np.zeros(t.shape + (3,))
    for (a, ca), (b, cb) in zip(STOPS, STOPS[1:], strict=False):
        m = (t >= a) & (t <= b)
        k = ((t - a) / (b - a))[..., None]
        out = np.where(m[..., None], np.array(ca) * (1 - k) + np.array(cb) * k, out)
    return out


def render(size: int, scale: float) -> Image.Image:
    """scale: доля стороны, которую занимает орб (у maskable меньше, чтобы не обрезало)."""
    ss = 2
    n = size * ss
    y, x = np.mgrid[0:n, 0:n].astype(float)
    cx = cy = n / 2
    dx, dy = x - cx, y - cy
    r = np.hypot(dx, dy)
    R = n * scale / 2
    ang = (np.arctan2(dy, dx) + math.pi) / (2 * math.pi)
    canvas = np.broadcast_to(BG, (n, n, 3)).copy()
    # мягкое свечение
    glow = np.clip(1 - (r - R * 0.7) / (R * 1.1), 0, 1) ** 2.2
    canvas += glow[..., None] * ramp(ang) * 0.55
    # диск
    disc = np.clip((R - r) / 2.0, 0, 1)
    iris = np.array([126, 107, 255.0])
    deep = np.array([34, 24, 130.0])
    cyan = np.array([79, 216, 232.0])
    amber = np.array([255, 178, 122.0])
    core = np.clip(r / R, 0, 1)[..., None]
    base = iris * (1 - core) + deep * core  # от яркого центра к тёмному краю
    low = np.clip(1 - np.hypot(dx - R * 0.35, dy - R * 0.45) / (R * 1.0), 0, 1)[..., None] ** 1.3
    base = base * (1 - low) + cyan * low  # бирюзовый отсвет снизу справа
    rim = np.clip(1 - np.hypot(dx + R * 0.2, dy - R * 1.05) / (R * 0.7), 0, 1)[..., None] ** 1.5
    base = base * (1 - rim * 0.7) + amber * rim * 0.7  # тёплая кромка
    hl = np.clip(1 - np.hypot(dx + R * 0.34, dy + R * 0.38) / (R * 0.55), 0, 1)[..., None] ** 1.5
    base = base + hl * 190
    canvas = canvas * (1 - disc[..., None]) + base * disc[..., None]
    img = Image.fromarray(np.clip(canvas, 0, 255).astype("uint8"), "RGB")
    return img.resize((size, size), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.3))


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs><radialGradient id="g" cx=".34" cy=".3" r=".9"><stop offset="0" stop-color="#fff" stop-opacity=".9"/><stop offset=".25" stop-color="#8E7BFF"/><stop offset=".7" stop-color="#4FD8E8"/><stop offset="1" stop-color="#FFB27A"/></radialGradient></defs>
<rect width="64" height="64" rx="14" fill="#06060C"/><circle cx="32" cy="32" r="19" fill="url(#g)"/></svg>
"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    render(192, 0.62).save(OUT / "icon-192.png", optimize=True)
    render(512, 0.62).save(OUT / "icon-512.png", optimize=True)
    render(512, 0.46).save(OUT / "maskable-512.png", optimize=True)
    render(180, 0.62).save(OUT / "apple-touch-icon.png", optimize=True)
    (OUT / "favicon.svg").write_text(SVG, encoding="utf-8")
    print("готово:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
