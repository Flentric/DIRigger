"""Small helpers for reading the 32-bit pointer-fixup blobs used by Chrome Engine 5."""

import struct

NULL = 0xFFFFFFFF


class Blob:
    """Read-only view over a resource blob whose pointers are offsets into itself."""

    def __init__(self, data: bytes):
        self.data = data

    def u8(self, o):
        return self.data[o]

    def u16(self, o):
        return struct.unpack_from("<H", self.data, o)[0]

    def u32(self, o):
        return struct.unpack_from("<I", self.data, o)[0]

    def f32(self, o):
        return struct.unpack_from("<f", self.data, o)[0]

    def arr(self, fmt, o, n):
        if n == 0:
            return ()
        return struct.unpack_from(f"<{n}{fmt}", self.data, o)

    def ptr(self, o):
        """Return the pointer stored at `o`, or None for a null pointer."""
        p = self.u32(o)
        return None if p == NULL else p

    def cstr(self, o):
        if o is None:
            return None
        end = self.data.index(b"\0", o)
        return self.data[o:end].decode("latin-1")

    def mat34(self, o):
        """Row-major 3x4 affine matrix (translation in column 3)."""
        r = struct.unpack_from("<12f", self.data, o)
        return [list(r[0:4]), list(r[4:8]), list(r[8:12])]


def read_pointer_fixups(data: bytes):
    """MeshFixups: a flat list of offsets of every 32-bit pointer in the .msh blob."""
    return list(struct.unpack(f"<{len(data) // 4}I", data))
