"""Export a compiled CE5 mesh to binary glTF (.glb) for inspection in Blender.

Conversion: model data is Y-up, facing +Z with the character's left on +X,
counter-clockwise front faces, in centimetres. That already matches glTF's
conventions, so we only scale by 0.01.
"""

import json
import math
import struct

from ..ce5.msh import NODE_MESH

SCALE = 0.01


def _conv_point(p):
    return (p[0] * SCALE, p[1] * SCALE, p[2] * SCALE)


def _conv_dir(d):
    return (d[0], d[1], d[2])


def _conv_mat(m):
    """3x4 row-major CE matrix -> glTF column-major 4x4 list (metres)."""
    r = [[m[i][j] for j in range(3)] + [m[i][3] * SCALE] for i in range(3)]
    r.append([0.0, 0.0, 0.0, 1.0])
    return [r[i][j] for j in range(4) for i in range(4)]


def _norm(v):
    l = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / l for c in v)


class _Bin:
    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def add(self, fmt, rows, comp, typ, target=None, minmax=False):
        while len(self.data) % 4:
            self.data.append(0)
        off = len(self.data)
        for r in rows:
            self.data.extend(struct.pack("<" + fmt, *r))
        view = {"buffer": 0, "byteOffset": off, "byteLength": len(self.data) - off}
        if target:
            view["target"] = target
        self.views.append(view)
        acc = {"bufferView": len(self.views) - 1, "componentType": comp,
               "count": len(rows), "type": typ}
        if minmax and rows:
            acc["min"] = [min(r[i] for r in rows) for i in range(len(rows[0]))]
            acc["max"] = [max(r[i] for r in rows) for i in range(len(rows[0]))]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def export_glb(msh, path, skin=None, include_hidden=False):
    """Write `msh` (a MeshFile) to `path`.

    skin: a SkinPreset; its material map names the slots and, unless
    include_hidden is set, its hidden nodes are left out.
    """
    FLOAT, UBYTE, USHORT, UINT = 5126, 5121, 5123, 5125
    ARRAY, ELEMENT = 34962, 34963
    b = _Bin()

    bones = [n for n in msh.nodes if n.type != NODE_MESH]
    bone_gl = {n.index: i for i, n in enumerate(bones)}
    hidden = set() if (skin is None or include_hidden) else {h for h, _ in skin.hidden}

    slot_mat = dict(skin.material_map) if skin else {}
    materials, mat_of_slot = [], {}
    for slot in range(msh.slot_count):
        name = msh.materials[slot_mat[slot]].name if slot in slot_mat else f"slot{slot}"
        mat_of_slot[slot] = len(materials)
        materials.append({"name": name, "extras": {"ce_slot": slot},
                          "pbrMetallicRoughness": {"metallicFactor": 0.0}})

    gl_nodes = []
    for n in bones:
        gl_nodes.append({"name": n.name, "matrix": _conv_mat(n.local),
                         "extras": {"ce_index": n.index, "ce_type": n.type, "ce_flags": n.flags}})
    for n in bones:
        kids = [bone_gl[c.index] for c in bones if c.parent == n.index]
        if kids:
            gl_nodes[bone_gl[n.index]]["children"] = kids

    ibm = b.add("16f", [_conv_mat(n.inv_bind) for n in bones], FLOAT, "MAT4")
    skins = [{"name": msh.name, "joints": list(range(len(bones))), "inverseBindMatrices": ibm,
              "skeleton": bone_gl[bones[0].index]}]

    meshes, mesh_nodes = [], []
    for n in msh.mesh_nodes:
        if n.index in hidden:
            continue
        me = n.mesh
        d = msh.decode_lod(me.lods[0])
        nv = len(d["positions"])

        surf_of = [0] * nv
        for s, idx in enumerate(d["surfaces"]):
            for i in idx:
                surf_of[i] = s

        attrs = {"POSITION": b.add("3f", [_conv_point(p) for p in d["positions"]],
                                   FLOAT, "VEC3", ARRAY, minmax=True)}
        if d["normals"]:
            attrs["NORMAL"] = b.add("3f", [_norm(_conv_dir(v)) for v in d["normals"]],
                                    FLOAT, "VEC3", ARRAY)
        if d["tangents"]:
            rows = [_norm(_conv_dir(t[:3])) + ((-1.0 if t[3] < 0 else 1.0),)
                    for t in d["tangents"]]
            attrs["TANGENT"] = b.add("4f", rows, FLOAT, "VEC4", ARRAY)
        if d["uvs"]:
            attrs["TEXCOORD_0"] = b.add("2f", d["uvs"], FLOAT, "VEC2", ARRAY)
        if d["joints"]:
            joints, weights = [], []
            for i in range(nv):
                pal = me.palettes[surf_of[i]]
                w = d["weights"][i]
                tot = sum(w) or 1
                joints.append(tuple(bone_gl[pal[j]] if w[k] else 0
                                    for k, j in enumerate(d["joints"][i])))
                weights.append(tuple(x / tot for x in w))
            attrs["JOINTS_0"] = b.add("4H", joints, USHORT, "VEC4", ARRAY)
            attrs["WEIGHTS_0"] = b.add("4f", weights, FLOAT, "VEC4", ARRAY)

        prims = []
        for s, idx in enumerate(d["surfaces"]):
            if not idx:
                continue
            acc = b.add("3H", [tuple(idx[i:i + 3]) for i in range(0, len(idx), 3)],
                        USHORT, "SCALAR", ELEMENT)
            b.accessors[acc]["count"] = len(idx)
            prims.append({"attributes": attrs, "indices": acc,
                          "material": mat_of_slot[me.surface_slots[s]]})
        meshes.append({"name": n.name, "primitives": prims,
                       "extras": {"ce_slots": me.surface_slots,
                                  "ce_morphs": me.morphs.names if me.morphs else []}})
        mesh_nodes.append(len(gl_nodes))
        gl_nodes.append({"name": n.name, "mesh": len(meshes) - 1,
                         "skin": 0 if d["joints"] else None,
                         "extras": {"ce_index": n.index, "ce_flags": n.flags}})
        if gl_nodes[-1]["skin"] is None:
            del gl_nodes[-1]["skin"]

    roots = [bone_gl[n.index] for n in bones if n.parent < 0] + mesh_nodes
    doc = {
        "asset": {"version": "2.0", "generator": "DIRigger"},
        "scene": 0,
        "scenes": [{"name": msh.name, "nodes": roots,
                    "extras": {"ce_anim_script": msh.anim_script,
                               "ce_materials": [m.name for m in msh.materials],
                               "ce_skin": skin.name if skin else None}}],
        "nodes": gl_nodes, "meshes": meshes, "materials": materials, "skins": skins,
        "accessors": b.accessors, "bufferViews": b.views,
        "buffers": [{"byteLength": len(b.data)}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    bin_ = bytes(b.data) + b"\0" * (-len(b.data) % 4)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(bin_), 0x004E4942) + bin_)
