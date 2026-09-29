"""Minimal Wavefront OBJ/MTL reader (triangulates polygons, keeps materials)."""

import os
from dataclasses import dataclass, field

import numpy as np


@dataclass
class ObjModel:
    positions: np.ndarray          # (P,3) unique positions
    uvs: np.ndarray                # (T,2)
    tris_pos: np.ndarray           # (F,3) position indices
    tris_uv: np.ndarray            # (F,3) uv indices (-1 when missing)
    tri_material: np.ndarray       # (F,) index into materials
    materials: list                # material names
    textures: dict = field(default_factory=dict)   # material name -> texture path


def _read_mtl(path):
    tex, cur = {}, None
    if not os.path.exists(path):
        return tex
    for line in open(path, encoding="latin-1"):
        s = line.strip().split(None, 1)
        if not s:
            continue
        if s[0] == "newmtl":
            cur = s[1].strip()
        elif s[0].lower() == "map_kd" and cur and len(s) > 1:
            tex[cur] = os.path.join(os.path.dirname(path), s[1].strip().split()[-1])
    return tex


def load_obj(path):
    pos, uv = [], []
    tp, tu, tm = [], [], []
    mats, mat_idx, cur = [], {}, None
    mtllibs = []
    for line in open(path, encoding="latin-1"):
        s = line.split()
        if not s:
            continue
        k = s[0]
        if k == "v":
            pos.append([float(x) for x in s[1:4]])
        elif k == "vt":
            uv.append([float(x) for x in s[1:3]])
        elif k == "usemtl":
            cur = s[1] if len(s) > 1 else "default"
        elif k == "mtllib":
            mtllibs.append(line.split(None, 1)[1].strip())
        elif k == "f":
            if cur not in mat_idx:
                mat_idx[cur] = len(mats)
                mats.append(cur or "default")
            corners = []
            for c in s[1:]:
                parts = c.split("/")
                vi = int(parts[0])
                vi = vi - 1 if vi > 0 else len(pos) + vi
                ti = -1
                if len(parts) > 1 and parts[1]:
                    ti = int(parts[1])
                    ti = ti - 1 if ti > 0 else len(uv) + ti
                corners.append((vi, ti))
            for j in range(1, len(corners) - 1):
                tri = (corners[0], corners[j], corners[j + 1])
                tp.append([c[0] for c in tri])
                tu.append([c[1] for c in tri])
                tm.append(mat_idx[cur])
    textures = {}
    for lib in mtllibs:
        textures.update(_read_mtl(os.path.join(os.path.dirname(path), lib)))
    return ObjModel(np.array(pos, float), np.array(uv, float).reshape(-1, 2),
                    np.array(tp, int), np.array(tu, int), np.array(tm, int), mats, textures)
