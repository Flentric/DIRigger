"""Turn an auto-rigged mesh into a Dead Island player model (all six resource parts).

Output layout (everything named after `base`, normally the hero being replaced):

  <base>.msh / .MeshFixups / .VertexData / .IndexData   the mesh
  <base>.Skin / .SkinFixups                             TPP / FPP presets
  textures/<material>.png + .dds                        one texture per material slot
  preview_tpp.glb / preview_fpp.glb                     re-read from the written files

Mesh nodes written:
  body         everything but the head, one material slot per texture
  head         head surfaces; hidden in first person
  head_shadow  copy of the head with shadow_def.mat; shown only in first person,
               so the player keeps a full shadow while the camera sits inside the head
"""

import os
import shutil
import struct
from collections import OrderedDict

import numpy as np

from ..ce5.msh import Node, Mesh, MeshFile, Material, NODE_MESH, NODE_SIZE, load_msh
from ..ce5.msh_writer import build_msh
from ..ce5.skin import SkinFile, SkinPreset, build_skin
from ..ce5.geometry import BufferBuilder, SKINNED, SHADOW, MAX_PALETTE, pack_influences
from ..rig.transfer import vertex_normals, tangents

# texture regions, decided per UV island from skin weights
TEX_REGIONS = ("head", "torso", "legs", "feet")


def tex_region(bone_name):
    n = bone_name.lower()
    if n in ("bip01 head", "eyes", "eyecamera", "eyecamera_param") or "hair" in n:
        return "head"
    for side in "lr":
        p = f"bip01 {side} "
        if n.startswith(p):
            part = n[len(p):]
            if part.startswith(("foot", "toe")):
                return "feet"
            if part.startswith(("thigh", "calf")):
                return "legs"
            return "torso"
    if n in ("bip01", "bip01 pelvis"):
        return "legs"
    return "torso"


def _islands(tri_rv):
    """Group triangles that share an edge with identical position+UV."""
    parent = list(range(len(tri_rv)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    seen = {}
    for t, (a, b, c) in enumerate(tri_rv):
        for e in ((a, b), (b, c), (c, a)):
            k = (min(e), max(e))
            if k in seen:
                parent[find(t)] = find(seen[k])
            else:
                seen[k] = t
    return np.array([find(t) for t in range(len(tri_rv))])


def _split_palettes(tris, tri_bones, limit=MAX_PALETTE):
    """Greedily cut a triangle list into surfaces whose bone sets fit a palette."""
    out, cur, bones = [], [], set()
    for t in tris:
        nb = bones | tri_bones[t]
        if cur and len(nb) > limit:
            out.append((sorted(bones), cur))
            cur, bones = [], set()
            nb = set(tri_bones[t])
        cur.append(t)
        bones = nb
    if cur:
        out.append((sorted(bones), cur))
    return out


def build_player(template_msh, template_skin, obj, rig, out_dir, base=None, textures=None,
                 materials=None, log=print):
    """Write the player model. Returns a dict with paths and statistics."""
    base = base or os.path.splitext(template_msh.name)[0]
    names = [n.name for n in template_msh.bones]
    W = rig.weights
    nb = len(names)

    # --- render vertices (unique position + uv) ---------------------------
    key_of, rv_pos, rv_uv = {}, [], []
    tri_rv = np.zeros_like(obj.tris_pos)
    for t in range(len(obj.tris_pos)):
        for k in range(3):
            key = (int(obj.tris_pos[t, k]), int(obj.tris_uv[t, k]))
            if key not in key_of:
                key_of[key] = len(rv_pos)
                rv_pos.append(key[0])
                rv_uv.append(key[1])
            tri_rv[t, k] = key_of[key]
    rv_pos = np.array(rv_pos)
    uv_src = obj.uvs if len(obj.uvs) else np.zeros((1, 2))
    UV = np.array([uv_src[u] if u >= 0 else (0.0, 0.0) for u in rv_uv])
    UV = np.c_[UV[:, 0], 1.0 - UV[:, 1]]          # OBJ v goes up, D3D v goes down

    Pb = rig.bind_positions
    Npos = vertex_normals(Pb, obj.tris_pos)
    RP, RN = Pb[rv_pos], Npos[rv_pos]
    RT = tangents(RP, RN, UV, tri_rv)

    # --- texture region per UV island --------------------------------------
    reg_of_bone = np.array([TEX_REGIONS.index(tex_region(n)) for n in names])
    reg_w = np.zeros((len(W), len(TEX_REGIONS)))
    for r in range(len(TEX_REGIONS)):
        reg_w[:, r] = W[:, reg_of_bone == r].sum(1)
    isl = _islands(tri_rv)
    tri_region = np.zeros(len(tri_rv), int)
    for i in np.unique(isl):
        m = isl == i
        tri_region[m] = reg_w[np.unique(obj.tris_pos[m])].sum(0).argmax()

    use_obj_mats = len(obj.materials) > 1 and not textures
    slots = OrderedDict()                      # slot name -> texture path
    tri_slot_name = []
    for t in range(len(tri_rv)):
        if use_obj_mats:
            sn = obj.materials[obj.tri_material[t]]
            tex = obj.textures.get(sn)
        else:
            sn = TEX_REGIONS[tri_region[t]]
            tex = (textures or {}).get(sn)
        sn = "".join(c if c.isalnum() or c == "_" else "_" for c in sn).lower()
        if sn not in slots:
            slots[sn] = tex
        tri_slot_name.append(sn)
    slot_names = list(slots)
    if not use_obj_mats:
        slot_names.sort(key=lambda s: ("torso", "legs", "feet", "head").index(s))
    tri_slot = np.array([slot_names.index(s) for s in tri_slot_name])
    shadow_slot = len(slot_names)
    n_slots = len(slot_names) + 1
    is_head = tri_region == TEX_REGIONS.index("head")

    # materials: one DEFAULT per slot (shadow_def for the shadow slot), then ours
    mat_names = [(materials or {}).get(s, f"{base}_{s}.mat") for s in slot_names]
    mats = [Material("DEFAULT.MAT", 1) for _ in slot_names] + [Material("shadow_def.mat", 1)]
    mats += [Material(n, 1) for n in mat_names]
    real_index = {s: n_slots + k for k, s in enumerate(slot_names)}

    # --- per-vertex influences ----------------------------------------------
    order = np.argsort(-W, axis=1)[:, :4]
    infl = [[(int(b), float(W[i, b])) for b in order[i] if W[i, b] > 0] for i in range(len(W))]
    tri_bones = [set(b for pi in obj.tris_pos[t] for b, _ in infl[pi]) for t in range(len(tri_rv))]

    buffers = BufferBuilder()

    def make_mesh(tris, layout, slot_of_tri):
        surfaces, slots_out, pals = [], [], []
        groups = OrderedDict()
        for t in tris:
            groups.setdefault(slot_of_tri(t), []).append(t)
        verts, positions, uvs, nrm, tan, wts, idx = {}, [], [], [], [], [], []
        for slot, ts in groups.items():
            for pal, part in _split_palettes(ts, tri_bones):
                local = {b: k for k, b in enumerate(pal)}
                vmap, tri_list = {}, []
                for t in part:
                    for rv in tri_rv[t]:
                        if rv not in vmap:
                            vmap[rv] = len(positions)
                            pi = rv_pos[rv]
                            positions.append(tuple(RP[rv]))
                            uvs.append(tuple(UV[rv]))
                            nrm.append(tuple(RN[rv]))
                            tan.append(tuple(RT[rv]))
                            wb, ib = pack_influences([(local[b], w) for b, w in infl[pi]])
                            wts.append(wb)
                            idx.append(ib)
                        tri_list.append(vmap[rv])
                surfaces.append(tri_list)
                slots_out.append(slot)
                pals.append(pal)
        if len(positions) > 65535:
            raise ValueError("mesh part has more than 65535 vertices; split the model")
        lod = buffers.add_lod(layout, positions, surfaces, uvs, nrm, tan, wts, idx)
        P = np.array(positions)
        return Mesh(slots_out, pals, [lod]), P

    body_tris = [t for t in range(len(tri_rv)) if not is_head[t]]
    head_tris = [t for t in range(len(tri_rv)) if is_head[t]]
    parts = []
    if body_tris:
        parts.append(("body",) + make_mesh(body_tris, SKINNED, lambda t: int(tri_slot[t])))
    if head_tris:
        parts.append(("head",) + make_mesh(head_tris, SKINNED, lambda t: int(tri_slot[t])))
        parts.append(("head_shadow",) + make_mesh(head_tris, SHADOW, lambda t: shadow_slot))

    # --- nodes ----------------------------------------------------------------
    tmpl_mesh_node = next(n for n in template_msh.nodes if n.type == NODE_MESH)
    nodes = list(template_msh.bones)
    ident = struct.pack("<12f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)
    for name, mesh, P in parts:
        lo, hi = P.min(0), P.max(0)
        raw = bytearray(tmpl_mesh_node.raw)
        raw[0x00:0x30] = ident
        raw[0x30:0x60] = ident
        raw[0x60:0x78] = struct.pack("<6f", *((lo + hi) / 2), *((hi - lo) / 2))
        nodes.append(Node(len(nodes), name, -1, NODE_MESH, 0, tmpl_mesh_node.flags,
                          None, None, None, None, mesh, bytes(raw)))

    surface_params = list(range(n_slots + len(slot_names)))
    header = list(template_msh.header)
    header[5] = len(parts) + 1
    msh = MeshFile(name=f"{base}.msh", anim_script=template_msh.anim_script, nodes=nodes,
                   materials=mats, slot_count=n_slots, surface_params=surface_params,
                   morph_names=[], header=tuple(header))
    msh_bytes, fix_bytes = build_msh(msh)

    # --- skins ------------------------------------------------------------------
    node_index = {n.name: i for i, n in enumerate(nodes)}
    matmap = [(s, real_index[slot_names[s]]) for s in range(len(slot_names))]
    matmap.append((shadow_slot, n_slots - 1))
    params = [0x0A00 | p for p in surface_params]
    presets = []
    tmpl_presets = template_skin.skins if template_skin else [
        SkinPreset("Default", [], [], []), SkinPreset(f"{base}_TPP", [], [], [], extra=0x12),
        SkinPreset(f"{base}_FPP", [], [], [], extra=0x12)]
    for tp in tmpl_presets:
        fpp = "fpp" in tp.name.lower()
        hide = ["head"] if fpp else ["head_shadow"]
        hidden = [(node_index[h], 3) for h in hide if h in node_index]
        presets.append(SkinPreset(tp.name, matmap, hidden, params, tp.flags, tp.extra))
    skin_bytes, skinfix_bytes = build_skin(SkinFile(presets))

    # --- write ------------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    files = {".msh": msh_bytes, ".MeshFixups": fix_bytes, ".VertexData": bytes(buffers.vertex),
             ".IndexData": bytes(buffers.index), ".Skin": skin_bytes, ".SkinFixups": skinfix_bytes}
    for ext, data in files.items():
        with open(os.path.join(out_dir, base + ext), "wb") as f:
            f.write(data)

    tex_dir = os.path.join(out_dir, "textures")
    os.makedirs(tex_dir, exist_ok=True)
    tex_report = []
    for s, mname in zip(slot_names, mat_names):
        src = slots[s]
        stem = os.path.splitext(mname)[0]
        if src and os.path.exists(src):
            from ..io.png import read_png, write_dds
            shutil.copyfile(src, os.path.join(tex_dir, stem + os.path.splitext(src)[1].lower()))
            write_dds(os.path.join(tex_dir, stem + ".dds"), read_png(src))
            tex_report.append((s, mname, os.path.basename(src)))
        else:
            tex_report.append((s, mname, None))

    stats = {
        "base": base, "out_dir": out_dir,
        "parts": [(n, len(P), [len(p) for p in m.palettes], m.surface_slots) for n, m, P in parts],
        "slots": slot_names, "materials": tex_report,
        "skins": [(p.name, [nodes[h].name for h, _ in p.hidden]) for p in presets],
    }
    return stats


def verify_output(out_dir, base):
    """Re-read the written files with the parser and sanity-check them."""
    m = load_msh(os.path.join(out_dir, base + ".msh"))
    for n in m.mesh_nodes:
        d = m.decode_lod(n.mesh.lods[0])
        assert len(d["positions"]) == n.mesh.lods[0].vertex_count
    return m
