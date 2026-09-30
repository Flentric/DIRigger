"""Auto-rig a static humanoid mesh onto a Dead Island template skeleton.

Steps:
  1. rotate the mesh into game space (auto-detected or given up/forward axes)
     and scale it to the template's height
  2. fit the template skeleton to the mesh (rough pass)
  3. transfer skin weights from the template's own skinned body/head
  4. split the mesh into regions with those weights and fit again (precise pass)
  5. transfer weights again, smooth and limit them to 4 influences
  6. re-pose the mesh into the template's bind pose, so the template skeleton
     (and therefore every game animation) can be used unchanged
"""

from dataclasses import dataclass

import numpy as np

from .fit import detect_orientation, fit_skeleton, region_labels
from .transfer import transfer_weights, smooth_weights, limit_weights, repose


@dataclass
class RigResult:
    rotation: np.ndarray       # source -> game-space rotation
    scale: float               # source units -> cm
    offset: np.ndarray         # added after rotation+scale
    positions: np.ndarray      # (P,3) mesh in its own pose, game space
    bind_positions: np.ndarray # (P,3) mesh re-posed to the template bind pose
    weights: np.ndarray        # (P,B)
    labels: np.ndarray         # (P,) region index
    fitted_world: np.ndarray   # (B,4,4)
    info: dict


def autorig(template, positions, triangles, up=None, forward=None, keep_size=False,
            unit_scale=None, log=print):
    R, orient = detect_orientation(positions, up, forward)
    P = positions @ R.T
    th = template.positions[:, 1]
    t_h = th.max() - th.min()
    if keep_size:
        scale = unit_scale or (100.0 if np.ptp(P[:, 1]) < 10 else 1.0)
    else:
        scale = t_h / np.ptp(P[:, 1])
    P = P * scale
    off = np.array([-np.median(P[:, 0]), th.min() - P[:, 1].min(), 0.0])
    # line the torso up with the template's in depth
    tz = np.median(template.positions[:, 2])
    off[2] = tz - np.median(P[:, 2])
    P = P + off
    log(f"  orientation: up={orient['up'].tolist()} forward={orient['forward'].tolist()} "
        f"scale={scale:.4g} height={np.ptp(P[:, 1]):.1f}cm")

    world, info = fit_skeleton(template, P)
    W, _ = transfer_weights(template, world, P, triangles)
    labels = region_labels(W, template.names)
    world, info = fit_skeleton(template, P, labels)
    W, _ = transfer_weights(template, world, P, triangles)
    W = limit_weights(smooth_weights(W, triangles, iterations=2, amount=0.4))
    labels = region_labels(W, template.names)
    bind = repose(template, world, P, W)
    log(f"  fitted skeleton, {int((W > 0).sum(1).mean() * 10) / 10} influences/vertex on average")
    return RigResult(R, scale, off, P, bind, W, labels, world, info)
