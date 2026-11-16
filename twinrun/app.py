"""TwinRun demo server: upload two runs, mark start/end, get one overlay video."""
import base64
import json
import os
import queue
import secrets
import shutil
import threading
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from twinrun import ingest, pipeline

DATA = os.environ.get("GL_DATA", os.path.join(os.path.dirname(__file__), "data"))
UP = os.path.join(DATA, "uploads")
JOBS = os.path.join(DATA, "jobs")
os.makedirs(UP, exist_ok=True)
os.makedirs(JOBS, exist_ok=True)
PASSWORD = os.environ.get("GL_PASSWORD")  # optional: protects the whole site with HTTP basic auth
MAX_UPLOAD = int(os.environ.get("GL_MAX_UPLOAD_MB", "3000")) * 1024 * 1024
RENDER_WIDTH = int(os.environ.get("GL_RENDER_WIDTH", "1280"))

app = FastAPI(title="TwinRun demo")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if PASSWORD and request.url.path != "/healthz":
        h = request.headers.get("authorization", "")
        ok = False
        if h.startswith("Basic "):
            try:
                _, pw = base64.b64decode(h[6:]).decode().split(":", 1)
                ok = secrets.compare_digest(pw, PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="twinrun"'})
    return await call_next(request)


# ---- single background worker: one overlay at a time (CPU bound) ----
jobs_q: "queue.Queue[tuple]" = queue.Queue()


def worker():
    while True:
        job_dir, kw = jobs_q.get()
        try:
            pipeline.process(job_dir, **kw)
        finally:
            jobs_q.task_done()


threading.Thread(target=worker, daemon=True).start()


def _upload_path(uid):
    if not uid or not all(c in "0123456789abcdef" for c in uid):
        raise HTTPException(400, "bad id")
    for f in os.listdir(UP):
        if f.startswith(uid + ".") and not f.endswith(".preview.mp4") and not f.endswith(".json"):
            return os.path.join(UP, f)
    raise HTTPException(404, "upload not found")


@app.get("/healthz")
def healthz():
    return {"ok": True, "queue": jobs_q.qsize()}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    uid = uuid.uuid4().hex
    ext = os.path.splitext(file.filename or "")[1].lower()[:6] or ".mp4"
    if ext not in (".mp4", ".mov", ".m4v", ".mkv", ".avi"):
        raise HTTPException(400, "Unsupported file type")
    dst = os.path.join(UP, uid + ext)
    size = 0
    with open(dst, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                os.remove(dst)
                raise HTTPException(413, "File too large")
            out.write(chunk)
    try:
        info = ingest.preview(dst, os.path.join(UP, uid + ".preview.mp4"))
    except Exception:
        os.remove(dst)
        raise HTTPException(400, "Could not read this video")
    meta = {"id": uid, "fps": info["fps"], "duration": info["duration"], "hdr": info["hdr"],
            "width": info["width"], "height": info["height"], "name": file.filename}
    with open(os.path.join(UP, uid + ".json"), "w") as f:
        json.dump(meta, f)
    meta["preview"] = f"/media/uploads/{uid}.preview.mp4"
    return meta


class JobIn(BaseModel):
    a: str
    b: str
    a_start: float = 0.0
    a_end: float | None = None
    b_start: float = 0.0
    b_end: float | None = None
    name_a: str = "A"
    name_b: str = "B"


@app.post("/api/jobs")
def create_job(j: JobIn):
    a, b = _upload_path(j.a), _upload_path(j.b)
    # both trimmed clips get the same length (the shorter one) so frames pair 1:1
    la = (j.a_end or 1e9) - j.a_start
    lb = (j.b_end or 1e9) - j.b_start
    L = min(la, lb)
    a_end = j.a_start + L if L < 1e8 else None
    b_end = j.b_start + L if L < 1e8 else None
    jid = uuid.uuid4().hex[:12]
    jd = os.path.join(JOBS, jid)
    os.makedirs(jd)
    pipeline._write(jd, state="queued", stage="queued", progress=0.0, position=jobs_q.qsize() + 1)
    jobs_q.put((jd, dict(a_src=a, b_src=b, a_start=j.a_start, a_end=a_end, b_start=j.b_start,
                         b_end=b_end, name_a=j.name_a[:12] or "A", name_b=j.name_b[:12] or "B",
                         render_width=RENDER_WIDTH)))
    return {"id": jid}


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    if not jid.isalnum():
        raise HTTPException(400)
    p = os.path.join(JOBS, jid, "status.json")
    if not os.path.exists(p):
        raise HTTPException(404)
    with open(p) as f:
        st = json.load(f)
    if st.get("state") == "done":
        st["video"] = f"/media/jobs/{jid}/overlay.mp4"
    return JSONResponse(st)


app.mount("/media", StaticFiles(directory=DATA), name="media")


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
