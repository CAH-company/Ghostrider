"""Cross-video alignment, time-gap computation and rendering of the ghost overlay."""
import subprocess
import cv2
import numpy as np

from .analyze import features, match_h, iter_frames
from .track import track_main, interpolate_boxes

GHOST_COLOR = (255, 200, 40)   # BGR: light blue/cyan
A_COLOR = (40, 90, 255)        # BGR: orange-red


def _apply(H, p):
    v = H @ np.array([p[0], p[1], 1.0])
    return v[:2] / v[2]


def _corners(shape):
    h, w = shape[:2]
    return np.float32([[0, 0], [w, 0], [w, h], [0, h]])


def _h_to_pts(H, shape):
    return cv2.perspectiveTransform(_corners(shape)[None], H)[0]


def _pts_to_h(pts, shape):
    return cv2.getPerspectiveTransform(_corners(shape), np.float32(pts))


def _disp(H1, H2, shape):
    return float(np.mean(np.linalg.norm(_h_to_pts(H1, shape) - _h_to_pts(H2, shape), axis=1)))


def align(path_a, path_b, an_a, an_b, progress=None):
    """H[t]: maps B frame t into A frame t (inputs already start-synchronised)."""
    GA, GB = an_a["G"], an_b["G"]
    shape = an_a["shape"]
    n = min(len(GA), len(GB))
    Hs, ok = [None] * n, [False] * n
    last = None  # (t, H) last accepted direct estimate
    ia, ib = iter_frames(path_a), iter_frames(path_b)
    for t in range(n):
        fa, fb = next(ia), next(ib)
        boxes_a = [d["box"] for d in an_a["dets"][t]]
        boxes_b = [d["box"] for d in an_b["dets"][t]]
        Hd, k = match_h(features(fb, boxes_b), features(fa, boxes_a), ratio=0.8, min_inl=30)
        ref = None
        if last is not None:
            tp, Hp = last
            # propagate last good estimate with each video's own camera motion
            ref = np.linalg.inv(GA[t]) @ GA[tp] @ Hp @ np.linalg.inv(GB[tp]) @ GB[t]
            ref /= ref[2, 2]
        accept = Hd is not None and (ref is None or _disp(Hd, ref, shape) < 80 or k > 150)
        if accept:
            Hs[t], ok[t] = Hd, True
            last = (t, Hd)
        elif ref is not None:
            Hs[t] = ref
        if progress and t % 10 == 0:
            progress(t / n)
    # frames before the first good estimate: propagate backwards
    first = next((t for t in range(n) if ok[t]), None)
    if first is None:
        raise RuntimeError("Could not align the two videos - were they filmed from the same spot?")
    for t in range(first - 1, -1, -1):
        Hp = Hs[first]
        Hs[t] = np.linalg.inv(GA[t]) @ GA[first] @ Hp @ np.linalg.inv(GB[first]) @ GB[t]
    # temporal smoothing of corner trajectories (removes jitter between independent estimates)
    P = np.stack([_h_to_pts(H / H[2, 2], shape) for H in Hs])  # n x 4 x 2
    Ps = P.copy()
    r = 3
    for t in range(n):
        a, b = max(0, t - r), min(n, t + r + 1)
        Ps[t] = np.median(P[a:b], axis=0) * 0.5 + np.mean(P[a:b], axis=0) * 0.5
    Hs = [_pts_to_h(p, shape) for p in Ps]
    return Hs, ok


def _smooth(x, r=4):
    x = np.asarray(x, float)
    out = x.copy()
    for i in range(len(x)):
        a, b = max(0, i - r), min(len(x), i + r + 1)
        out[i] = x[a:b].mean(axis=0)
    return out


def compute_gap(boxes_a, boxes_b, GA, Hs, fps):
    """Time difference at the same point of the course. gap[t] > 0 => A is slower than B (A behind).

    Both skiers' feet are mapped into A's stabilised coordinates; the two trajectories are aligned
    with banded, open-ended DTW (distance normalised by skier height, so far/near parts weigh equally).
    """
    n = len(Hs)
    pa = np.full((n, 2), np.nan)
    pb = np.full((n, 2), np.nan)
    ha = np.full(n, np.nan)
    for t in range(n):
        if boxes_a[t] is not None:
            x1, y1, x2, y2 = boxes_a[t]
            pa[t] = _apply(GA[t], ((x1 + x2) / 2, y2))
            top = _apply(GA[t], ((x1 + x2) / 2, y1))
            ha[t] = np.linalg.norm(pa[t] - top)
        if boxes_b[t] is not None:
            x1, y1, x2, y2 = boxes_b[t]
            pb[t] = _apply(GA[t], _apply(Hs[t], ((x1 + x2) / 2, y2)))
    ia = np.where(~np.isnan(pa[:, 0]))[0]
    ib = np.where(~np.isnan(pb[:, 0]))[0]
    if len(ia) < 10 or len(ib) < 10:
        return np.full(n, np.nan), None
    A = _smooth(pa[ia], 3)
    B = _smooth(pb[ib], 3)
    hA = np.maximum(_smooth(ha[ia], 10), 8.0)
    band = int(3 * fps)
    na, nb = len(A), len(B)
    INF = 1e18
    D = np.full((na, nb), INF)
    for i in range(na):
        j0 = max(0, np.searchsorted(ib, ia[i] - band))
        j1 = min(nb, np.searchsorted(ib, ia[i] + band))
        if j1 <= j0:
            continue
        d = np.linalg.norm(B[j0:j1] - A[i], axis=1) / hA[i]
        row = np.full(j1 - j0, INF)
        prev = D[i - 1] if i > 0 else None
        for k, j in enumerate(range(j0, j1)):
            if i == 0 or j == 0:
                best = 0.0  # open begin
                if i > 0:
                    best = min(best, D[i - 1, j])
            else:
                best = min(prev[j - 1], prev[j], row[k - 1] if k > 0 else INF)
            row[k] = d[k] + best
        D[i, j0:j1] = row
    # open end: last row or last column
    i, j = na - 1, int(np.argmin(D[na - 1]))
    jc = int(np.argmin(D[:, nb - 1]))
    if D[jc, nb - 1] / max(jc + 1, 1) < D[i, j] / na:
        i, j = jc, nb - 1
    match = {}
    while i >= 0 and j >= 0 and D[i, j] < INF:
        match.setdefault(i, []).append(j)
        if i == 0 or j == 0:
            break
        c = [D[i - 1, j - 1], D[i - 1, j], D[i, j - 1]]
        k = int(np.argmin(c))
        i, j = (i - 1, j - 1) if k == 0 else (i - 1, j) if k == 1 else (i, j - 1)
    gap = np.full(n, np.nan)
    rows = sorted(match)
    if len(rows) < 10:
        return gap, None
    s_of = np.array([np.mean([ib[j] for j in match[r]]) for r in rows], float)
    edge = np.array([any(j in (0, nb - 1) for j in match[r]) for r in rows])
    s_of = _smooth(np.maximum.accumulate(s_of), int(fps * 0.25))
    for r, sv, e in zip(rows, s_of, edge):
        if not e:
            gap[ia[r]] = (ia[r] - sv) / fps
    v = ~np.isnan(gap)
    if v.sum() > 5:
        g = gap[v]
        med = np.array([np.median(g[max(0, i - 7):i + 8]) for i in range(len(g))])
        gap[v] = _smooth(med, 5)
    return gap, {"pa": pa, "pb": pb}


def _full_mask(det, shape):
    m = np.zeros(shape[:2], np.uint8)
    if det is None or det.get("mask") is None:
        return None
    (x1, y1, x2, y2), crop = det["mask"]
    m[y1:y2, x1:x2] = crop[: y2 - y1, : x2 - x1]
    return m


def _diff_mask(fa, warped_b, box, shape):
    """Fallback silhouette when YOLO missed B: pixels in B's box that differ from A."""
    m = np.zeros(shape[:2], np.uint8)
    if box is None:
        return m
    x1, y1, x2, y2 = np.clip(np.asarray(box).astype(int), 0, [shape[1], shape[0], shape[1], shape[0]])
    if x2 <= x1 or y2 <= y1:
        return m
    d = cv2.absdiff(cv2.cvtColor(fa[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY),
                    cv2.cvtColor(warped_b[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY))
    m[y1:y2, x1:x2] = (d > 40).astype(np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))


def _warp_box(H, box):
    x1, y1, x2, y2 = box
    p = cv2.perspectiveTransform(np.float32([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]]), H)[0]
    return np.array([p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()])


def _fmt_gap(g):
    return f"{'+' if g >= 0 else '-'}{abs(g):.2f}"


def _label(img, text, x, y, color, scale):
    font = cv2.FONT_HERSHEY_DUPLEX
    (tw, th), _ = cv2.getTextSize(text, font, scale, 1)
    pad = int(5 * scale)
    x, y = int(x - tw / 2), int(y)
    cv2.rectangle(img, (x - pad, y - th - pad), (x + tw + pad, y + pad), color, -1)
    cv2.putText(img, text, (x, y), font, scale, (255, 255, 255), max(1, int(scale * 1.5)), cv2.LINE_AA)


def _hud(img, t, fps, gap, gap_all, name_a, name_b, s):
    h, w = img.shape[:2]
    font = cv2.FONT_HERSHEY_DUPLEX
    # top-left panel
    ov = img.copy()
    pw, ph = int(300 * s), int(96 * s)
    cv2.rectangle(ov, (int(12 * s), int(12 * s)), (int(12 * s) + pw, int(12 * s) + ph), (25, 25, 25), -1)
    cv2.addWeighted(ov, 0.6, img, 0.4, 0, img)
    x0, y0 = int(24 * s), int(44 * s)
    cv2.putText(img, f"{t / fps:5.2f} s", (x0, y0), font, 0.9 * s, (255, 255, 255), max(1, int(2 * s)), cv2.LINE_AA)
    if gap is not None and not np.isnan(gap):
        col = (90, 220, 90) if gap < 0 else (80, 80, 255)
        txt = f"{name_a} {_fmt_gap(gap)} s"
        cv2.putText(img, txt, (x0, y0 + int(40 * s)), font, 0.9 * s, col, max(1, int(2 * s)), cv2.LINE_AA)
        sub = "ahead" if gap < 0 else "behind"
        cv2.putText(img, f"vs {name_b} ({sub})", (x0 + int(165 * s), y0 + int(40 * s)), font, 0.45 * s,
                    (220, 220, 220), 1, cv2.LINE_AA)
    else:
        cv2.putText(img, f"{name_a} vs {name_b}", (x0, y0 + int(40 * s)), font, 0.7 * s, (220, 220, 220), 1, cv2.LINE_AA)
    # gap chart (bottom)
    valid = ~np.isnan(gap_all)
    if valid.sum() < 5:
        return
    cw, ch = int(w * 0.42), int(h * 0.16)
    cx0, cy0 = w - cw - int(12 * s), h - ch - int(12 * s)
    ov = img.copy()
    cv2.rectangle(ov, (cx0, cy0), (cx0 + cw, cy0 + ch), (25, 25, 25), -1)
    cv2.addWeighted(ov, 0.6, img, 0.4, 0, img)
    g = gap_all[valid]
    m = max(0.1, np.abs(g).max()) * 1.15
    xs = np.where(valid)[0]
    n = len(gap_all)
    def px(i, v):
        return int(cx0 + 8 + (cw - 16) * i / max(1, n - 1)), int(cy0 + ch / 2 - (ch / 2 - 8) * v / m)
    cv2.line(img, (cx0 + 8, cy0 + ch // 2), (cx0 + cw - 8, cy0 + ch // 2), (150, 150, 150), 1)
    pts = [px(i, v) for i, v in zip(xs, g)]
    for p, q, v in zip(pts[:-1], pts[1:], g[1:]):
        cv2.line(img, p, q, (90, 220, 90) if v < 0 else (80, 80, 255), max(1, int(2 * s)), cv2.LINE_AA)
    cx = int(cx0 + 8 + (cw - 16) * t / max(1, n - 1))
    cv2.line(img, (cx, cy0 + 4), (cx, cy0 + ch - 4), (255, 255, 255), 1)
    cv2.putText(img, f"{name_a} faster", (cx0 + 10, cy0 + ch - 8), font, 0.38 * s, (90, 220, 90), 1, cv2.LINE_AA)
    cv2.putText(img, f"{name_a} slower", (cx0 + 10, cy0 + int(16 * s)), font, 0.38 * s, (80, 80, 255), 1, cv2.LINE_AA)


def render(path_a, path_b, an_a, an_b, Hs, boxes_a, boxes_b, gap, out_path,
           name_a="A", name_b="B", ghost_alpha=0.9, progress=None, scale=1.0, hi_a=None, hi_b=None):
    """Render overlay. If hi_a/hi_b given (higher-res copies), render at that resolution with scaled H."""
    shape = an_a["shape"]
    fps = an_a["fps"]
    n = len(Hs)
    src_a, src_b = hi_a or path_a, hi_b or path_b
    cap = cv2.VideoCapture(src_a)
    W, Hh = int(cap.get(3)), int(cap.get(4))
    cap.release()
    k = W / shape[1]
    S = np.diag([k, k, 1.0])
    Si = np.linalg.inv(S)
    s = W / 960.0
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                           "-s", f"{W}x{Hh}", "-r", f"{fps}", "-i", "-", "-c:v", "libx264",
                           "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)
    ia, ib = iter_frames(src_a), iter_frames(src_b)
    for t in range(n):
        fa, fb = next(ia), next(ib)
        H = S @ Hs[t] @ Si
        warped = cv2.warpPerspective(fb, H, (W, Hh), flags=cv2.INTER_LINEAR)
        det_b = an_b["dets"][t][an_b["idx"][t]] if an_b["idx"][t] >= 0 else None
        mb = _full_mask(det_b, shape)
        bbox_b = boxes_b[t]
        out = fa.copy()
        if bbox_b is not None:
            wb = _warp_box(H, np.asarray(bbox_b) * k)
            visible = wb[2] > 0 and wb[0] < W and wb[3] > 0 and wb[1] < Hh
        else:
            visible = False
        if visible:
            if mb is not None:
                mb = cv2.resize(mb, (W, Hh), interpolation=cv2.INTER_NEAREST) if k != 1 else mb
                mw = cv2.warpPerspective(mb * 255, H, (W, Hh))
            else:
                mw = _diff_mask(fa, warped, wb, (Hh, W)) * 255
            mw = cv2.dilate(mw, np.ones((3, 3), np.uint8))
            alpha = cv2.GaussianBlur(mw, (0, 0), 1.5 * s).astype(np.float32) / 255.0
            # keep A's own skier in front
            det_a = an_a["dets"][t][an_a["idx"][t]] if an_a["idx"][t] >= 0 else None
            ma = _full_mask(det_a, shape)
            if ma is not None:
                ma = cv2.resize(ma, (W, Hh), interpolation=cv2.INTER_NEAREST)
                alpha *= (1 - cv2.GaussianBlur(ma.astype(np.float32), (0, 0), 1.0))
            # outline halo to make the ghost identifiable
            edge = cv2.morphologyEx(mw, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
            edge = cv2.GaussianBlur(edge, (0, 0), 1.0).astype(np.float32) / 255.0
            a3 = (alpha * ghost_alpha)[..., None]
            out = (out * (1 - a3) + warped * a3).astype(np.float32)
            e3 = (np.clip(edge * 0.8, 0, 1) * (1 - alpha * 0.5))[..., None]
            out = (out * (1 - e3) + np.array(GHOST_COLOR, np.float32) * e3).astype(np.uint8)
            _label(out, name_b, (wb[0] + wb[2]) / 2, wb[1] - 8 * s, GHOST_COLOR, 0.55 * s)
        if boxes_a[t] is not None:
            ba = np.asarray(boxes_a[t]) * k
            _label(out, name_a, (ba[0] + ba[2]) / 2, ba[1] - 8 * s, A_COLOR, 0.55 * s)
        _hud(out, t, fps, gap[t], gap, name_a, name_b, s)
        ff.stdin.write(out.tobytes())
        if progress and t % 10 == 0:
            progress(t / n)
    ff.stdin.close()
    ff.wait()
    return out_path


def run(path_a, path_b, an_a, an_b, out_path, progress=None, **kw):
    for an in (an_a, an_b):
        an["idx"] = track_main(an["dets"], an["G"], an["shape"])
    boxes_a, _ = interpolate_boxes(an_a["dets"], an_a["idx"], an_a["G"])
    boxes_b, _ = interpolate_boxes(an_b["dets"], an_b["idx"], an_b["G"])
    p = (lambda a, b: (lambda x: progress(a + (b - a) * x))) if progress else (lambda a, b: None)
    Hs, ok = align(path_a, path_b, an_a, an_b, progress=p(0, 0.4))
    n = len(Hs)
    gap, _ = compute_gap(boxes_a[:n], boxes_b[:n], an_a["G"], Hs, an_a["fps"])
    render(path_a, path_b, an_a, an_b, Hs, boxes_a, boxes_b, gap, out_path, progress=p(0.4, 1.0), **kw)
    valid = ~np.isnan(gap)
    final = float(gap[valid][-1]) if valid.any() else None
    return {"final_gap": final, "aligned_ratio": float(np.mean(ok)), "frames": n}
