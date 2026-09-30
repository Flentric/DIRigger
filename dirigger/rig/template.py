"""Skeleton + skinned reference mesh taken from a game model (e.g. hero_logan)."""

from dataclasses import dataclass

import numpy as np

from ..ce5.msh import NODE_MESH


def mat4(m34):
    return np.vstack([np.array(m34, float), [0.0, 0.0, 0.0, 1.0]])


@dataclass
class Template:
    msh: object                  # MeshFile
    bones: list                  # Node list (non-mesh nodes), index == node index
    names: list
    parents: np.ndarray
    world: np.ndarray            # (B,4,4) bind world transforms
    positions: np.ndarray        # (V,3) reference mesh
    triangles: np.ndarray        # (F,3)
    weights: np.ndarray          # (V,B) dense skin weights

    def index(self, name):
        return self.names.index(name)

    @property
    def joint_pos(self):
        return self.world[:, :3, 3]


def load_template(msh, mesh_names=("body", "head")):
    bones = [n for n in msh.nodes if n.type != NODE_MESH]
    assert all(b.index == i for i, b in enumerate(bones)), "bones must come first"
    nb = len(bones)
    world = np.zeros((nb, 4, 4))
    for b in bones:
        L = mat4(b.local)
        world[b.index] = world[b.parent] @ L if b.parent >= 0 else L

    P, F, W = [], [], []
    base = 0
    for name in mesh_names:
        node = msh.node_by_name(name)
        if node is None:
            continue
        me = node.mesh
        d = msh.decode_lod(me.lods[0])
        n = len(d["positions"])
        w = np.zeros((n, nb))
        surf_of = np.zeros(n, int)
        for s, idx in enumerate(d["surfaces"]):
            surf_of[idx] = s
            F.append(np.array(idx).reshape(-1, 3) + base)
        for i in range(n):
            pal = me.palettes[surf_of[i]]
            for j, wt in zip(d["joints"][i], d["weights"][i]):
                if wt:
                    w[i, pal[j]] += wt / 255.0
        P.append(np.array(d["positions"]))
        W.append(w)
        base += n
    return Template(msh, bones, [b.name for b in bones], np.array([b.parent for b in bones]),
                    world, np.vstack(P), np.vstack(F), np.vstack(W))
