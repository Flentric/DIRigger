"""Writer for compiled Chrome Engine 5 meshes (original Dead Island).

`build_msh` serializes a MeshFile (as produced by `parse_msh`, or assembled
from scratch) using the game's own layout and alignment, and returns the
.msh blob plus the .MeshFixups pointer list. Re-building an unmodified game
file reproduces it byte for byte (see tests).

Vertex and index buffers are not touched here: each Lod carries its own
offsets into .VertexData / .IndexData (see `geometry.py` to build those).
"""

import struct

from .msh import NODE_MESH, NODE_SIZE, LOD_SIZE, MAX_STREAMS

NULL = 0xFFFFFFFF

# pointer fields inside fixed-size records (offsets relative to the record)
_NODE_PTRS = (0x78, 0x80, 0x8C, 0x90, 0xA4, 0xA8)
_NODE_MESH_PTRS = (0x94, 0x98, 0x9C, 0xA0)
_HDR_PTRS = (0x00, 0x10, 0x1C, 0x20, 0x28, 0x2C, 0x30, 0x3C, 0x40)

# default raw records for meshes built from scratch
EMPTY_MORPH_A = struct.pack("<8I", 0, NULL, NULL, NULL, 0, NULL, 1, 0)
EMPTY_B = struct.pack("<8I", 0, NULL, NULL, 0, NULL, 0, 0, 0)


class _Out:
    def __init__(self):
        self.buf = bytearray()
        self.ptrs = []

    def align(self, n):
        while len(self.buf) % n:
            self.buf.append(0)
        return len(self.buf)

    def put(self, data, align=1):
        o = self.align(align)
        self.buf.extend(data)
        return o

    def u32(self, o, v):
        struct.pack_into("<I", self.buf, o, v)

    def ptr(self, o, target):
        """Store a pointer at `o` and register it for fixup (None = null)."""
        self.u32(o, NULL if target is None else target)
        self.ptrs.append(o)

    def cstr(self, s):
        return self.put(s.encode("latin-1") + b"\0")


def build_msh(m):
    """Serialize `m` (MeshFile). Returns (msh_bytes, meshfixups_bytes)."""
    out = _Out()
    hdr = out.put(bytes(0x44))

    sp = out.put(struct.pack(f"<{len(m.surface_params)}I", *m.surface_params))
    name = out.cstr(m.name)
    anim = out.cstr(m.anim_script) if m.anim_script else None

    nodes_at = out.align(8)
    out.put(bytes(NODE_SIZE * len(m.nodes)))

    morph_strings = {}
    for i, n in enumerate(m.nodes):
        rec = nodes_at + NODE_SIZE * i
        out.buf[rec:rec + NODE_SIZE] = n.raw.ljust(NODE_SIZE, b"\0")
        struct.pack_into("<I", out.buf, rec + 0x7C, i)
        out.buf[rec + 0x84] = n.type
        out.buf[rec + 0x85] = sum(1 for c in m.nodes if c.parent == i)
        out.ptr(rec + 0x78, out.cstr(n.name))
        out.ptr(rec + 0x80, None if n.parent < 0 else nodes_at + NODE_SIZE * n.parent)
        out.ptr(rec + 0xA4, None)
        out.ptr(rec + 0xA8, None)
        if n.type != NODE_MESH:
            out.ptr(rec + 0x8C, None)
            out.ptr(rec + 0x90, None)
            continue
        for f in _NODE_MESH_PTRS:
            out.ptr(rec + f, None)
        me = n.mesh

        a = out.put(me.raw_a or EMPTY_MORPH_A, 16)
        b = out.put(me.raw_b or EMPTY_B)
        out.ptr(rec + 0x8C, a)
        out.ptr(rec + 0x90, b)

        mo = me.morphs
        if mo:
            offs = out.put(bytes(4 * len(mo.names)))
            remap = out.put(struct.pack(f"<{len(mo.remap)}H", *mo.remap), 8)
            names = out.put(bytes(4 * len(mo.names)), 8)
            for k, nm in enumerate(mo.names):
                morph_strings[nm] = out.cstr(nm)
                out.ptr(names + 4 * k, morph_strings[nm])
            data = out.put(mo.base_data, 16)
            for k, t in enumerate(mo.targets):
                out.ptr(offs + 4 * k, out.put(t, 16))
            struct.pack_into("<IIIII", out.buf, a, len(mo.names), offs, remap, names, mo.vertex_count)
            struct.pack_into("<I", out.buf, a + 20, data)
            for f in (4, 8, 12, 20):
                out.ptrs.append(a + f)
        else:
            for f in (4, 8, 12, 20):
                out.ptr(a + f, None)

        n_surf = len(me.surface_slots)
        paltab = out.put(bytes(8 * n_surf), 16)
        slots = out.put(struct.pack(f"<{n_surf}H", *me.surface_slots), 8)
        for s, pal in enumerate(me.palettes):
            p = out.put(struct.pack(f"<{len(pal)}H", *pal), 16)
            out.ptr(paltab + 8 * s, p)
            out.u32(paltab + 8 * s + 4, len(pal))

        lods = out.align(4)
        out.put(bytes(LOD_SIZE * len(me.lods)))
        for k, lod in enumerate(me.lods):
            lo = lods + LOD_SIZE * k
            ic = out.put(struct.pack(f"<{len(lod.index_counts)}I", *lod.index_counts), 4)
            decl = out.put(bytes(8 * len(lod.elements)), 4)
            for j, e in enumerate(lod.elements):
                out.u32(decl + 8 * j, e.type | e.usage << 8 | e.extra << 16 | e.stream << 24)
                out.ptr(decl + 8 * j + 4, None)
            streams = list(lod.stream_offsets) + [NULL] * (MAX_STREAMS - 1 - len(lod.stream_offsets))
            struct.pack_into(f"<HHII{MAX_STREAMS - 1}I", out.buf, lo + 8, len(lod.elements),
                             len(lod.index_counts), lod.vertex_count, lod.index_offset, *streams)
            out.ptr(lo, ic)
            out.ptr(lo + 4, decl)
            out.ptr(lo + 0x24, None)

        struct.pack_into("<HH", out.buf, b + 12, len(me.lods), n_surf)
        out.ptr(b + 4, lods)
        out.ptr(b + 8, slots)
        out.ptr(b + 16, paltab)

    matdb = out.put(struct.pack("<HHI", m.slot_count, len(m.materials), 0), 4)
    ents = out.put(bytes(12 * len(m.materials)))
    out.ptr(matdb + 4, ents)
    strings = {}
    for k, mat in enumerate(m.materials):
        if mat.name not in strings:
            strings[mat.name] = out.cstr(mat.name)
        out.ptr(ents + 12 * k, strings[mat.name])
        struct.pack_into("<II", out.buf, ents + 12 * k + 4, mat.flags, 0)

    mtab = None
    if m.morph_names:
        mtab = out.put(bytes(8 * len(m.morph_names)), 4)
        words = m.morph_table_words or [0] * len(m.morph_names)
        for k, nm in enumerate(m.morph_names):
            if nm not in morph_strings:
                morph_strings[nm] = out.cstr(nm)
            out.ptr(mtab + 8 * k, morph_strings[nm])
            out.u32(mtab + 8 * k + 4, words[k])

    h = list(m.header) + [0] * (17 - len(m.header))
    h[1], h[3], h[6] = NULL, len(m.nodes), len(m.surface_params)
    h[9] = len(m.morph_names)
    struct.pack_into("<17I", out.buf, hdr, *h)
    out.ptr(hdr + 0x00, name)
    out.ptr(hdr + 0x10, nodes_at)
    out.ptr(hdr + 0x1C, sp)
    out.ptr(hdr + 0x20, matdb)
    out.ptr(hdr + 0x28, mtab)
    for f in (0x2C, 0x30, 0x3C):
        out.ptr(hdr + f, None)
    out.ptr(hdr + 0x40, anim)

    fixups = struct.pack(f"<{len(out.ptrs)}I", *sorted(out.ptrs))
    return bytes(out.buf), fixups
