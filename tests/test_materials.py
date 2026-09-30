"""Material handling of the player builder, on a tiny synthetic template (no game files)."""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

from dirigger.build.player import build_player, atlas_layout
from dirigger.ce5.msh import (Node, Mesh, Lod, MeshFile, Material, NODE_BONE, NODE_MESH,
                              load_msh)
from dirigger.ce5.skin import SkinFile, SkinPreset, parse_skin
from dirigger.io.obj import ObjModel
from dirigger.io.png import write_png, read_png

IDENT = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
BONES = ["bip01", "bip01 spine", "bip01 head", "bip01 l thigh"]


def _template():
    nodes = [Node(i, n, -1 if i == 0 else 0, NODE_BONE, 0, 0, IDENT, IDENT, (0, 0, 0), (0, 0, 0),
                  None, bytes(0xB0)) for i, n in enumerate(BONES)]
    for k, name in enumerate(("body", "head", "head_shadow")):
        mesh = Mesh([k], [[0]], [Lod([3], [], 3, 0, [0])])
        if name == "body":
            # like Logan: a second, larger surface with another material (his hands)
            mesh = Mesh([0, 1], [[0], [0]], [Lod([3, 30], [], 3, 0, [0])])
        nodes.append(Node(len(nodes), name, -1, NODE_MESH, 0, 0, IDENT, IDENT, (0, 0, 0),
                          (0, 0, 0), mesh, bytes(0xB0)))
    mats = [Material("DEFAULT.MAT", 1)] * 3 + [Material("hero_x_body.mat", 1),
                                               Material("hero_x_head.mat", 1),
                                               Material("shadow_def.mat", 1)]
    msh = MeshFile("hero_x.msh", "", nodes, mats, 3, list(range(6)), [], tuple([0] * 17))
    mm = [(0, 3), (1, 4), (2, 5)]
    params = [0x0A00 | p for p in range(6)]
    skin = SkinFile([SkinPreset("X_TPP", mm, [], params), SkinPreset("X_FPP", mm, [], params)])
    return msh, skin


def _model(folder):
    # three separate islands: torso, legs, head; one OBJ material, overlapping 0..1 UVs
    pos = np.array([[0, 100, 0], [10, 100, 0], [0, 120, 0],
                    [0, 40, 0], [10, 40, 0], [0, 60, 0],
                    [0, 160, 0], [10, 160, 0], [0, 175, 0]], float)
    uvs = np.array([[0, 0], [1, 0], [0, 1]], float)
    tris = np.arange(9).reshape(3, 3)
    obj = ObjModel(pos, uvs, tris, np.tile([0, 1, 2], (3, 1)), np.zeros(3, int), ["cj"])
    W = np.zeros((9, len(BONES)))
    W[0:3, 1] = 1     # spine -> torso
    W[3:6, 3] = 1     # thigh -> legs
    W[6:9, 2] = 1     # head
    rig = SimpleNamespace(weights=W, bind_positions=pos)
    tex = {}
    for name, col in (("torso", (255, 0, 0)), ("legs", (0, 255, 0)), ("head", (0, 0, 255))):
        img = np.zeros((32, 32, 4), np.uint8)
        img[..., :3] = col
        img[..., 3] = 255
        tex[name] = os.path.join(folder, name + ".png")
        write_png(tex[name], img)
    return obj, rig, tex


class MaterialTests(unittest.TestCase):

    def _build(self, mode):
        tm, ts = _template()
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        obj, rig, tex = _model(d)
        out = os.path.join(d, "out")
        stats = build_player(tm, ts, obj, rig, out, textures=tex, material_mode=mode,
                             log=lambda *a: None)
        m = load_msh(os.path.join(out, "hero_x.msh"))
        sk = parse_skin(open(os.path.join(out, "hero_x.Skin"), "rb").read())
        return tm, ts, stats, m, sk, out

    def test_template_mode_keeps_game_materials(self):
        tm, ts, stats, m, sk, out = self._build("template")
        # material table and skin maps are the template's, so nothing new must exist in game
        self.assertEqual([x.name for x in m.materials], [x.name for x in tm.materials])
        self.assertEqual(m.slot_count, tm.slot_count)
        self.assertEqual(m.surface_params, tm.surface_params)
        for got, want in zip(sk.skins, ts.skins):
            self.assertEqual(got.material_map, want.material_map)
            self.assertEqual(got.params, want.params)
        slots = {n.name: n.mesh.surface_slots for n in m.mesh_nodes}
        self.assertEqual(slots, {"body": [0], "head": [1], "head_shadow": [2]})

    def test_body_textures_share_an_atlas(self):
        tm, ts, stats, m, sk, out = self._build("template")
        body = m.node_by_name("body")
        d = m.decode_lod(body.mesh.lods[0])
        uv = np.array(d["uvs"])
        cells = atlas_layout(2)[2]
        for (u0, v0, du, dv), tri in zip(cells, (d["surfaces"][0][:3], d["surfaces"][0][3:6])):
            pts = uv[tri]
            self.assertTrue(np.all(pts[:, 0] >= u0 - 1e-3) and np.all(pts[:, 0] <= u0 + du + 1e-3))
            self.assertTrue(np.all(pts[:, 1] >= v0 - 1e-3) and np.all(pts[:, 1] <= v0 + dv + 1e-3))
        atlas = read_png(os.path.join(out, "textures", "hero_x_body.png"))
        h, w = atlas.shape[:2]
        # each body triangle samples its own texture colour from the atlas
        for tri, col in ((d["surfaces"][0][:3], (255, 0, 0)), (d["surfaces"][0][3:6], (0, 255, 0))):
            cu, cv = uv[tri].mean(0)
            self.assertEqual(tuple(atlas[int(cv * h), int(cu * w), :3]), col)
        self.assertTrue(os.path.exists(os.path.join(out, "textures", "hero_x_body.dds")))
        self.assertTrue(os.path.exists(os.path.join(out, "textures", "hero_x_head.dds")))

    def test_custom_mode_names_new_materials(self):
        tm, ts, stats, m, sk, out = self._build("custom")
        names = [x.name for x in m.materials]
        self.assertIn("hero_x_torso.mat", names)
        self.assertIn("shadow_def.mat", names)


if __name__ == "__main__":
    unittest.main()
