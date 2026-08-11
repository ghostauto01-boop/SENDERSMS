#!/usr/bin/env python3
"""Generate the app icons (PWA + favicon) from one vector definition.

Run after changing the brand colours or the mark:

    .venv/bin/python scripts/make_icons.py

Writes frontend/public/{icon-192.png,icon-512.png,apple-touch-icon.png,favicon.svg}.

The mark is a speech bubble (messaging) with a send arrow inside (outbound SMS)
-- the two things this app does. It is drawn from explicit geometry rather than
exported from a design tool so it can be regenerated exactly, and it is
supersampled 4x then downscaled so the diagonals stay smooth at 16px.
"""

import math
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "frontend", "public")

# Brand: matches theme_color in manifest.json and primary-600 in Tailwind.
BLUE = (37, 99, 235)     # #2563eb
INDIGO = (79, 70, 229)   # #4f46e5
WHITE = (255, 255, 255)

SS = 4  # supersample factor


def _gradient(size):
    """Diagonal blue -> indigo background, full-bleed (maskable-safe)."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            # Project onto the top-left -> bottom-right diagonal.
            t = (x + y) / (2 * (size - 1))
            px[x, y] = (
                round(BLUE[0] + (INDIGO[0] - BLUE[0]) * t),
                round(BLUE[1] + (INDIGO[1] - BLUE[1]) * t),
                round(BLUE[2] + (INDIGO[2] - BLUE[2]) * t),
            )
    return img


def _rotate(points, cx, cy, deg):
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)
    return [
        (cx + (x - cx) * cos - (y - cy) * sin, cy + (x - cx) * sin + (y - cy) * cos)
        for x, y in points
    ]


def _plane(size, cx, cy, w, deg=-18):
    """Classic 'send' paper plane, normalised from a 24x24 grid.

    Includes the notch on the trailing edge, which is what makes it read as a
    paper plane rather than a generic triangle.
    """
    grid = [(2, 21), (23, 12), (2, 3), (2, 10), (17, 12), (2, 14)]
    gx0, gx1 = 2, 23
    gy0, gy1 = 3, 21
    scale = w / (gx1 - gx0)
    gw, gh = (gx1 - gx0) * scale, (gy1 - gy0) * scale
    pts = [
        (cx - gw / 2 + (x - gx0) * scale, cy - gh / 2 + (y - gy0) * scale)
        for x, y in grid
    ]
    return _rotate(pts, cx, cy, deg)


def build(size, rounded=False):
    S = size * SS
    img = _gradient(S).convert("RGBA")
    d = ImageDraw.Draw(img)

    # Speech bubble. Kept inside the central ~80% so Android's maskable crop
    # (circle, squircle, teardrop) never clips it.
    bx0, by0 = 0.215 * S, 0.225 * S
    bx1, by1 = 0.785 * S, 0.655 * S
    radius = 0.105 * S
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=radius, fill=WHITE)

    # Tail, merged into the bubble body so it reads as one shape.
    d.polygon(
        [
            (0.315 * S, 0.60 * S),
            (0.30 * S, 0.795 * S),
            (0.475 * S, 0.638 * S),
        ],
        fill=WHITE,
    )

    # Send arrow, knocked out in brand blue against the white bubble.
    d.polygon(
        _plane(S, cx=0.50 * S, cy=0.435 * S, w=0.335 * S),
        fill=BLUE,
    )

    if rounded:
        # iOS-style squircle-ish corner for the plain "any" icon, so it does not
        # render as a hard square on desktop/browser surfaces.
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, S - 1, S - 1], radius=int(0.22 * S), fill=255
        )
        img.putalpha(mask)

    return img.resize((size, size), Image.LANCZOS)


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2563eb"/>
      <stop offset="1" stop-color="#4f46e5"/>
    </linearGradient>
  </defs>
  <rect width="64" height="64" rx="14" fill="url(#g)"/>
  <path d="M20.2 14.4h23.6a6.7 6.7 0 0 1 6.7 6.7v13.8a6.7 6.7 0 0 1-6.7 6.7H30.4L19.2 50.9l0.9-9.3h-0.9a6.7 6.7 0 0 1-6.7-6.7V21.1a6.7 6.7 0 0 1 6.7-6.7z" fill="#fff"/>
  <path d="M40.6 20.7 20.9 30.4l7.4 1.7 1.6 7.4 9.7-19.7z" fill="#2563eb" transform="rotate(-6 32 30)"/>
</svg>
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    # "any" icons get a rounded corner; "maskable" icons stay full-bleed so the
    # launcher can crop them to whatever shape the OS uses.
    targets = [
        ("icon-192.png", 192, True),
        ("icon-512.png", 512, True),
        ("icon-maskable-192.png", 192, False),
        ("icon-maskable-512.png", 512, False),
        # iOS applies its own mask and does not honour transparency, so this one
        # must be full-bleed and opaque.
        ("apple-touch-icon.png", 180, False),
    ]
    for name, size, rounded in targets:
        path = os.path.join(OUT, name)
        build(size, rounded=rounded).save(path, "PNG", optimize=True)
        print(f"wrote {name} ({size}x{size})")

    with open(os.path.join(OUT, "favicon.svg"), "w") as fh:
        fh.write(SVG)
    print("wrote favicon.svg")


if __name__ == "__main__":
    main()
