"""DXT1 / DXT5 (BC1 / BC3) block compression and decompression with numpy."""

import numpy as np


def _blocks(rgba):
    h, w = rgba.shape[:2]
    return rgba.reshape(h // 4, 4, w // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)


def _to565(c):
    c = np.clip(np.rint(c), 0, 255).astype(np.int32)
    return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)


def _from565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.float32)


def _color_block(px):
    """px: (N,16,3) float -> (N,8) uint8 opaque 4-colour DXT1 blocks."""
    lo, hi = px.min(1), px.max(1)
    inset = (hi - lo) / 16
    c0, c1 = _to565(hi - inset), _to565(lo + inset)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    same = c0 == c1                       # flat block: every index 0
    e0, e1 = _from565(c0), _from565(c1)
    pal = np.stack([e0, e1, (2 * e0 + e1) / 3, (e0 + 2 * e1) / 3], 1)      # (N,4,3)
    d = ((px[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)             # (N,16,4)
    idx = d.argmin(-1)
    idx[same] = 0
    bits = (idx.astype(np.uint32) << (2 * np.arange(16, dtype=np.uint32))).sum(1).astype(np.uint32)
    out = np.zeros((len(px), 8), np.uint8)
    out[:, 0:2] = c0.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = c1.astype("<u2").view(np.uint8).reshape(-1, 2)
    out[:, 4:8] = bits.astype("<u4").view(np.uint8).reshape(-1, 4)
    return out


def _alpha_block(a):
    """a: (N,16) float -> (N,8) uint8 DXT5 alpha blocks (8-value mode)."""
    a0 = np.clip(np.rint(a.max(1)), 0, 255).astype(np.int32)
    a1 = np.clip(np.rint(a.min(1)), 0, 255).astype(np.int32)
    flat = a0 == a1
    a1 = np.where(flat & (a0 > 0), a0 - 1, a1)
    a0 = np.where(flat & (a0 == 0), 1, a0)
    w = np.array([0, 7, 1, 2, 3, 4, 5, 6])          # palette slot -> weight of a1 (in 7ths)
    pal = (a0[:, None] * (7 - w) + a1[:, None] * w) / 7.0
    idx = np.abs(a[:, :, None] - pal[:, None, :]).argmin(-1).astype(np.uint64)
    bits = (idx << (3 * np.arange(16, dtype=np.uint64))).sum(1)
    out = np.zeros((len(a), 8), np.uint8)
    out[:, 0], out[:, 1] = a0, a1
    for b in range(6):
        out[:, 2 + b] = (bits >> np.uint64(8 * b)) & np.uint64(255)
    return out


def compress(rgba, fmt):
    """(H,W,4) uint8 with H,W multiples of 4 -> bytes. fmt: 'dxt1' or 'dxt5'."""
    bl = _blocks(rgba).astype(np.float32)
    col = _color_block(bl[..., :3])
    if fmt == "dxt1":
        return col.tobytes()
    return np.concatenate([_alpha_block(bl[..., 3]), col], 1).tobytes()


def decompress(data, w, h, fmt):
    """Inverse of `compress` (for checks and previews). Returns (H,W,4) uint8."""
    bs = 8 if fmt == "dxt1" else 16
    raw = np.frombuffer(data, np.uint8)[:(w // 4) * (h // 4) * bs].reshape(-1, bs)
    cb = raw[:, -8:]
    c0 = cb[:, 0].astype(np.int32) | (cb[:, 1].astype(np.int32) << 8)
    c1 = cb[:, 2].astype(np.int32) | (cb[:, 3].astype(np.int32) << 8)
    e0, e1 = _from565(c0), _from565(c1)
    four = (c0 > c1)[:, None]
    p2 = np.where(four, (2 * e0 + e1) / 3, (e0 + e1) / 2)
    p3 = np.where(four, (e0 + 2 * e1) / 3, 0)
    pal = np.stack([e0, e1, p2, p3], 1)
    bits = cb[:, 4:8].copy().view("<u4")[:, 0]
    idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))) & 3
    rgb = np.take_along_axis(pal, idx[..., None].astype(np.int64).repeat(3, -1), 1)
    alpha = np.full(idx.shape, 255.0)
    if fmt == "dxt5":
        a0, a1 = raw[:, 0].astype(np.float32), raw[:, 1].astype(np.float32)
        ab = np.zeros(len(raw), np.uint64)
        for b in range(6):
            ab |= raw[:, 2 + b].astype(np.uint64) << np.uint64(8 * b)
        ai = ((ab[:, None] >> (3 * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
        eight = (a0 > a1)[:, None]
        w8 = np.array([0, 7, 1, 2, 3, 4, 5, 6])[ai]
        v8 = (a0[:, None] * (7 - w8) + a1[:, None] * w8) / 7
        w6 = np.array([0, 5, 1, 2, 3, 4, 0, 0])[ai]
        v6 = np.where(ai == 6, 0, np.where(ai == 7, 255, (a0[:, None] * (5 - w6) + a1[:, None] * w6) / 5))
        alpha = np.where(eight, v8, v6)
    px = np.concatenate([rgb, alpha[..., None]], -1)
    img = px.reshape(h // 4, w // 4, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(h, w, 4)
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)
