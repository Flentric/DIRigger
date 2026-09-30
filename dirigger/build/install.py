"""Put a built player model (mesh + colour textures) straight into level .rpack files.

Every level pack carries its own copy of the hero, so each pack a level loads
needs the new model. Only the hero's six mesh parts and the mip levels of the
textures named after its materials are replaced; everything else in the pack
is copied as stored.
"""

import os
import struct

import numpy as np

from ..ce5.rpack import load_rpack, build_rpack, TYPE_MESH, TYPE_TEXTURE
from ..io.dxt import compress
from ..io.png import read_png, resize

TEX_FORMATS = {0x11: "dxt1", 0x13: "dxt5"}
MESH_EXTS = (".msh", ".MeshFixups", ".VertexData", ".IndexData", ".Skin", ".SkinFixups")


def texture_info(rp, f):
    """(width, height, mip count, format id) from a texture's 80-byte header part."""
    hdr = rp.part_data(rp.file_parts(f)[0])
    w, h = struct.unpack_from("<HH", hdr, 0)
    mips, fmt = struct.unpack_from("<II", hdr, 8)
    return w, h, mips, fmt


def _mip_chain(rgba, count):
    levels = [rgba]
    img = rgba.astype(np.float32)
    for _ in range(count - 1):
        h, w = img.shape[:2]
        if h > 1:
            img = (img[0::2] + img[1::2]) / 2
        if w > 1:
            img = (img[:, 0::2] + img[:, 1::2]) / 2
        levels.append(np.clip(img + 0.5, 0, 255).astype(np.uint8))
    return levels


def _encode_level(img, fmt):
    h, w = img.shape[:2]
    if h < 4 or w < 4:                      # blocks cover 4x4 even for the 2x2 / 1x1 mips
        img = np.pad(img, ((0, max(0, 4 - h)), (0, max(0, 4 - w)), (0, 0)), mode="edge")
    return compress(img, fmt)


def encode_texture(rp, f, rgba):
    """Encode `rgba` in the texture's own size, format and part layout.

    Returns the new data for each mip part (parts[1:]): the largest mips are one
    part each, stored smallest first; the first data part holds all the small
    ones, largest first.
    """
    w, h, mips, fmt = texture_info(rp, f)
    if fmt not in TEX_FORMATS:
        raise ValueError(f"texture format {fmt:#x} not supported")
    img = resize(rgba, w, h)
    data = [_encode_level(m, TEX_FORMATS[fmt]) for m in _mip_chain(img, mips)]
    parts = rp.file_parts(f)[1:]
    singles = len(parts) - 1
    new = [b"".join(data[singles:])] + data[:singles][::-1]
    old = [rp.parts[p].unpacked for p in parts]
    if [len(x) for x in new] != old:
        raise ValueError(f"mip layout mismatch for {rp.name(f)}: {[len(x) for x in new]} vs {old}")
    return dict(zip(parts, new))


def flat_normal(size=4):
    """A normal map with no bumps. Works whether the shader reads x from red or alpha."""
    img = np.zeros((size, size, 4), np.uint8)
    img[...] = (128, 128, 255, 128)
    return img


def install(rpack_path, out_dir, base, textures=True, flat_normals=True, log=print):
    """Replace hero `base` in one .rpack with the files in `out_dir`. Returns what changed."""
    rp = load_rpack(rpack_path)
    f = rp.find(base, TYPE_MESH)
    if f is None:
        log(f"  {os.path.basename(rpack_path)}: no {base} here, skipped")
        return None
    changed = []
    parts = rp.mesh_parts(f)
    for ext in MESH_EXTS:
        with open(os.path.join(out_dir, base + ext), "rb") as fh:
            rp.replace(parts[ext], fh.read())
    changed.append(f"{base} mesh")

    if textures:
        tex_dir = os.path.join(out_dir, "textures")
        for fn in sorted(os.listdir(tex_dir)) if os.path.isdir(tex_dir) else []:
            stem, ext = os.path.splitext(fn)
            if ext.lower() != ".png":
                continue
            t = rp.find(stem, TYPE_TEXTURE)
            if t is None:
                log(f"  no texture called {stem} in this pack; {fn} not installed")
                continue
            for p, data in encode_texture(rp, t, read_png(os.path.join(tex_dir, fn))).items():
                rp.replace(p, data)
            changed.append(stem)
            n = rp.find(stem + "_nrm", TYPE_TEXTURE)
            if flat_normals and n is not None:
                for p, data in encode_texture(rp, n, flat_normal()).items():
                    rp.replace(p, data)
                changed.append(stem + "_nrm (flat)")

    data = build_rpack(rp)
    tmp = rpack_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, rpack_path)
    return changed


def unpack(rpack_path, name, out_dir):
    """Write a mesh's six parts as <name>.msh, .MeshFixups, ... (a template dump)."""
    rp = load_rpack(rpack_path)
    f = rp.find(name, TYPE_MESH)
    if f is None:
        raise ValueError(f"no mesh called {name} in {rpack_path}")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for ext, p in rp.mesh_parts(f).items():
        path = os.path.join(out_dir, name + ext)
        with open(path, "wb") as fh:
            fh.write(rp.part_data(p))
        written.append(path)
    return written
