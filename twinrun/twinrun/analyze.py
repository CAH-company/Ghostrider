"""Per-video analysis (streaming, low memory): person detections (YOLO-seg, masks cropped to box)
+ frame-to-frame camera homographies chained to frame 0."""
import os
import threading
import cv2
import numpy as np

# analysis of A and B runs in parallel threads: model, SIFT and matcher are per-thread
_tls = threading.local()
_CUDA = None


def _cuda():
    global _CUDA
    if _CUDA is None:
        try:
            import torch
            _CUDA = torch.cuda.is_available()
        except Exception:
            _CUDA = False
    return _CUDA


def get_model():
    m = getattr(_tls, "model", None)
    if m is None:
        from ultralytics import YOLO
        m = YOLO(os.environ.get("GL_YOLO", "yolo11n-seg.pt"))
        if _cuda():
            m.to("cuda")
        _tls.model = m
    return m


def iter_frames(path):
    cap = cv2.VideoCapture(path)
    while True:
        ok, f = cap.read()
        if not ok:
            break
        yield f
    cap.release()


def video_info(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return fps, n, (h, w)


def sift():
    s = getattr(_tls, "sift", None)
    if s is None:
        s = _tls.sift = cv2.SIFT_create(nfeatures=3000, contrastThreshold=0.02)
    return s


def _clahe():
    c = getattr(_tls, "clahe", None)
    if c is None:
        c = _tls.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return c


def people_mask(shape, boxes, pad=12):
    m = np.full(shape[:2], 255, np.uint8)
    for b in boxes:
        x1, y1, x2, y2 = np.asarray(b).astype(int)
        cv2.rectangle(m, (x1 - pad, y1 - pad), (x2 + pad, y2 + pad), 0, -1)
    return m


def features(frame, boxes):
    g = _clahe().apply(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    kp, des = sift().detectAndCompute(g, people_mask(frame.shape, boxes))
    pts = np.float32([k.pt for k in kp]) if kp else np.zeros((0, 2), np.float32)
    return pts, des


def _matcher():
    m = getattr(_tls, "matcher", None)
    if m is None:
        m = _tls.matcher = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=64))
    return m


def match_h(f1, f2, ratio=0.75, thresh=3.0, min_inl=25, return_pts=False):
    """Homography mapping points of f1 into f2. Returns (H, n_inliers) or (None, n)."""
    p1, d1 = f1
    p2, d2 = f2
    if d1 is None or d2 is None or len(p1) < 10 or len(p2) < 10:
        return None, 0
    knn = _matcher().knnMatch(d1, d2, k=2)
    good = [x[0] for x in knn if len(x) == 2 and x[0].distance < ratio * x[1].distance]
    if len(good) < min_inl:
        return None, len(good)
    a = p1[[m.queryIdx for m in good]]
    b = p2[[m.trainIdx for m in good]]
    H, inl = cv2.findHomography(a, b, cv2.USAC_MAGSAC, thresh, maxIters=5000, confidence=0.999)
    if H is None:
        return None, 0
    n = int(inl.sum())
    if n < min_inl:
        return None, n
    return H / H[2, 2], n


def _crop_mask(mask, box, shape):
    H, W = shape[:2]
    x1, y1, x2, y2 = np.asarray(box).astype(int)
    x1, y1 = max(0, x1 - 4), max(0, y1 - 4)
    x2, y2 = min(W, x2 + 4), min(H, y2 + 4)
    return (x1, y1, x2, y2), mask[y1:y2, x1:x2].copy()


def analyze(path, progress=None, imgsz=960, batch=8):
    fps, n_est, shape = video_info(path)
    model = get_model()
    dets_all, G = [], []
    prev, prev2 = None, None  # features of t-1, t-2
    buf = []
    done = 0

    def flush(buf):
        nonlocal prev, prev2, done
        kw = dict(device=0, half=True) if _cuda() else {}
        res = model.predict(buf, imgsz=imgsz, classes=[0], conf=0.15, verbose=False, retina_masks=True, **kw)
        for f, r in zip(buf, res):
            dets = []
            if r.boxes is not None and len(r.boxes):
                boxes = r.boxes.xyxy.cpu().numpy()
                confs = r.boxes.conf.cpu().numpy()
                masks = r.masks.data.cpu().numpy() if r.masks is not None else None
                for k in range(len(boxes)):
                    d = {"box": boxes[k], "conf": float(confs[k]), "mask": None}
                    if masks is not None:
                        m = (masks[k] > 0.5).astype(np.uint8)
                        if m.shape != f.shape[:2]:
                            m = cv2.resize(m, (f.shape[1], f.shape[0]), interpolation=cv2.INTER_NEAREST)
                        d["mask"] = _crop_mask(m, boxes[k], f.shape)
                    dets.append(d)
            dets_all.append(dets)
            ft = features(f, [d["box"] for d in dets])
            t = len(G)
            if t == 0:
                G.append(np.eye(3))
            else:
                Hm, _ = match_h(ft, prev)
                if Hm is not None:
                    G.append(G[t - 1] @ Hm)
                else:
                    Hm, _ = match_h(ft, prev2) if prev2 is not None else (None, 0)
                    G.append(G[t - 2] @ Hm if Hm is not None else G[t - 1].copy())
                G[t] = G[t] / G[t][2, 2]
            prev2, prev = prev, ft
            done += 1
        if progress and n_est:
            progress(min(1.0, done / n_est))

    for f in iter_frames(path):
        buf.append(f)
        if len(buf) == batch:
            flush(buf)
            buf = []
    if buf:
        flush(buf)
    return {"fps": fps, "shape": shape, "dets": dets_all, "G": G}
