"""Encode mesh geometry into .VertexData / .IndexData buffers.

Vertex layouts used by player models (see docs/FORMAT.md):

  SKINNED  stream0: short4 pos | ubyte4 weights | ubyte4 indices | short2 uv   (20 B)
           stream1: short4n normal | short4n tangent                          (16 B)
  RIGID    as SKINNED without weights (single bone), stream0 = 16 B
  SHADOW   stream0: short4 pos | ubyte4 weights | ubyte4 indices             (16 B)
"""

import math
import struct

from .msh import Lod, VertexElement, POS_SCALE, UV_SCALE

SKINNED = [(7, 0, 0), (4, 1, 0), (4, 2, 0), (0x0A, 3, 1), (6, 4, 0), (0x0A, 5, 1)]
RIGID = [(7, 0, 0), (4, 2, 0), (0x0A, 3, 1), (6, 4, 0), (0x0A, 5, 1)]
SHADOW = [(7, 0, 0), (4, 1, 0), (4, 2, 0)]

# D3DCOLOR byte order: influence k is stored in byte _SLOT[k]
_SLOT = (2, 1, 0, 3)

MAX_PALETTE = 45  # largest palette seen in shipped player meshes


def pack_influences(influences):
    """[(palette_index, weight_float), ...] -> (weight bytes, index bytes) in game order.

    Keeps the 4 strongest influences and quantizes weights so they sum to 255.
    """
    inf = sorted(influences, key=lambda x: -x[1])[:4]
    tot = sum(w for _, w in inf) or 1.0
    q = [int(round(255 * w / tot)) for _, w in inf]
    if q:
        q[0] += 255 - sum(q)
    wb, ib = [0] * 4, [0] * 4
    for k, ((j, _), w) in enumerate(zip(inf, q)):
        if w <= 0:
            continue
        wb[_SLOT[k]] = w
        ib[_SLOT[k]] = j * 3
    return bytes(wb), bytes(ib)


def _clamp16(v):
    return max(-32768, min(32767, int(round(v))))


def _unit(v):
    l = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / l for c in v]


class BufferBuilder:
    """Accumulates vertex/index data for several meshes, like the game's files."""

    def __init__(self):
        self.vertex = bytearray()
        self.index = bytearray()

    def add_lod(self, layout, positions, surfaces, uvs=None, normals=None, tangents=None,
                weights=None, indices=None):
        """Append one LOD and return its Lod record.

        positions: [(x,y,z)] in centimetres (game space)
        surfaces:  list of triangle index lists (absolute vertex indices)
        normals/tangents: unit vectors; tangents may carry a 4th handedness component
        weights/indices: pre-packed 4-byte strings per vertex (see pack_influences)
        """
        n = len(positions)
        elems = [VertexElement(t, u, 0, s) for t, u, s in layout]
        streams = sorted({e.stream for e in elems})
        offsets = []
        for s in streams:
            offsets.append(len(self.vertex))
            for i in range(n):
                for e in elems:
                    if e.stream != s:
                        continue
                    self.vertex += self._encode(e, i, positions, uvs, normals, tangents,
                                                weights, indices)
        ib_off = len(self.index)
        for tri in surfaces:
            self.index += struct.pack(f"<{len(tri)}H", *tri)
        return Lod([len(t) for t in surfaces], elems, n, ib_off, offsets)

    @staticmethod
    def _encode(e, i, positions, uvs, normals, tangents, weights, indices):
        if e.usage == 0:
            p = positions[i]
            return struct.pack("<4h", *(_clamp16(c / POS_SCALE) for c in p), 1)
        if e.usage == 1:
            return weights[i]
        if e.usage == 2:
            return indices[i]
        if e.usage == 3:
            nx, ny, nz = _unit(normals[i][:3])
            return struct.pack("<4h", _clamp16(nx * 32767), _clamp16(ny * 32767),
                               _clamp16(nz * 32767), 32767)
        if e.usage == 4:
            u, v = uvs[i]
            return struct.pack("<2h", _clamp16(u / UV_SCALE), _clamp16(v / UV_SCALE))
        if e.usage == 5:
            t = tangents[i]
            tx, ty, tz = _unit(t[:3])
            w = -32766 if len(t) > 3 and t[3] < 0 else 32767
            return struct.pack("<4h", _clamp16(tx * 32767), _clamp16(ty * 32767),
                               _clamp16(tz * 32767), w)
        raise ValueError(f"unsupported vertex usage {e.usage}")
