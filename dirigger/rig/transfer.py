"""Skin-weight transfer from the template and re-posing into the template bind pose."""

import numpy as np


def vertex_normals(P, F):
    n = np.zeros_like(P)
    fn = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    for k in range(3):
        np.add.at(n, F[:, k], fn)
    return n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)


def skin_points(P, W, mats):
    """Linear blend skinning: P (N,3), W (N,B), mats (B,4,4)."""
    Ph = np.c_[P, np.ones(len(P))]
    out = np.zeros_like(P)
    for b in np.nonzero(W.any(0))[0]:
        out += W[:, b:b + 1] * (Ph @ mats[b].T)[:, :3]
    return out


def transfer_weights(template, fitted_world, P, F, k=6, chunk=256):
    """Weights for custom mesh P/F (CE space, in its own pose).

    The template mesh is first deformed onto the fitted skeleton, then each
    custom vertex takes the distance-weighted average of its k nearest
    template vertices, preferring ones whose normals agree.
    """
    mats = np.array([fitted_world[i] @ np.linalg.inv(template.world[i])
                     for i in range(len(template.names))])
    Tp = skin_points(template.positions, template.weights, mats)
    Tn = vertex_normals(Tp, template.triangles)
    Pn = vertex_normals(P, F)

    W = np.zeros((len(P), template.weights.shape[1]))
    for s in range(0, len(P), chunk):
        q, qn = P[s:s + chunk], Pn[s:s + chunk]
        d = np.linalg.norm(q[:, None, :] - Tp[None, :, :], axis=2)
        dot = qn @ Tn.T
        d = d * (1.0 + 1.5 * (1.0 - np.clip(dot, -1, 1)))
        nn = np.argsort(d, axis=1)[:, :k]
        dn = np.take_along_axis(d, nn, axis=1)
        w = 1.0 / (dn + 0.5) ** 2
        w /= w.sum(1, keepdims=True)
        W[s:s + chunk] = np.einsum("nk,nkb->nb", w, template.weights[nn])
    return W, Tp


def smooth_weights(W, F, iterations=2, amount=0.5):
    n = len(W)
    for _ in range(iterations):
        acc = np.zeros_like(W)
        cnt = np.zeros(n)
        for a, b in ((0, 1), (1, 2), (2, 0)):
            np.add.at(acc, F[:, a], W[F[:, b]])
            np.add.at(acc, F[:, b], W[F[:, a]])
            np.add.at(cnt, F[:, a], 1)
            np.add.at(cnt, F[:, b], 1)
        nb = acc / np.maximum(cnt, 1)[:, None]
        W = (1 - amount) * W + amount * nb
    return W


def limit_weights(W, max_influences=4, min_weight=0.02):
    W = W.copy()
    order = np.argsort(-W, axis=1)
    drop = order[:, max_influences:]
    np.put_along_axis(W, drop, 0.0, axis=1)
    W[W < min_weight] = 0.0
    W /= W.sum(1, keepdims=True) + 1e-12
    return W


def repose(template, fitted_world, P, W):
    """Move the custom mesh from its own pose into the template's bind pose."""
    mats = np.array([template.world[i] @ np.linalg.inv(fitted_world[i])
                     for i in range(len(template.names))])
    return skin_points(P, W, mats)


def tangents(P, N, UV, F):
    """Per-vertex tangent (xyz + handedness w) from UVs."""
    t = np.zeros_like(P)
    b = np.zeros_like(P)
    e1, e2 = P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]]
    d1, d2 = UV[F[:, 1]] - UV[F[:, 0]], UV[F[:, 2]] - UV[F[:, 0]]
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1e-12, r)
    ft = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    fb = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r[:, None]
    for k in range(3):
        np.add.at(t, F[:, k], ft)
        np.add.at(b, F[:, k], fb)
    t = t - N * np.sum(N * t, 1, keepdims=True)
    bad = np.linalg.norm(t, axis=1) < 1e-9
    t[bad] = np.cross(N[bad], [0, 1, 0]) + 1e-6
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    w = np.where(np.sum(np.cross(N, t) * b, 1) < 0, -1.0, 1.0)
    return np.c_[t, w]
