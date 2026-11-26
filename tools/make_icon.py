"""Render the BANTAY logo (arch + dot, from public/logo.svg) to bantay.ico without extra dependencies.
Usage: python tools/make_icon.py"""
import struct
import zlib
from pathlib import Path

import numpy as np

TERRACOTTA, ORANGE, CHARCOAL = (0xC9, 0x6F, 0x4A), (0xE5, 0x8A, 0x52), (0x25, 0x23, 0x21)


def render(size, ss=4):
    n = size * ss
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / n * 256        # logo coordinates (viewBox 0..256)
    rgba = np.zeros((n, n, 4))
    # charcoal rounded tile so the icon reads on light and dark desktops
    r = 56
    cx, cy = np.clip(x, r, 256 - r), np.clip(y, r, 256 - r)
    tile = (x - cx) ** 2 + (y - cy) ** 2 <= r ** 2
    rgba[tile] = (*CHARCOAL, 255)
    # logo scaled to 80 % inside the tile
    lx, ly = (x - 128) / 0.8 + 128, (y - 128) / 0.8 + 128 + 18
    d = np.hypot(lx - 128, ly - 144)
    arch = (ly <= 144) & (d <= 92) & (d >= 48)
    legs = (ly >= 144) & (ly <= 204) & (((lx >= 36) & (lx <= 80)) | ((lx >= 176) & (lx <= 220)))
    rgba[arch | legs] = (*TERRACOTTA, 255)
    rgba[d <= 26] = (*ORANGE, 255)
    return rgba.reshape(size, ss, size, ss, 4).mean((1, 3)).round().astype(np.uint8)   # anti-aliased


def png(img):
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[i].tobytes() for i in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(sizes=(256, 64, 48, 32, 16)):
    images = [png(render(s)) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


if __name__ == "__main__":
    dest = Path(__file__).resolve().parent.parent / "bantay.ico"
    dest.write_bytes(ico())
    print("wrote", dest)
