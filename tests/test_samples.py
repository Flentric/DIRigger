"""Checks against real game files.

Game data is not redistributed with this repo. Point DIRIGGER_SAMPLES at a
folder containing extracted hero folders (e.g. hero_logan/hero_logan.msh,
.MeshFixups, .VertexData, .IndexData, .Skin, .SkinFixups); tests are skipped
otherwise.

  DIRIGGER_SAMPLES=/path/to/samples python -m unittest discover tests
"""

import glob
import os
import unittest

from dirigger.ce5.msh import load_msh, NODE_MESH
from dirigger.ce5.skin import parse_skin, build_skin

SAMPLES = os.environ.get("DIRIGGER_SAMPLES", "")
MSHS = sorted(glob.glob(os.path.join(SAMPLES, "**", "*.msh"), recursive=True)) if SAMPLES else []


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) + (a[i][3] if j == 3 else 0.0)
             for j in range(4)] for i in range(3)]


@unittest.skipUnless(MSHS, "set DIRIGGER_SAMPLES to run sample-file tests")
class SampleTests(unittest.TestCase):

    def test_skin_roundtrip(self):
        for p in MSHS:
            base = os.path.splitext(p)[0]
            if not os.path.exists(base + ".Skin"):
                continue
            skin = open(base + ".Skin", "rb").read()
            fix = open(base + ".SkinFixups", "rb").read()
            b, f = build_skin(parse_skin(skin))
            self.assertEqual(b, skin, p)
            self.assertEqual(f, fix, p)

    def test_fixups_point_inside_blob(self):
        for p in MSHS:
            m = load_msh(p)
            for off in m.fixups:
                v = m.blob.u32(off)
                self.assertTrue(v == 0xFFFFFFFF or v < len(m.blob.data), (p, hex(off)))

    def test_inverse_bind_matches_hierarchy(self):
        for p in MSHS:
            m = load_msh(p)
            world = {}
            for n in m.nodes:
                if n.type == NODE_MESH:
                    continue
                world[n.index] = _mul(world[n.parent], n.local) if n.parent >= 0 else n.local
                ident = _mul(world[n.index], n.inv_bind)
                for i in range(3):
                    for j in range(4):
                        # translation is in centimetres, so allow a little float drift there
                        tol = 0.1 if j == 3 else 0.005
                        self.assertAlmostEqual(ident[i][j], 1.0 if i == j else 0.0, delta=tol,
                                               msg=(p, n.name))

    def test_geometry_decodes(self):
        for p in MSHS:
            m = load_msh(p)
            for n in m.mesh_nodes:
                lod = n.mesh.lods[0]
                d = m.decode_lod(lod)
                self.assertEqual(len(d["positions"]), lod.vertex_count)
                for s, idx in enumerate(d["surfaces"]):
                    self.assertEqual(len(idx) % 3, 0)
                    self.assertLess(max(idx), lod.vertex_count)
                    pal = n.mesh.palettes[s]
                    for i in set(idx):
                        if d["joints"]:
                            w = d["weights"][i]
                            for k, j in enumerate(d["joints"][i]):
                                if w[k]:
                                    self.assertLess(j, len(pal), (p, n.name, s))
                # positions sit inside the node's AABB (within quantisation error)
                c, h = n.aabb_center, n.aabb_half
                for pos in d["positions"]:
                    for a in range(3):
                        self.assertLessEqual(abs(pos[a] - c[a]), h[a] + 0.2, (p, n.name))


if __name__ == "__main__":
    unittest.main()
