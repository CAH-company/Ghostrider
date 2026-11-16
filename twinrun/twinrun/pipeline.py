"""End-to-end job: two raw uploads + start/end marks -> overlay MP4."""
import json
import os
import time
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import ingest, analyze, overlay


_lock = threading.Lock()


def _write(job_dir, on_status=None, **kw):
    with _lock:
        p = os.path.join(job_dir, "status.json")
        st = {}
        if os.path.exists(p):
            with open(p) as f:
                st = json.load(f)
        st.update(kw)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, p)
    if on_status:
        on_status(st)


def process(job_dir, a_src, b_src, a_start=0.0, a_end=None, b_start=0.0, b_end=None,
            name_a="A", name_b="B", render_width=1280, on_status=None):
    """Stages: prepare, analyze (A and B in parallel), align, render. `on_status(dict)` is called on every
    status change (the caller throttles). Returns timings dict (seconds per stage)."""
    weights = {"prepare": (0.0, 0.10), "analyze": (0.10, 0.60), "align": (0.70, 0.10), "render": (0.80, 0.20)}

    def prog(stage, x):
        b, w = weights[stage]
        _write(job_dir, on_status, state="running", stage=stage, progress=round(b + w * x, 3))

    timings = {}
    t0 = time.time()
    try:
        prog("prepare", 0)
        a_lo, a_hi = os.path.join(job_dir, "a.mp4"), os.path.join(job_dir, "a_hi.mp4")
        b_lo, b_hi = os.path.join(job_dir, "b.mp4"), os.path.join(job_dir, "b_hi.mp4")
        # common fps (that of A) so frames pair 1:1
        fps = ingest.probe(a_src)["fps"]
        ingest.normalize(a_src, a_lo, start=a_start, end=a_end, dst_hi=a_hi, width_hi=render_width, fps=fps)
        prog("prepare", 0.5)
        ingest.normalize(b_src, b_lo, start=b_start, end=b_end, dst_hi=b_hi, width_hi=render_width, fps=fps)
        timings["prepare"] = round(time.time() - t0, 1)

        t1 = time.time()
        pr = {"a": 0.0, "b": 0.0}

        def ap(k):
            def f(x):
                pr[k] = x
                prog("analyze", (pr["a"] + pr["b"]) / 2)
            return f
        with ThreadPoolExecutor(2) as ex:
            fa = ex.submit(analyze.analyze, a_lo, progress=ap("a"))
            fb = ex.submit(analyze.analyze, b_lo, progress=ap("b"))
            an_a, an_b = fa.result(), fb.result()
        timings["analyze"] = round(time.time() - t1, 1)

        t2 = time.time()

        def p2(x):
            if x < 0.4:
                prog("align", x / 0.4)
            else:
                prog("render", (x - 0.4) / 0.6)
        out = os.path.join(job_dir, "overlay.mp4")
        res = overlay.run(a_lo, b_lo, an_a, an_b, out, progress=p2,
                          name_a=name_a, name_b=name_b, hi_a=a_hi, hi_b=b_hi)
        timings["align_render"] = round(time.time() - t2, 1)
        for f in (a_lo, b_lo, a_hi, b_hi):
            try:
                os.remove(f)
            except OSError:
                pass
        _write(job_dir, on_status, state="done", stage="done", progress=1.0, result=res,
               seconds=round(time.time() - t0))
    except Exception as e:
        traceback.print_exc()
        _write(job_dir, on_status, state="error", error=str(e))
    timings["total"] = round(time.time() - t0, 1)
    return timings
