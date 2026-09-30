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

from ..ce5.msh import Node, Mesh, MeshFile, Material, Morphs, NODE_MESH, load_msh
from ..ce5.msh_writer import build_msh
from ..ce5.skin import SkinFile, SkinPreset, build_skin
from ..ce5.geometry import BufferBuilder, SKINNED, SHADOW, MORPH, MAX_PALETTE, pack_influences
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


def _main_slot(node):
    """Material slot of a mesh node's largest surface."""
    me = node.mesh
    if not me or not me.surface_slots:
        return None
    counts = me.lods[0].index_counts
    return me.surface_slots[max(range(len(counts)), key=counts.__getitem__)]


def template_slot_plan(template_msh, template_skin):
    """Pick the template slots our parts draw with: {"body", "head", "head_shadow"} -> slot.

    Every slot here is one the template's own skins already map to a material that
    exists in the game, so the output never names a material the game cannot load.
    """
    nodes = {n.name: n for n in template_msh.mesh_nodes}
    body = _main_slot(nodes["body"]) if "body" in nodes else None
    if body is None:
        rest = [n for k, n in nodes.items() if not k.startswith("head") and "hair" not in k]
        if rest:
            big = max(rest, key=lambda n: n.mesh.lods[0].vertex_count)
            body = _main_slot(big)
    if body is None:
        raise ValueError("template has no body mesh to take a material slot from")
    head = _main_slot(nodes["head"]) if "head" in nodes else None
    shadow = _main_slot(nodes["head_shadow"]) if "head_shadow" in nodes else None
    if shadow is None and template_skin:
        for sl, mi in template_skin.skins[0].material_map:
            if template_msh.materials[mi].name.lower() == "shadow_def.mat":
                shadow = sl
                break
    return {"body": body, "head": body if head is None else head, "head_shadow": shadow}


def slot_material_names(template_msh, template_skin):
    """Slot -> material name the template's third-person skin uses for it."""
    presets = template_skin.skins if template_skin else []
    pick = next((p for p in presets if "tpp" in p.name.lower()), presets[0] if presets else None)
    if pick is None:
        return {}
    return {sl: template_msh.materials[mi].name for sl, mi in pick.material_map}


ATLAS_PAD = 1 / 64          # border around each atlas cell, as a fraction of the cell


def atlas_layout(n):
    """Grid for n atlas cells: (cols, rows, [(u0, v0, du, dv)] per cell) in 0..1 UV space."""
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    xf = []
    for k in range(n):
        r, c = divmod(k, cols)
        xf.append(((c + ATLAS_PAD) / cols, (r + ATLAS_PAD) / rows,
                   (1 - 2 * ATLAS_PAD) / cols, (1 - 2 * ATLAS_PAD) / rows))
    return cols, rows, xf


def build_atlas(images, max_size=4096):
    """Lay images out on the `atlas_layout` grid and return the RGBA atlas.

    Each cell has an edge-extended border so mipmaps don't bleed between cells.
    `None` images become flat grey cells.
    """
    from ..io.png import resize
    cols, rows, _ = atlas_layout(len(images))
    big = max([max(im.shape[:2]) for im in images if im is not None] or [512])
    cell = 1 << int(np.ceil(np.log2(max(big, 128))))
    cell = max(128, min(cell, max_size // max(cols, rows)))
    pad = int(cell * ATLAS_PAD)
    inner = cell - 2 * pad
    atlas = np.full((rows * cell, cols * cell, 4), 128, np.uint8)
    atlas[..., 3] = 255
    for k, im in enumerate(images):
        r, c = divmod(k, cols)
        if im is None:
            im = atlas[:inner, :inner]
        tile = np.pad(resize(im, inner, inner), ((pad, pad), (pad, pad), (0, 0)), mode="edge")
        atlas[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = tile
    return atlas


def transfer_morphs(template_msh, head_positions, falloff=(1.0, 4.0)):
    """Give a new head the template head's facial morph targets.

    Each new vertex copies the per-target delta of the nearest template head
    vertex (both are in the template's bind pose, in cm), fading to zero
    between falloff[0] and falloff[1] cm away. Returns (Morphs, raw A record)
    or None when the template head has no morphs.
    """
    node = template_msh.node_by_name("head")
    if node is None or node.mesh.morphs is None:
        return None
    mo = node.mesh.morphs
    tpos = np.frombuffer(mo.base_data[:mo.vertex_count * 12], np.float32).reshape(-1, 3)
    deltas = np.stack([np.frombuffer(t, np.int16).reshape(-1, 3) for t in mo.targets])
    P = np.asarray(head_positions, float)
    near = np.zeros(len(P), int)
    dist = np.zeros(len(P))
    for s in range(0, len(P), 512):
        d = np.linalg.norm(P[s:s + 512, None, :] - tpos[None, :, :], axis=2)
        near[s:s + 512] = d.argmin(1)
        dist[s:s + 512] = d.min(1)
    lo, hi = falloff
    fade = np.clip((hi - dist) / (hi - lo), 0.0, 1.0)
    new = np.round(deltas[:, near, :] * fade[None, :, None]).astype(np.int16)
    targets = [new[k].tobytes() for k in range(len(mo.targets))]
    base = np.asarray(P, np.float32).tobytes()
    return Morphs(list(mo.names), list(mo.remap), [], len(P), 0, base, targets), node.mesh.raw_a


def build_player(template_msh, template_skin, obj, rig, out_dir, base=None, textures=None,
                 materials=None, material_mode="template", log=print):
    """Write the player model. Returns a dict with paths and statistics.

    material_mode:
      "template"  draw with the template's own slots and materials, which exist in the
                  game. Several textures for one material are packed into an atlas.
      "custom"    one new material per texture slot (<base>_<slot>.mat or `materials`).
                  These must be packed into the game as real .mat resources, otherwise
                  the engine has nothing to draw and the model is invisible.
    """
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
    is_head = tri_region == TEX_REGIONS.index("head")

    if material_mode == "template" and template_skin is None:
        log("  template has no .Skin; falling back to custom materials")
        material_mode = "custom"

    cells = {}                                  # cell id -> (u0, v0, du, dv)
    tex_jobs = []                               # (report label, material name, [slot names])
    if material_mode == "template":
        plan = template_slot_plan(template_msh, template_skin)
        mat_names_of = slot_material_names(template_msh, template_skin)
        # every triangle draws with the template's head or body slot
        tri_out = np.where(is_head, plan["head"], plan["body"])
        body_slot = lambda t: int(tri_out[t])
        shadow_slot = plan["head_shadow"]
        # one texture per template slot: slot names sharing a slot go into an atlas
        users = OrderedDict()
        for t in range(len(tri_rv)):
            users.setdefault(int(tri_out[t]), OrderedDict())[int(tri_slot[t])] = None
        for tslot, used in users.items():
            used = list(used)
            label = "head" if tslot == plan["head"] and tslot != plan["body"] else "body"
            tex_jobs.append((label, mat_names_of.get(tslot, f"slot {tslot}"),
                             [slot_names[k] for k in used], tslot))
            if len(used) > 1:
                # each source texture gets a cell in one atlas per template slot
                cells.update({(tslot, k): x for k, x in zip(used, atlas_layout(len(used))[2])})

        def cell_of_tri(t):
            key = (int(tri_out[t]), int(tri_slot[t]))
            return key if key in cells else None
        mats = list(template_msh.materials)
        slot_count = template_msh.slot_count
        surface_params = list(template_msh.surface_params)
    else:
        shadow_slot = len(slot_names)
        n_slots = len(slot_names) + 1
        body_slot = lambda t: int(tri_slot[t])
        cell_of_tri = lambda t: None
        # materials: one DEFAULT per slot (shadow_def for the shadow slot), then ours
        mat_names = [(materials or {}).get(s, f"{base}_{s}.mat") for s in slot_names]
        mats = [Material("DEFAULT.MAT", 1) for _ in slot_names] + [Material("shadow_def.mat", 1)]
        mats += [Material(n, 1) for n in mat_names]
        real_index = {s: n_slots + k for k, s in enumerate(slot_names)}
        slot_count = n_slots
        surface_params = list(range(n_slots + len(slot_names)))
        tex_jobs = [(s, n, [s], None) for s, n in zip(slot_names, mat_names)]

    # --- per-vertex influences ----------------------------------------------
    order = np.argsort(-W, axis=1)[:, :4]
    infl = [[(int(b), float(W[i, b])) for b in order[i] if W[i, b] > 0] for i in range(len(W))]
    tri_bones = [set(b for pi in obj.tris_pos[t] for b, _ in infl[pi]) for t in range(len(tri_rv))]

    buffers = BufferBuilder()

    def uv_in_cell(uv, cell):
        if cell is None:
            return tuple(uv)
        u0, v0, du, dv = cells[cell]
        u, v = np.clip(uv, 0.0, 1.0)
        return (u0 + u * du, v0 + v * dv)

    def make_mesh(tris, layout, slot_of_tri, morph=False):
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
                    cell = cell_of_tri(t)
                    for rv in tri_rv[t]:
                        if (rv, cell) not in vmap:
                            vmap[(rv, cell)] = len(positions)
                            pi = rv_pos[rv]
                            positions.append(tuple(RP[rv]))
                            uvs.append(uv_in_cell(UV[rv], cell))
                            nrm.append(tuple(RN[rv]))
                            tan.append(tuple(RT[rv]))
                            wb, ib = pack_influences([(local[b], w) for b, w in infl[pi]])
                            wts.append(wb)
                            idx.append(ib)
                        tri_list.append(vmap[(rv, cell)])
                surfaces.append(tri_list)
                slots_out.append(slot)
                pals.append(pal)
        if len(positions) > 65535:
            raise ValueError("mesh part has more than 65535 vertices; split the model")
        lod = buffers.add_lod(layout, positions, surfaces, uvs, nrm, tan, wts, idx)
        P = np.array(positions)
        mesh = Mesh(slots_out, pals, [lod])
        if morph:
            res = transfer_morphs(template_msh, P)
            if res:
                mesh.morphs, mesh.raw_a = res
        return mesh, P

    body_tris = [t for t in range(len(tri_rv)) if not is_head[t]]
    head_tris = [t for t in range(len(tri_rv)) if is_head[t]]
    parts = []
    if body_tris:
        parts.append(("body",) + make_mesh(body_tris, SKINNED, body_slot))
    if head_tris:
        has_morphs = bool(template_msh.morph_names)
        parts.append(("head",) + make_mesh(head_tris, MORPH if has_morphs else SKINNED,
                                           body_slot, morph=has_morphs))
        if shadow_slot is not None:
            parts.append(("head_shadow",) + make_mesh(head_tris, SHADOW, lambda t: shadow_slot))
        else:
            log("  template has no shadow slot; skipping head_shadow")

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

    header = list(template_msh.header)
    header[5] = len(parts) + 1
    msh = MeshFile(name=f"{base}.msh", anim_script=template_msh.anim_script, nodes=nodes,
                   materials=mats, slot_count=slot_count, surface_params=surface_params,
                   morph_names=list(template_msh.morph_names) if head_tris else [],
                   morph_table_words=list(template_msh.morph_table_words) if head_tris else [],
                   header=tuple(header))
    msh_bytes, fix_bytes = build_msh(msh)

    # --- skins ------------------------------------------------------------------
    node_index = {n.name: i for i, n in enumerate(nodes)}
    if material_mode == "template":
        matmap, params = None, None            # keep each template preset's own
    else:
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
        presets.append(SkinPreset(tp.name, tp.material_map if matmap is None else matmap, hidden,
                                  tp.params if params is None else params, tp.flags, tp.extra))
    skin_bytes, skinfix_bytes = build_skin(SkinFile(presets))

    # --- write ------------------------------------------------------------------
    os.makedirs(out_dir, exist_ok=True)
    files = {".msh": msh_bytes, ".MeshFixups": fix_bytes, ".VertexData": bytes(buffers.vertex),
             ".IndexData": bytes(buffers.index), ".Skin": skin_bytes, ".SkinFixups": skinfix_bytes}
    for ext, data in files.items():
        with open(os.path.join(out_dir, base + ext), "wb") as f:
            f.write(data)

    from ..io.png import read_png, write_dds, write_png
    tex_dir = os.path.join(out_dir, "textures")
    os.makedirs(tex_dir, exist_ok=True)
    tex_report = []
    for label, mname, used, tslot in tex_jobs:
        stem = os.path.splitext(os.path.basename(mname))[0].replace(" ", "_")
        srcs = [slots[s] if slots[s] and os.path.exists(slots[s]) else None for s in used]
        if len(used) > 1:
            imgs = [read_png(p) if p else None for p in srcs]
            if not any(i is not None for i in imgs):
                tex_report.append((label, mname, None))
                continue
            atlas = build_atlas(imgs)
            write_png(os.path.join(tex_dir, stem + ".png"), atlas)
            write_dds(os.path.join(tex_dir, stem + ".dds"), atlas)
            desc = "atlas of " + ", ".join(
                f"{s}={os.path.basename(p) if p else 'MISSING'}" for s, p in zip(used, srcs))
            tex_report.append((label, mname, f"textures/{stem}.dds ({desc})"))
        elif srcs[0]:
            src = srcs[0]
            shutil.copyfile(src, os.path.join(tex_dir, stem + os.path.splitext(src)[1].lower()))
            write_dds(os.path.join(tex_dir, stem + ".dds"), read_png(src))
            tex_report.append((label, mname, f"textures/{stem}.dds ({os.path.basename(src)})"))
        else:
            tex_report.append((label, mname, None))

    stats = {
        "base": base, "out_dir": out_dir, "material_mode": material_mode,
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
