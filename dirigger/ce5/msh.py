"""Reader for compiled Chrome Engine 5 meshes (original Dead Island, 32-bit).

A compiled mesh is split into several resource parts:

  <name>.msh          main blob (header, nodes, mesh descriptors, materials, morphs)
  <name>.MeshFixups   offsets of every 32-bit pointer inside the .msh blob
  <name>.VertexData   GPU vertex streams for all meshes
  <name>.IndexData    16-bit triangle-list indices for all meshes
  <name>.Skin         skin presets (hidden nodes + material remaps), see skin.py
  <name>.SkinFixups   typed resolve table for the .Skin blob

See docs/FORMAT.md for the byte-level layout.
"""

import os
import struct
from dataclasses import dataclass, field

from .binutil import Blob, read_pointer_fixups

NODE_SIZE = 0xB0
LOD_SIZE = 0x28
MAX_STREAMS = 5

# Node types (byte at node+0x84)
NODE_MESH = 2
NODE_DUMMY = 4
NODE_BONE = 8

# Vertex element usages
USAGE_POSITION = 0
USAGE_WEIGHTS = 1
USAGE_INDICES = 2
USAGE_NORMAL = 3
USAGE_UV0 = 4
USAGE_TANGENT = 5

# Vertex element types -> (byte size, struct format)
ELEM_TYPES = {
    2: (12, "3f"),   # float3 position (morphable meshes)
    4: (4, "4B"),    # ubyte4 (blend weights / blend indices)
    6: (4, "2h"),    # short2 texcoord, /4096
    7: (8, "4h"),    # short4 position, /8 -> centimetres
    0x0A: (8, "4h"), # short4n normal / tangent, /32767
}

POS_SCALE = 1.0 / 8.0
UV_SCALE = 1.0 / 4096.0
NRM_SCALE = 1.0 / 32767.0


@dataclass
class VertexElement:
    type: int
    usage: int
    extra: int
    stream: int

    @property
    def size(self):
        return ELEM_TYPES[self.type][0]


@dataclass
class Lod:
    index_counts: list
    elements: list
    vertex_count: int
    index_offset: int          # byte offset into IndexData
    stream_offsets: list       # byte offsets into VertexData
    raw: bytes = b""


@dataclass
class Morphs:
    names: list
    remap: list
    target_offsets: list
    vertex_count: int
    data_offset: int
    base_data: bytes = b""     # opaque block at data_offset, before the first target
    targets: list = None       # opaque per-target blocks (16-byte padded)


@dataclass
class Mesh:
    surface_slots: list        # material slot per surface
    palettes: list             # bone palette (node indices) per surface
    lods: list
    morphs: Morphs = None
    raw_a: bytes = b""
    raw_b: bytes = b""


@dataclass
class Node:
    index: int
    name: str
    parent: int
    type: int
    child_count: int
    flags: int
    local: list                # 3x4, parent-relative
    inv_bind: list             # 3x4, model -> node
    aabb_center: tuple
    aabb_half: tuple
    mesh: Mesh = None
    raw: bytes = b""


@dataclass
class Material:
    name: str
    flags: int


@dataclass
class MeshFile:
    name: str
    anim_script: str
    nodes: list
    materials: list
    slot_count: int
    surface_params: list
    morph_names: list
    header: tuple
    morph_table_words: list = field(default_factory=list)
    blob: Blob = None
    fixups: list = field(default_factory=list)
    vertex_data: bytes = b""
    index_data: bytes = b""

    def node_by_name(self, name):
        for n in self.nodes:
            if n.name == name:
                return n
        return None

    @property
    def bones(self):
        return [n for n in self.nodes if n.type != NODE_MESH]

    @property
    def mesh_nodes(self):
        return [n for n in self.nodes if n.type == NODE_MESH]

    # ------------------------------------------------------------------
    # Geometry decoding

    def decode_lod(self, lod: Lod):
        """Decode one LOD into plain Python lists.

        Returns dict with positions, normals, tangents, uvs, weights, indices
        (raw blend indices, already divided by 3 -> palette slots) and the
        per-surface triangle index lists.
        """
        strides = [0] * MAX_STREAMS
        offsets = {}
        for e in lod.elements:
            offsets[e.usage] = (e.stream, strides[e.stream], e)
            strides[e.stream] += e.size

        n = lod.vertex_count
        out = {"positions": [], "normals": [], "tangents": [], "uvs": [],
               "weights": [], "joints": []}
        vd = self.vertex_data

        def read(usage, i):
            s, off, e = offsets[usage]
            base = lod.stream_offsets[s] + i * strides[s] + off
            return struct.unpack_from("<" + ELEM_TYPES[e.type][1], vd, base), e

        for i in range(n):
            p, e = read(USAGE_POSITION, i)
            if e.type == 7:
                out["positions"].append((p[0] * POS_SCALE, p[1] * POS_SCALE, p[2] * POS_SCALE))
            else:
                out["positions"].append(tuple(p))
            if USAGE_NORMAL in offsets:
                q, _ = read(USAGE_NORMAL, i)
                out["normals"].append(tuple(c * NRM_SCALE for c in q[:3]))
            if USAGE_TANGENT in offsets:
                q, _ = read(USAGE_TANGENT, i)
                out["tangents"].append(tuple(c * NRM_SCALE for c in q))
            if USAGE_UV0 in offsets:
                q, _ = read(USAGE_UV0, i)
                out["uvs"].append((q[0] * UV_SCALE, q[1] * UV_SCALE))
            if USAGE_INDICES in offsets:
                q, _ = read(USAGE_INDICES, i)
                out["joints"].append(tuple(c // 3 for c in q))
            if USAGE_WEIGHTS in offsets:
                q, _ = read(USAGE_WEIGHTS, i)
                out["weights"].append(tuple(q))
            elif USAGE_INDICES in offsets:
                out["weights"].append((255, 0, 0, 0))

        surfaces = []
        off = lod.index_offset
        for c in lod.index_counts:
            surfaces.append(list(struct.unpack_from(f"<{c}H", self.index_data, off)))
            off += 2 * c
        out["surfaces"] = surfaces
        return out


def _parse_elements(b: Blob, o, count):
    elems = []
    for k in range(count):
        d = b.u32(o + 8 * k)
        elems.append(VertexElement(d & 0xFF, (d >> 8) & 0xFF, (d >> 16) & 0xFF, (d >> 24) & 0xFF))
    return elems


def _parse_lod(b: Blob, o):
    p_counts, p_decl = b.u32(o), b.u32(o + 4)
    n_elems, n_surf = b.u16(o + 8), b.u16(o + 10)
    vcount, ib_off = b.u32(o + 12), b.u32(o + 16)
    streams = [s for s in b.arr("I", o + 20, MAX_STREAMS) if s != 0xFFFFFFFF]
    return Lod(list(b.arr("I", p_counts, n_surf)), _parse_elements(b, p_decl, n_elems),
               vcount, ib_off, streams, b.data[o:o + LOD_SIZE])


def _parse_mesh(b: Blob, p_morph, p_mesh):
    p_lods = b.ptr(p_mesh + 4)
    p_slots = b.ptr(p_mesh + 8)
    n_lods, n_surf = b.u16(p_mesh + 12), b.u16(p_mesh + 14)
    p_pal = b.ptr(p_mesh + 16)
    slots = list(b.arr("H", p_slots, n_surf))
    palettes = []
    for s in range(n_surf):
        pp, pc = b.u32(p_pal + 8 * s), b.u32(p_pal + 8 * s + 4)
        palettes.append(list(b.arr("H", pp, pc)))
    lods = [_parse_lod(b, p_lods + LOD_SIZE * k) for k in range(n_lods)]

    morphs = None
    if p_morph is not None and b.u32(p_morph) > 0:
        nm = b.u32(p_morph)
        offs = list(b.arr("I", b.u32(p_morph + 4), nm))
        remap = list(b.arr("H", b.u32(p_morph + 8), nm))
        names = [b.cstr(b.u32(b.u32(p_morph + 12) + 4 * k)) for k in range(nm)]
        vc, data = b.u32(p_morph + 16), b.u32(p_morph + 20)
        morphs = Morphs(names, remap, offs, vc, data,
                        base_data=b.data[data:min(offs)],
                        targets=[b.data[t:t + vc * 6] for t in offs])
    return Mesh(slots, palettes, lods, morphs,
                b.data[p_morph:p_morph + 0x20] if p_morph is not None else b"",
                b.data[p_mesh:p_mesh + 0x20])


def parse_msh(data: bytes, fixups=None, vertex_data=b"", index_data=b""):
    b = Blob(data)
    hdr = b.arr("I", 0, 17)
    n_nodes, p_nodes = hdr[3], hdr[4]

    nodes = []
    for i in range(n_nodes):
        o = p_nodes + NODE_SIZE * i
        p_parent = b.ptr(o + 0x80)
        typ = b.u8(o + 0x84)
        node = Node(
            index=b.u32(o + 0x7C),
            name=b.cstr(b.ptr(o + 0x78)),
            parent=-1 if p_parent is None else (p_parent - p_nodes) // NODE_SIZE,
            type=typ,
            child_count=b.u8(o + 0x85),
            flags=b.u32(o + 0x88),
            local=b.mat34(o),
            inv_bind=b.mat34(o + 0x30),
            aabb_center=b.arr("f", o + 0x60, 3),
            aabb_half=b.arr("f", o + 0x6C, 3),
            raw=data[o:o + NODE_SIZE],
        )
        if typ == NODE_MESH:
            node.mesh = _parse_mesh(b, b.ptr(o + 0x8C), b.ptr(o + 0x90))
        nodes.append(node)

    p_mat = hdr[8]
    slot_count, mat_count = b.u16(p_mat), b.u16(p_mat + 2)
    p_entries = b.u32(p_mat + 4)
    materials = [Material(b.cstr(b.u32(p_entries + 12 * k)), b.u32(p_entries + 12 * k + 4))
                 for k in range(mat_count)]

    morph_names = [b.cstr(b.u32(hdr[10] + 8 * k)) for k in range(hdr[9])] if hdr[9] else []

    return MeshFile(
        name=b.cstr(b.ptr(0)),
        morph_table_words=[b.u32(hdr[10] + 8 * k + 4) for k in range(hdr[9])] if hdr[9] else [],
        anim_script=b.cstr(b.ptr(0x40)),
        nodes=nodes,
        materials=materials,
        slot_count=slot_count,
        surface_params=list(b.arr("I", hdr[7], hdr[6])),
        morph_names=morph_names,
        header=hdr,
        blob=b,
        fixups=fixups or [],
        vertex_data=vertex_data,
        index_data=index_data,
    )


def _find(folder, base, ext):
    for cand in os.listdir(folder):
        if cand.lower() == (base + ext).lower():
            return os.path.join(folder, cand)
    return None


def load_msh(path):
    """Load a compiled mesh from its .msh path; sibling part files are picked up automatically."""
    folder, fname = os.path.split(os.path.abspath(path))
    base = os.path.splitext(fname)[0]

    def part(ext):
        p = _find(folder, base, ext)
        return open(p, "rb").read() if p else b""

    with open(path, "rb") as f:
        data = f.read()
    fx = part(".MeshFixups")
    return parse_msh(data, read_pointer_fixups(fx) if fx else None,
                     part(".VertexData"), part(".IndexData"))
