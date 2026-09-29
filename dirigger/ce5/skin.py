"""Compiled .Skin / .SkinFixups (original Dead Island, Chrome Engine 5).

A skin is a named preset applied to a mesh: which nodes are hidden and which
material goes into each material slot. Player models ship a `<Hero>_TPP`
(third person, full body) and a `<Hero>_FPP` (first person, head hidden) skin.

Pointers in the .Skin blob are stored as (offset + 1); 0 means null. The
.SkinFixups part holds the blob size, a typed resolve table and the list of
pointer locations.
"""

import struct
from dataclasses import dataclass, field

CLASS_STRING = 0xA0000000
CLASS_ROOT = 0xA0000001
CLASS_SKIN = 0xA0000002
CLASS_PARAMS = 0xA0000005
CLASS_HIDE = 0xA0000006
CLASS_MATMAP = 0xA0000008

ROOT_SIZE = 0x14
SKIN_SIZE = 0x40


@dataclass
class SkinPreset:
    name: str
    material_map: list          # [(slot, material_index)]
    hidden: list                # [(node_index, flags)]
    params: list                # per-surface-param u32 values
    flags: int = 0x81
    extra: int = 0              # upper u16 next to flags; preserved as-is


@dataclass
class SkinFile:
    skins: list = field(default_factory=list)
    root_tail: int = 0x00010000

    def get(self, name):
        for s in self.skins:
            if s.name.lower() == name.lower():
                return s
        return None


def _p(v):
    return None if v == 0 else v - 1


def parse_skin(data: bytes) -> SkinFile:
    u32 = lambda o: struct.unpack_from("<I", data, o)[0]
    u16 = lambda o: struct.unpack_from("<H", data, o)[0]

    def cstr(o):
        return data[o:data.index(b"\0", o)].decode("latin-1")

    p_skins, count, tail = _p(u32(0)), u32(0x0C), u32(0x10)
    out = SkinFile(root_tail=tail)
    for k in range(count):
        e = p_skins + SKIN_SIZE * k
        fl = u32(e + 0x24)
        n_map, n_hide, n_par = u16(e + 0x28), u16(e + 0x2A), u32(e + 0x2C)
        pm, ph, pp = _p(u32(e + 0x30)), _p(u32(e + 0x34)), _p(u32(e + 0x38))
        out.skins.append(SkinPreset(
            name=cstr(_p(u32(e + 0x08))),
            material_map=[(u16(pm + 4 * i), u16(pm + 4 * i + 2)) for i in range(n_map)],
            hidden=[(u16(ph + 4 * i), u16(ph + 4 * i + 2)) for i in range(n_hide)],
            params=[u32(pp + 4 * i) for i in range(n_par)],
            flags=fl & 0xFFFF,
            extra=fl >> 16,
        ))
    return out


def build_skin(skin: SkinFile):
    """Serialize a SkinFile. Returns (skin_bytes, skinfixups_bytes).

    Identical sub-arrays are shared between presets, matching the game's
    compiler, so re-building an unmodified file is byte-identical.
    """
    buf = bytearray(ROOT_SIZE + SKIN_SIZE * len(skin.skins))
    resolves = [(0, CLASS_ROOT, 1), (ROOT_SIZE, CLASS_SKIN, len(skin.skins))]
    pointers = [0]
    struct.pack_into("<IIIII", buf, 0, ROOT_SIZE + 1, 0, 0, len(skin.skins), skin.root_tail)

    pool = {}

    def emit(cls, key, payload, count):
        if (cls, key) in pool:
            return pool[(cls, key)]
        off = len(buf)
        buf.extend(payload)
        while len(buf) % 4:
            buf.append(0)
        resolves.append((off, cls, count))
        pool[(cls, key)] = off
        return off

    def pairs(items):
        return b"".join(struct.pack("<HH", a, b) for a, b in items)

    for k, s in enumerate(skin.skins):
        e = ROOT_SIZE + SKIN_SIZE * k
        pm = emit(CLASS_MATMAP, tuple(s.material_map), pairs(s.material_map), len(s.material_map)) \
            if s.material_map else None
        ph = emit(CLASS_HIDE, tuple(s.hidden), pairs(s.hidden), len(s.hidden)) if s.hidden else None
        pp = emit(CLASS_PARAMS, tuple(s.params), struct.pack(f"<{len(s.params)}I", *s.params),
                  len(s.params)) if s.params else None
        name = s.name.encode("latin-1") + b"\0"
        pn = len(buf)
        buf.extend(name)
        while len(buf) % 4:
            buf.append(0)
        resolves.append((pn, CLASS_STRING, 1))

        ref = lambda o: 0 if o is None else o + 1
        struct.pack_into("<I", buf, e + 0x08, ref(pn))
        struct.pack_into("<IHHIIII", buf, e + 0x24, s.flags | (s.extra << 16),
                         len(s.material_map), len(s.hidden), len(s.params),
                         ref(pm), ref(ph), ref(pp))
        pointers += [e + 0x08, e + 0x30, e + 0x34, e + 0x38]

    fx = struct.pack("<III", len(buf), len(resolves), 1)
    fx += b"".join(struct.pack("<iII", *r) for r in resolves)
    fx += struct.pack(f"<I{len(pointers)}I", len(pointers), *pointers)
    return bytes(buf), fx
