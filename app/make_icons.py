#!/usr/bin/env python3
"""Generate Interlude app icons (pure stdlib, no Pillow).

Produces icon-192.png and icon-512.png: a solid violet square with two white
rounded pause bars. Kept inside the maskable "safe zone" (center ~60%) so the
icon survives circular/rounded masking on macOS and Android.

Run once (or whenever the palette changes):  python3 make_icons.py
"""
import os
import struct
import zlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

VIOLET = (124, 77, 235)   # #7C4DEB
WHITE = (255, 255, 255)


def rounded_rect(x, y, w, h, r):
    """Return a predicate telling whether (px,py) is inside a rounded rect."""
    def inside(px, py):
        if px < x or px >= x + w or py < y or py >= y + h:
            return False
        # corner regions
        cx = None
        cy = None
        if px < x + r and py < y + r:
            cx, cy = x + r, y + r
        elif px >= x + w - r and py < y + r:
            cx, cy = x + w - r, y + r
        elif px < x + r and py >= y + h - r:
            cx, cy = x + r, y + h - r
        elif px >= x + w - r and py >= y + h - r:
            cx, cy = x + w - r, y + h - r
        if cx is not None:
            return (px - cx) ** 2 + (py - cy) ** 2 <= r * r
        return True
    return inside


def build_png(size):
    # Pause geometry: two bars centered, within the middle ~54% of the icon.
    bar_h = int(size * 0.42)
    bar_w = int(size * 0.13)
    gap = int(size * 0.10)
    total_w = bar_w * 2 + gap
    left = (size - total_w) // 2
    top = (size - bar_h) // 2
    radius = max(2, bar_w // 3)

    bar1 = rounded_rect(left, top, bar_w, bar_h, radius)
    bar2 = rounded_rect(left + bar_w + gap, top, bar_w, bar_h, radius)

    raw = bytearray()
    for py in range(size):
        raw.append(0)  # filter type 0 (None) per scanline
        for px in range(size):
            if bar1(px, py) or bar2(px, py):
                raw += bytes(WHITE)
            else:
                raw += bytes(VIOLET)
    return raw


def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path, size):
    raw = build_png(size)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    with open(path, "wb") as f:
        f.write(sig)
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", idat))
        f.write(chunk(b"IEND", b""))
    print(f"wrote {path} ({size}x{size})")


if __name__ == "__main__":
    write_png(os.path.join(BASE_DIR, "icon-192.png"), 192)
    write_png(os.path.join(BASE_DIR, "icon-512.png"), 512)
