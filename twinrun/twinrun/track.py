"""Find the skier the camera follows: globally optimal path through per-frame person detections.

Dynamic programming over detections with:
  - unary cost: distance from frame centre (the camera follows the athlete), small detections near
    the top edge (spectators) are penalised, confidence rewarded
  - pairwise cost: motion in stabilized (frame-0) coordinates, converted back to frame pixels,
    plus box-size consistency
  - skips of up to K frames (missed detections) with a per-frame penalty
"""
import numpy as np


def _apply(H, p):
    v = H @ np.array([p[0], p[1], 1.0])
    return v[:2] / v[2]


def _scale(H, p):
    a = _apply(H, p)
    b = _apply(H, (p[0] + 10, p[1]))
    return np.linalg.norm(b - a) / 10.0


def track_main(dets, G, shape, K=12, miss_cost=1.2):
    n = len(dets)
    Hh, W = shape[:2]
    cand = []  # per frame: list of (global_xy, local_scale, height, unary)
    for t in range(n):
        s = _scale(G[t], (W / 2, Hh / 2))
        lst = []
        for d in dets[t]:
            x1, y1, x2, y2 = d["box"]
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            h = y2 - y1
            dc = np.hypot((cx - W / 2) / W, (cy - Hh / 2) / Hh)
            u = 2.5 * dc - 0.5 * d["conf"]
            if y2 < 0.12 * Hh:  # crowd at the top of the slope
                u += 1.0
            lst.append((_apply(G[t], (cx, cy)), s, h * s, u))
        cand.append(lst)

    INF = 1e18
    cost = [np.full(len(c), INF) for c in cand]
    back = [[None] * len(c) for c in cand]
    for t in range(n):
        for k, (g, s, h, u) in enumerate(cand[t]):
            best = u + miss_cost * t  # may start here (skier can enter the frame late)
            arg = None
            for tp in range(max(0, t - K), t):
                gap = t - tp
                for kp, (gp, sp, hp, up) in enumerate(cand[tp]):
                    if cost[tp][kp] >= INF:
                        continue
                    # motion per frame relative to body height (scale-invariant: far & near skier)
                    d_rel = np.linalg.norm(g - gp) / max(min(h, hp), 15.0) / gap
                    size = abs(np.log((h + 1) / (hp + 1))) / gap
                    tr = (d_rel / 0.3) ** 2 + 1.5 * size
                    if d_rel > 1.2:
                        continue
                    c = cost[tp][kp] + tr + miss_cost * (gap - 1) + u
                    if c < best:
                        best, arg = c, (tp, kp)
            cost[t][k] = best
            back[t][k] = arg
    # end within last K frames
    best, end = INF, None
    for t in range(n):  # skier can also leave the frame before the clip ends
        for k in range(len(cand[t])):
            c = cost[t][k] + miss_cost * (n - 1 - t)
            if c < best:
                best, end = c, (t, k)
    idx = [-1] * n
    node = end
    while node is not None:
        t, k = node
        idx[t] = k
        node = back[t][k]
    return idx


def interpolate_boxes(dets, idx, G):
    """Per-frame box of the tracked skier in frame coords; gaps filled via stabilized interpolation."""
    n = len(idx)
    known = [t for t in range(n) if idx[t] >= 0]
    boxes = [None] * n
    for t in known:
        boxes[t] = dets[t][idx[t]]["box"].astype(float)
    if not known:
        return boxes, [False] * n
    # interpolate in global coords: centre + size
    gc = {}
    for t in known:
        x1, y1, x2, y2 = boxes[t]
        c = _apply(G[t], ((x1 + x2) / 2, (y1 + y2) / 2))
        s = _scale(G[t], ((x1 + x2) / 2, (y1 + y2) / 2))
        gc[t] = (c, (x2 - x1) * s, (y2 - y1) * s)
    for t in range(n):
        if boxes[t] is not None:
            continue
        prev = max([k for k in known if k < t], default=None)
        nxt = min([k for k in known if k > t], default=None)
        if prev is None or nxt is None:
            continue
        a = (t - prev) / (nxt - prev)
        c = gc[prev][0] * (1 - a) + gc[nxt][0] * a
        w = gc[prev][1] * (1 - a) + gc[nxt][1] * a
        h = gc[prev][2] * (1 - a) + gc[nxt][2] * a
        Ginv = np.linalg.inv(G[t])
        lc = _apply(Ginv, c)
        s = _scale(G[t], lc)
        boxes[t] = np.array([lc[0] - w / s / 2, lc[1] - h / s / 2, lc[0] + w / s / 2, lc[1] + h / s / 2])
    detected = [i >= 0 for i in idx]
    return boxes, detected
