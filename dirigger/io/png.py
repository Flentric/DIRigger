"""Tiny PNG reader and uncompressed DDS writer (no third-party imaging library needed)."""

import struct
import zlib

import numpy as np


def read_png(path):
    """Return an (H, W, 4) uint8 RGBA array.

    Uses Pillow when installed (any format); otherwise a built-in decoder for
    8-bit non-interlaced PNGs.
    """
    try:
        from PIL import Image
        return np.array(Image.open(path).convert("RGBA"))
    except ImportError:
        pass
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos, idat, palette, trns = 8, b"", None, None
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif typ == b"PLTE":
            palette = np.frombuffer(chunk, np.uint8).reshape(-1, 3)
        elif typ == b"tRNS":
            trns = np.frombuffer(chunk, np.uint8)
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
    if depth != 8 or interlace:
        raise ValueError(f"{path}: only 8-bit non-interlaced PNGs are supported")
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * ch
    out = np.zeros((h, stride), np.int32)
    prev = np.zeros(stride, np.int32)
    p = 0
    for y in range(h):
        f = raw[p]
        line = np.frombuffer(raw, np.uint8, stride, p + 1).astype(np.int32)
        p += 1 + stride
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 255
        else:
            cur = np.zeros(stride, np.int32)
            for x in range(stride):
                a = cur[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                if f == 1:
                    pr = a
                elif f == 3:
                    pr = (a + b) >> 1
                else:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[x] = (line[x] + pr) & 255
        out[y] = cur
        prev = cur
    px = out.astype(np.uint8).reshape(h, w, ch)
    if ctype == 3:
        rgba = np.concatenate([palette[px[..., 0]], np.full((h, w, 1), 255, np.uint8)], 2)
        if trns is not None:
            alpha = np.full(len(palette), 255, np.uint8)
            alpha[:len(trns)] = trns
            rgba[..., 3] = alpha[px[..., 0]]
        return rgba
    if ch == 1:
        px = np.repeat(px, 3, 2)
    elif ch == 2:
        px = np.concatenate([np.repeat(px[..., :1], 3, 2), px[..., 1:]], 2)
    if px.shape[2] == 3:
        px = np.concatenate([px, np.full((h, w, 1), 255, np.uint8)], 2)
    return px


def write_dds(path, rgba, mipmaps=True):
    """Write an uncompressed A8R8G8B8 DDS with an optional box-filtered mip chain."""
    h, w = rgba.shape[:2]
    levels = [rgba]
    if mipmaps:
        img = rgba.astype(np.float32)
        while img.shape[0] > 1 or img.shape[1] > 1:
            hh, ww = max(1, img.shape[0] // 2), max(1, img.shape[1] // 2)
            img = img[:hh * 2 if img.shape[0] > 1 else 1, :ww * 2 if img.shape[1] > 1 else 1]
            if img.shape[0] > 1:
                img = (img[0::2] + img[1::2]) / 2
            if img.shape[1] > 1:
                img = (img[:, 0::2] + img[:, 1::2]) / 2
            levels.append(np.clip(img + 0.5, 0, 255).astype(np.uint8))
    DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH, DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT = \
        1, 2, 4, 8, 0x1000, 0x20000
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT
    caps = 0x1000
    if len(levels) > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= 0x400008
    pf = struct.pack("<II4sIIIII", 32, 0x41, b"\0\0\0\0", 32,
                     0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    hdr = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, h, w, w * 4, 0, len(levels))
    hdr += b"\0" * 44 + pf + struct.pack("<IIIII", caps, 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(hdr)
        for lv in levels:
            f.write(lv[..., [2, 1, 0, 3]].tobytes())
