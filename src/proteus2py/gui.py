"""Helpers for the Proteus GUI session: find simulation graphs on a screenshot, crop pictures."""
from __future__ import annotations

from pathlib import Path

CANVAS = (310, 135, 1635, 945)             # schematic canvas area on the 1920x1080 virtual screen
TAB_SCHEMATIC = (240, 117)


def find_graph_bars(png: Path, min_width: int = 250) -> list[tuple[int, int, int]]:
    """Title bars of graphs in the schematic: bright green (idle) or red (selected) runs. Returns [(x_center, y, width)] top-to-bottom."""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    px = im.load()
    x0, y0, x1, y1 = CANVAS
    found: list[tuple[int, int, int]] = []
    for y in range(y0, y1):
        x = x0
        while x < x1:
            r, g, b = px[x, y]
            green = g > 150 and r < 80 and b < 80
            red = r > 200 and g < 60 and b < 60
            if green or red:
                s = x
                while x < x1 and px[x, y][:3] == (r, g, b):
                    x += 1
                if x - s >= min_width:
                    found.append(((s + x) // 2, y, x - s))
            else:
                x += 1
    # one bar = rows of (nearly) the same long run; text pixels inside the bar break the run, so only full-width rows count
    out: list[list[int]] = []
    for xc, y, w in sorted(found, key=lambda t: (t[1], t[0])):
        for o in out:
            if abs(o[0] - xc) < 6 and abs(o[2] - w) < 4 and y - o[3] <= 14:
                o[3] = y
                break
        else:
            out.append([xc, y, w, y])
    return [(o[0], (o[1] + o[3]) // 2, o[2]) for o in out]


def crop(png: Path, box: tuple[int, int, int, int], out: Path | None = None) -> Path:
    from PIL import Image
    im = Image.open(png)
    dst = out or png
    im.crop(box).save(dst)
    return dst


def trim(png: Path, pad: int = 6) -> Path:
    """Crop the Xvfb screenshot to the Proteus window (drop the blue desktop and black margin)."""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    w, h = im.size
    box = (max(0, 155 - pad), max(0, 24 - pad), min(w, 1642 + pad), min(h, 980 + pad))
    im.crop(box).save(png)
    return png
