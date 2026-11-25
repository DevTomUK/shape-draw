"""Listing icon: an extruded D-shaped outline in the tool's own colours (no Blender logo).

    python listing/make_icon.py        -> listing/icon.png (256 x 256)

Needs Pillow. The icon is CC0 (platform rule for binary assets).
"""
import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
SIZE, SS = 256, 8           # final size, supersampling
S = SIZE * SS

BG_TOP, BG_BOT = (44, 47, 56), (24, 26, 32)
ORANGE = (255, 158, 38)
WALL_LIGHT, WALL_DARK = (196, 110, 30), (120, 64, 20)
TOP = (255, 186, 92)
HANDLE = (140, 204, 255)
WHITE = (255, 255, 255)
GREEN = (90, 255, 115)


def outline():
    """D shape: a square on the left, a half circle on the right (local units)."""
    pts = [(-1.0, -1.0), (0.0, -1.0)]
    for k in range(1, 24):
        a = -math.pi / 2 + math.pi * k / 24
        pts.append((math.cos(a), math.sin(a)))
    pts += [(0.0, 1.0), (-1.0, 1.0)]
    return pts


def project(x, y, z):
    """Simple oblique 3/4 view, centred in the icon."""
    sx = (x * 0.92 - y * 0.42) * S * 0.29 + S * 0.5
    sy = (-(x * 0.22 + y * 0.46) - z * 0.95) * S * 0.29 + S * 0.62
    return sx, sy


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg = Image.new("RGB", (S, S))
    bd = ImageDraw.Draw(bg)
    for y in range(S):
        t = y / S
        bd.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT, strict=True)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.2), fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)

    ring = outline()
    h = 0.85
    base = [project(x, y, 0) for x, y in ring]
    top = [project(x, y, h) for x, y in ring]
    n = len(ring)

    # walls, back to front (painter's order by depth of the edge midpoint)
    walls = []
    for i in range(n):
        j = (i + 1) % n
        (x0, y0), (x1, y1) = ring[i], ring[j]
        nx, ny = y1 - y0, -(x1 - x0)  # outward normal for a CCW ring
        if nx * 0.42 + ny * 0.92 > 0:  # facing away from the viewer
            continue
        depth = (x0 + x1) * 0.22 + (y0 + y1) * 0.46
        light = max(0.0, min(1.0, 0.5 - (nx - ny) * 0.35))
        col = tuple(int(a + (b - a) * light) for a, b in zip(WALL_DARK, WALL_LIGHT, strict=True))
        walls.append((depth, [base[i], base[j], top[j], top[i]], col))
    for _depth, quad, col in sorted(walls, key=lambda w: -w[0]):
        d.polygon(quad, fill=col)

    d.polygon(top, fill=TOP)
    w = int(SS * 5)
    d.line(top + [top[0]], fill=ORANGE, width=w, joint="curve")
    # base edges and corner verticals, only where the wall faces the viewer
    for _depth, quad, _col in walls:
        d.line([quad[0], quad[1]], fill=ORANGE, width=int(w * 0.8))
    for i in (0, 1, n - 1):
        d.line([base[i], top[i]], fill=ORANGE, width=int(w * 0.8))

    # curve handle on the round side's tip, like the tool shows it
    tip = n // 2
    hx, hy = ring[tip]
    a, b = project(hx, hy - 0.55, h), project(hx, hy + 0.55, h)
    d.line([a, b], fill=HANDLE, width=int(w * 0.7))
    r = SS * 7
    for p in (a, b):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=HANDLE)
    r = SS * 9
    for i, col in ((0, GREEN), (1, WHITE), (n - 1, WHITE), (tip, WHITE)):
        p = top[i]
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=col, outline=(30, 30, 30), width=SS * 2)

    img.resize((SIZE, SIZE), Image.LANCZOS).save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
