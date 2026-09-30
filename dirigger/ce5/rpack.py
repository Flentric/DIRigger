"""Reader/writer for Chrome Engine 5 resource packs (`*.rpack`, magic "RP5L").

Layout (little-endian, see docs/FORMAT.md):

  header    9 x u32: magic, 0x24, 1, nParts, nSections, nFiles, namesSize, nNames, 0x800
  sections  nSections x {u32 type, u32 fileOffset, u32 unpackedSize, u32 packedSize, u32 nParts}
  parts     nParts x {u8 section, u8 flags, u16 file, u32 offset, u32 unpackedSize, u32 packedSize}
  files     nFiles x {u8 nParts, u8 0, u8 type, u8 1, u32 name, u32 firstPart}
  names     nNames x u32 offset, then the zero-terminated names
  data      each section 0x800-aligned

A section with packedSize 0 is stored raw. Otherwise either every part is its
own zlib stream (part offset = position in the packed section, 0x800-aligned,
packedSize > 0), or, when the parts' packedSize is 0, the whole section is one
zlib stream and part offsets point into the unpacked data.

File types seen: 0x10 mesh (parts: .msh, .MeshFixups, .VertexData, .IndexData,
.Skin, .SkinFixups), 0x20 texture, 0x30 shader, 0x40/0x42 animation, 0x50 particles.
"""

import struct
import zlib
from dataclasses import dataclass, field

MAGIC = 0x4C355052            # "RP5L"
ALIGN = 0x800

TYPE_MESH = 0x10
TYPE_TEXTURE = 0x20

# low byte of a mesh part's section type -> the dump file extension
MESH_PART_EXT = {0x10: ".msh", 0x11: ".MeshFixups", 0xF0: ".VertexData", 0xF1: ".IndexData",
                 0x12: ".Skin", 0x13: ".SkinFixups"}


@dataclass
class Section:
    type: int
    offset: int
    unpacked: int
    packed: int
    count: int
    solid: bool = False        # one zlib stream for the whole section
    raw: bytes = b""           # file bytes of the section (as stored)


@dataclass
class Part:
    section: int
    flags: int
    file: int
    offset: int
    unpacked: int
    packed: int
    stored: bytes = None       # the part's bytes as stored (zlib stream or raw)
    data: bytes = None         # replacement unpacked data, set by `replace`


@dataclass
class ResFile:
    count: int
    b1: int
    type: int
    b3: int
    name_index: int
    first: int


@dataclass
class RPack:
    header: tuple
    sections: list
    parts: list
    files: list
    names: list
    name_offsets: list
    names_blob: bytes
    tail: bytes = b""
    _cache: dict = field(default_factory=dict)

    def name(self, f):
        return self.names[self.files[f].name_index]

    def find(self, name, type=None):
        for i, f in enumerate(self.files):
            if self.names[f.name_index].lower() == name.lower() and (type is None or f.type == type):
                return i
        return None

    def file_parts(self, f):
        rf = self.files[f]
        return list(range(rf.first, rf.first + rf.count))

    def section_data(self, s):
        """Unpacked bytes of a solid section."""
        if s not in self._cache:
            sec = self.sections[s]
            self._cache[s] = zlib.decompress(sec.raw) if sec.packed else sec.raw
        return self._cache[s]

    def part_data(self, p):
        part = self.parts[p]
        if part.data is not None:
            return part.data
        sec = self.sections[part.section]
        if sec.solid or not sec.packed:
            return self.section_data(part.section)[part.offset:part.offset + part.unpacked]
        return zlib.decompress(part.stored)

    def replace(self, p, data):
        """Give part `p` new unpacked contents (written on `build`)."""
        if self.sections[self.parts[p].section].solid:
            raise ValueError("parts of solidly compressed sections can't be replaced yet")
        self.parts[p].data = bytes(data)

    def mesh_parts(self, f):
        """{'.msh': part index, ...} for a mesh file."""
        return {MESH_PART_EXT[self.sections[self.parts[p].section].type & 0xFF]: p
                for p in self.file_parts(f)}


def parse_rpack(data: bytes) -> RPack:
    h = struct.unpack_from("<9I", data, 0)
    if h[0] != MAGIC:
        raise ValueError("not an RP5L rpack")
    n_parts, n_sec, n_files, names_size, n_names = h[3], h[4], h[5], h[6], h[7]
    o = 0x24
    secs = []
    for i in range(n_sec):
        secs.append(Section(*struct.unpack_from("<5I", data, o)))
        o += 20
    parts = []
    for i in range(n_parts):
        parts.append(Part(*struct.unpack_from("<BBHIII", data, o)))
        o += 16
    files = []
    for i in range(n_files):
        files.append(ResFile(*struct.unpack_from("<BBBBII", data, o)))
        o += 12
    offs = list(struct.unpack_from(f"<{n_names}I", data, o))
    o += 4 * n_names
    blob = data[o:o + names_size]
    names = [blob[x:blob.index(b"\0", x)].decode("latin-1") for x in offs]

    end = 0
    for si, sec in enumerate(secs):
        mine = [p for p in parts if p.section == si]
        sec.solid = bool(sec.packed) and all(p.packed == 0 for p in mine)
        if sec.solid:
            span = sec.packed
        else:
            # sizes in the section record leave out the padding between parts
            span = max([p.offset + (p.packed if sec.packed else p.unpacked) for p in mine] or [0])
        sec.raw = data[sec.offset:sec.offset + span]
        for p in mine:
            if not sec.solid:
                n = p.packed if sec.packed else p.unpacked
                p.stored = sec.raw[p.offset:p.offset + n]
        end = max(end, sec.offset + span)
    end += -end % ALIGN
    return RPack(h, secs, parts, files, names, offs, blob, data[end:])


def load_rpack(path):
    with open(path, "rb") as f:
        return parse_rpack(f.read())


def _pad(buf, n=ALIGN):
    buf.extend(b"\0" * (-len(buf) % n))


def build_rpack(rp: RPack) -> bytes:
    """Serialize. Unchanged parts are copied as stored; replaced parts are recompressed.

    Parts keep their order inside each section; every part and section starts
    0x800-aligned, as in the game's packs.
    """
    n_sec = len(rp.sections)
    head_size = 0x24 + 20 * n_sec + 16 * len(rp.parts) + 12 * len(rp.files) + \
        4 * len(rp.names) + len(rp.names_blob)

    out = bytearray(head_size)
    _pad(out)
    new_secs = []
    for si, sec in enumerate(rp.sections):
        mine = sorted((i for i, p in enumerate(rp.parts) if p.section == si),
                      key=lambda i: rp.parts[i].offset)
        start = len(out)
        if sec.solid:
            body = sec.raw
            unpacked = sec.unpacked
        else:
            body = bytearray()
            unpacked = 0
            for i in mine:
                p = rp.parts[i]
                _pad(body)
                if p.data is not None:
                    stored = zlib.compress(p.data, 9) if sec.packed else p.data
                    p.unpacked = len(p.data)
                    p.packed = len(stored) if sec.packed else 0
                    p.stored, p.data = stored, None
                p.offset = len(body)
                body += p.stored
                unpacked += p.unpacked
        out += body
        packed = sum(rp.parts[i].packed for i in mine) if sec.packed and not sec.solid \
            else sec.packed
        new_secs.append(Section(sec.type, start, unpacked, packed, len(mine), sec.solid,
                                bytes(body)))
        _pad(out)
    rp.sections = new_secs
    rp._cache.clear()

    hdr = bytearray()
    hdr += struct.pack("<9I", *rp.header)
    for s in rp.sections:
        hdr += struct.pack("<5I", s.type, s.offset, s.unpacked, s.packed, s.count)
    for p in rp.parts:
        hdr += struct.pack("<BBHIII", p.section, p.flags, p.file, p.offset, p.unpacked, p.packed)
    for f in rp.files:
        hdr += struct.pack("<BBBBII", f.count, f.b1, f.type, f.b3, f.name_index, f.first)
    hdr += struct.pack(f"<{len(rp.name_offsets)}I", *rp.name_offsets)
    hdr += rp.names_blob
    out[:len(hdr)] = hdr
    return bytes(out) + rp.tail
