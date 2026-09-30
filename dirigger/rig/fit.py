"""Orientation detection and skeleton fitting for humanoid meshes.

Game space ("CE space"): centimetres, Y up, the character faces +Z and its
left side is +X (right-handed). The fitted skeleton reuses the template's
bones, moved and rotated so every joint sits inside the custom mesh.

Fitting works per chain (legs, spine, arms). The same measurements (crotch
height, limb centerlines, fingertip) are taken on the template reference mesh
and on the custom mesh, and each template joint is mapped to the equivalent
spot on the custom one. That handles different proportions and moderate pose
differences (A-pose vs T-pose arms).
"""

import numpy as np

AXES = {"x": 0, "y": 1, "z": 2}

# bone -> child it points at (used to rotate the bone's frame)
AIM = {
    "bip01 pelvis": "bip01 spine",
    "bip01 hspine": "bip01 spine1",
    "bip01 spine": "bip01 spine1",
    "bip01 spine1": "bip01 spine2",
    "bip01 spine2": "bip01 spine3",
    "bip01 spine3": "bip01 hspine1",
    "bip01 hspine1": "bip01 neck",
    "bip01 neck": "bip01 head",
}
for _s in "lr":
    AIM.update({
        f"bip01 {_s} thigh": f"bip01 {_s} calf",
        f"bip01 {_s} calf": f"bip01 {_s} foot",
        f"bip01 {_s} foot": f"bip01 {_s} toe0",
        f"bip01 {_s} clavicle": f"bip01 {_s} upperarm",
        f"bip01 {_s} upperarm": f"bip01 {_s} forearm",
        f"bip01 {_s} forearm": f"bip01 {_s} hand",
        f"bip01 {_s} hand": f"bip01 {_s} finger2",
    })

REGIONS = ("torso", "head", "l_arm", "r_arm", "l_leg", "r_leg")


def bone_region(name):
    n = name.lower()
    for side in "lr":
        if n.startswith(f"bip01 {side} "):
            part = n[8:]
            if part.startswith(("thigh", "calf", "foot", "toe")):
                return f"{side}_leg"
            if part.startswith("clavicle"):
                return "torso"
            return f"{side}_arm"
    if n in ("bip01 head", "eyes", "eyecamera", "eyecamera_param") or "head" in n or "hair" in n:
        return "head"
    return "torso"


def region_labels(W, names):
    """Per-vertex region index (into REGIONS) from skin weights W (N,B)."""
    reg = np.array([REGIONS.index(bone_region(n)) for n in names])
    acc = np.zeros((len(W), len(REGIONS)))
    for r in range(len(REGIONS)):
        acc[:, r] = W[:, reg == r].sum(1)
    return acc.argmax(1)


SPINE = ["bip01", "bip01 pelvis", "bip01 hspine", "bip01 spine", "bip01 spine1", "bip01 spine2",
         "bip01 spine3", "bip01 hspine1", "bip01 neck", "bip01 head"]


# ----------------------------------------------------------------------------
# orientation

def _axis_vec(spec):
    spec = spec.strip().lower()
    sign = -1.0 if spec.startswith("-") else 1.0
    v = np.zeros(3)
    v[AXES[spec.lstrip("+-")]] = sign
    return v


def detect_orientation(P, up=None, forward=None):
    """Return (R, info): R (3x3) maps source coordinates into CE space."""
    ext = P.max(0) - P.min(0)
    mid = (P.max(0) + P.min(0)) / 2

    if up is None or forward is None:
        # the lateral axis is the mirror-symmetric one; forward has the smallest extent
        asym = []
        for a in range(3):
            c = P[:, a] - mid[a]
            asym.append(np.abs(np.sort(c) + np.sort(-c)[::-1]).mean() / (ext[a] + 1e-9))
        fwd_axis = int(np.argmin(ext))
        rest = [a for a in range(3) if a != fwd_axis]
        lat_axis = min(rest, key=lambda a: asym[a])
        up_axis = [a for a in rest if a != lat_axis][0]

        u = P[:, up_axis]
        lo, hi = u.min(), u.max()
        h = hi - lo
        top = P[u > hi - 0.08 * h]
        bot = P[u < lo + 0.08 * h]
        # feet are spread apart; the head is narrow
        up_sign = 1.0 if np.ptp(top[:, lat_axis]) < np.ptp(bot[:, lat_axis]) else -1.0
        upv = np.zeros(3)
        upv[up_axis] = up_sign
        if up is not None:
            upv = _axis_vec(up)
            up_axis = int(np.argmax(np.abs(upv)))

        if forward is None:
            height = P @ upv
            h0 = height.min()
            feet = P[height < h0 + 0.04 * h]
            shins = P[(height > h0 + 0.12 * h) & (height < h0 + 0.2 * h)]
            fs = 1.0 if feet[:, fwd_axis].mean() > shins[:, fwd_axis].mean() else -1.0
            fwv = np.zeros(3)
            fwv[fwd_axis] = fs
        else:
            fwv = _axis_vec(forward)
    else:
        upv, fwv = _axis_vec(up), _axis_vec(forward)

    left = np.cross(upv, fwv)
    R = np.vstack([left, upv, fwv])
    return R, {"up": upv, "forward": fwv, "left": left}


# ----------------------------------------------------------------------------
# measurements

def crotch_height(P, H):
    """Lowest height (above the knees) where the mesh crosses the midline."""
    xc = np.median(P[:, 0])
    near = P[np.abs(P[:, 0] - xc) < 0.025 * H]
    ys = near[near[:, 1] > 0.3 * H][:, 1]
    return float(ys.min()) if len(ys) else 0.47 * H


def _centerline(Q, param, t, hw):
    """Centre of the slice of Q at parameter t (bounding-box midpoint, so it
    does not depend on how densely each side is tessellated)."""
    for k in range(6):
        sel = np.abs(param - t) < hw * (1.6 ** k)
        if sel.sum() >= 3:
            S = Q[sel]
            return (S.min(0) + S.max(0)) / 2
    return (Q.min(0) + Q.max(0)) / 2


def rot_between(a, b):
    """Smallest rotation taking unit direction a onto b."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c < -0.999999:
        axis = np.cross(a, [1, 0, 0])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1, 0])
        axis /= np.linalg.norm(axis)
        return 2 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1 + c)


class _Measure:
    """Landmarks of one mesh (template or custom), in CE space.

    With `labels` (region per vertex, from skin weights) limbs are separated
    exactly; without, a rough geometric split is used (first pass).
    """

    def __init__(self, P, labels=None):
        self.P = P
        self.labels = labels
        self.floor = float(P[:, 1].min())
        self.top = float(P[:, 1].max())
        self.H = self.top - self.floor
        self.xc = float(np.median(P[:, 0]))
        H = self.H
        if labels is None:
            self.crotch = self.floor + 0.45 * H
            self.torso = P[(np.abs(P[:, 0] - self.xc) < 0.09 * H) & (P[:, 1] > self.crotch)]
            self.legs = {s: P[(P[:, 1] < self.crotch) & (sgn * (P[:, 0] - self.xc) > 0)]
                         for s, sgn in (("l", 1), ("r", -1))}
        else:
            lab = np.array(REGIONS)[labels]
            self.torso = P[(lab == "torso") | (lab == "head")]
            self.legs = {s: P[lab == f"{s}_leg"] for s in "lr"}
            self.crotch = float(min(self.legs["l"][:, 1].max(), self.legs["r"][:, 1].max()))

    def torso_z(self, y):
        # average a few neighbouring slices to keep the spine smooth
        H = self.H
        return float(np.mean([_centerline(self.torso, self.torso[:, 1], y + d * H, 0.02 * H)[2]
                              for d in (-0.03, -0.015, 0.0, 0.015, 0.03)]))

    def leg_center(self, side, y):
        Q = self.legs[side]
        return _centerline(Q, Q[:, 1], y, 0.02 * self.H)

    def foot_length(self, side):
        Q = self.legs[side]
        f = Q[Q[:, 1] < self.floor + 0.04 * self.H]
        return float(np.ptp(f[:, 2])) if len(f) else 0.14 * self.H

    def arm(self, side, shoulder):
        sgn = 1 if side == "l" else -1
        P = self.P
        if self.labels is not None:
            Q = P[np.array(REGIONS)[self.labels] == f"{side}_arm"]
        else:
            sel = (sgn * (P[:, 0] - self.xc) > abs(shoulder[0] - self.xc) * 1.4) & (P[:, 1] > self.crotch)
            Q = P[sel]
        d = np.linalg.norm(Q - shoulder, axis=1)
        tip = Q[np.argmax(d)]
        L = float(np.linalg.norm(tip - shoulder))
        axis = (tip - shoulder) / L
        param = ((Q - shoulder) @ axis) / L
        return Q, param, axis, L


# ----------------------------------------------------------------------------
# fitting

def fit_skeleton(template, P, labels=None):
    """Fit the template skeleton to custom mesh points P (CE space, same up/forward).

    P should already be scaled to the template's height with the floor at the
    template's floor. `labels` (region per vertex of P) enables the precise
    second pass. Returns (world, info): world is (B,4,4) fitted rigid frames.
    """
    names = template.names
    J = template.joint_pos
    Tw = template.world
    if labels is None:
        mt, mc = _Measure(template.positions), _Measure(P)
    else:
        mt = _Measure(template.positions, region_labels(template.weights, names))
        mc = _Measure(P, labels)
    s_up = s_leg = mc.H / mt.H

    def map_y(y):
        return mc.floor + (y - mt.floor) * s_up

    pos = {}
    idx = {n: i for i, n in enumerate(names)}

    # spine chain: keep on the midline, follow the custom torso's depth profile
    for n in SPINE:
        if n not in idx:
            continue
        j = J[idx[n]]
        y = map_y(j[1])
        z = mc.torso_z(y) + (j[2] - mt.torso_z(j[1])) * s_up
        pos[n] = np.array([mc.xc, y, z])

    for side, sgn in (("l", 1), ("r", -1)):
        # legs
        hip = J[idx[f"bip01 {side} thigh"]]
        y_hip = map_y(hip[1])
        wt = np.abs(mt.legs[side][:, 0] - mt.xc).mean()
        wc = np.abs(mc.legs[side][:, 0] - mc.xc).mean()
        pos[f"bip01 {side} thigh"] = np.array([mc.xc + (hip[0] - mt.xc) * wc / wt, y_hip,
                                               mc.torso_z(y_hip) + (hip[2] - mt.torso_z(hip[1])) * s_up])
        for n in ("calf", "foot"):
            j = J[idx[f"bip01 {side} {n}"]]
            y = map_y(j[1])
            off = (j - mt.leg_center(side, j[1])) * s_leg
            c = mc.leg_center(side, y)
            pos[f"bip01 {side} {n}"] = np.array([c[0] + off[0], y, c[2] + off[2]])
        ft, fc = mt.foot_length(side), mc.foot_length(side)
        d = J[idx[f"bip01 {side} toe0"]] - J[idx[f"bip01 {side} foot"]]
        pos[f"bip01 {side} toe0"] = pos[f"bip01 {side} foot"] + d * [fc / ft, s_leg, fc / ft]

        # shoulder girdle, relative to the top of the spine
        top = pos["bip01 hspine1"]
        jt = J[idx["bip01 hspine1"]]
        for n in ("clavicle", "upperarm"):
            pos[f"bip01 {side} {n}"] = top + (J[idx[f"bip01 {side} {n}"]] - jt) * s_up

        # arm chain along each mesh's own arm axis
        sh_t, sh_c = J[idx[f"bip01 {side} upperarm"]], pos[f"bip01 {side} upperarm"]
        Qt, pt, at, Lt = mt.arm(side, sh_t)
        Qc, pc, ac, Lc = mc.arm(side, sh_c)
        R = rot_between(at, ac)
        for n in ("forearm", "hand", "finger2"):
            j = J[idx[f"bip01 {side} {n}"]]
            t = float((j - sh_t) @ at / Lt)
            off = j - _centerline(Qt, pt, t, 0.03)
            pos[f"bip01 {side} {n}"] = _centerline(Qc, pc, t, 0.03) + R @ off * (Lc / Lt)

    # frames: rotate each template frame so it aims at its fitted child
    B = len(names)
    rot = [np.eye(3)] * B
    world = np.zeros((B, 4, 4))
    for i, n in enumerate(names):
        p = template.parents[i]
        a = AIM.get(n)
        if n in pos and a in pos:
            rot[i] = rot_between(J[idx[a]] - J[i], pos[a] - pos[n])
        elif p >= 0:
            rot[i] = rot[p]
        if n not in pos:
            if p < 0:
                pos[n] = J[i].copy()
            else:
                k = 1.0
                pa = AIM.get(names[p])
                if pa in pos and names[p] in pos:
                    k = np.linalg.norm(pos[pa] - pos[names[p]]) / np.linalg.norm(J[idx[pa]] - J[p])
                else:
                    k = s_up
                pos[n] = pos[names[p]] + rot[p] @ (J[i] - J[p]) * k
        world[i] = np.eye(4)
        world[i][:3, :3] = rot[i] @ Tw[i][:3, :3]
        world[i][:3, 3] = pos[n]
    info = {"scale_up": s_up, "scale_leg": s_leg, "crotch": mc.crotch, "height": mc.H,
            "template_height": mt.H}
    return world, info
