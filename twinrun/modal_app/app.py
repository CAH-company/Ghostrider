"""TwinRun on Modal: GPU processing + submit endpoint. Files live in Cloudflare R2.

Deploy:  modal deploy modal_app/app.py
Test:    modal run modal_app/app.py --a-key uploads/test/IMG_1620.mov --b-key uploads/test/IMG_1621.mov
"""
import json
import os
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "twinrun"

# L4 list prices (USD/s) - estimate only, check the Modal dashboard for the real bill
PRICE_GPU_L4 = 0.000222
PRICE_CPU_CORE = 0.0000131
PRICE_MEM_GIB = 0.00000222
CPU, MEM_MB = 8, 8192

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "libgl1", "libglib2.0-0")
    # HDR from iPhone needs zscale (libzimg); fail the build loudly if missing
    .run_commands("ffmpeg -hide_banner -filters | grep -q zscale")
    .pip_install("torch", "torchvision", "ultralytics==8.3.75", "opencv-python-headless==4.11.0.86",
                 "numpy<2.3", "boto3", "fastapi[standard]")
    .run_commands("cd /root && python -c \"from ultralytics import YOLO; YOLO('yolo11n-seg.pt')\"")
    .env({"GL_YOLO": "/root/yolo11n-seg.pt"})
    .add_local_dir(str(PKG), "/root/twinrun")
)

app = modal.App("twinrun", image=image)
r2_secret = modal.Secret.from_name("twinrun-r2")
token_secret = modal.Secret.from_name("twinrun-token")


def _s3():
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )


def _put_json(s3, key, obj):
    s3.put_object(Bucket=os.environ["R2_BUCKET"], Key=key, Body=json.dumps(obj).encode(),
                  ContentType="application/json", CacheControl="no-store")


@app.function(gpu="L4", cpu=CPU, memory=MEM_MB, timeout=1200, secrets=[r2_secret])
def process_job(job_id: str, a_key: str, b_key: str, a_start: float = 0.0, a_end: float | None = None,
                b_start: float = 0.0, b_end: float | None = None, name_a: str = "A", name_b: str = "B"):
    import tempfile
    from twinrun import pipeline

    t0 = time.time()
    bucket = os.environ["R2_BUCKET"]
    s3 = _s3()
    prefix = f"jobs/{job_id}"
    job_dir = tempfile.mkdtemp(prefix="job_")
    last = {"t": 0.0}

    def on_status(st):
        # status.json -> R2, max 1 write / 2 s (final states always go through)
        # "done" is written by process_job after overlay.mp4 is uploaded
        if st.get("state") == "done":
            return
        now = time.time()
        if st.get("state") == "error" or now - last["t"] >= 2.0:
            last["t"] = now
            try:
                _put_json(s3, f"{prefix}/status.json", st)
            except Exception as e:  # status is best-effort
                print("status upload failed:", e)

    try:
        pipeline._write(job_dir, on_status, state="running", stage="prepare", progress=0.0)
        a_src, b_src = os.path.join(job_dir, "src_a"), os.path.join(job_dir, "src_b")
        td = time.time()
        s3.download_file(bucket, a_key, a_src)
        s3.download_file(bucket, b_key, b_src)
        t_download = round(time.time() - td, 1)

        timings = pipeline.process(job_dir, a_src, b_src, a_start=a_start, a_end=a_end, b_start=b_start,
                                   b_end=b_end, name_a=name_a, name_b=name_b, on_status=on_status)
        timings["download"] = t_download

        with open(os.path.join(job_dir, "status.json")) as f:
            final = json.load(f)
        if final.get("state") == "done":
            tu = time.time()
            s3.upload_file(os.path.join(job_dir, "overlay.mp4"), bucket, f"{prefix}/overlay.mp4",
                           ExtraArgs={"ContentType": "video/mp4"})
            timings["upload"] = round(time.time() - tu, 1)
        total = round(time.time() - t0, 1)
        cost = total * (PRICE_GPU_L4 + CPU * PRICE_CPU_CORE + MEM_MB / 1024 * PRICE_MEM_GIB)
        meta = {"job_id": job_id, "state": final.get("state"), "timings_s": timings, "total_s": total,
                "cost_usd_estimate": round(cost, 4), "result": final.get("result")}
        _put_json(s3, f"{prefix}/timings.json", meta)
        # status must be written last, after overlay.mp4 is in R2
        _put_json(s3, f"{prefix}/status.json", final)
        print(json.dumps(meta, indent=2, default=str))
        return meta
    except Exception as e:
        import traceback
        traceback.print_exc()
        _put_json(s3, f"{prefix}/status.json", {"state": "error", "error": str(e)})
        raise


@app.function(secrets=[token_secret])
@modal.asgi_app()
def submit():
    """Vercel calls POST / with `Authorization: Bearer $TWINRUN_TOKEN`; returns immediately, work is spawned."""
    import hmac
    from fastapi import FastAPI, HTTPException, Request

    web = FastAPI()

    @web.post("/")
    async def create(request: Request):
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {os.environ['TWINRUN_TOKEN']}".encode()):
            raise HTTPException(status_code=401, detail="unauthorized")
        body = await request.json()
        for k in ("job_id", "a_key", "b_key"):
            if not body.get(k):
                raise HTTPException(status_code=400, detail=f"missing {k}")
        process_job.spawn(
            body["job_id"], body["a_key"], body["b_key"],
            float(body.get("a_start") or 0), body.get("a_end"), float(body.get("b_start") or 0), body.get("b_end"),
            str(body.get("name_a") or "A")[:12], str(body.get("name_b") or "B")[:12])
        return {"job_id": body["job_id"]}

    return web


@app.local_entrypoint()
def main(a_key: str, b_key: str, a_start: float = 0.0, a_end: float = 0.0, b_start: float = 0.0,
         b_end: float = 0.0, name_a: str = "A", name_b: str = "B", job_id: str = ""):
    job_id = job_id or f"test-{int(time.time())}"
    meta = process_job.remote(job_id, a_key, b_key, a_start, a_end or None, b_start, b_end or None, name_a, name_b)
    print("job:", job_id)
    print(json.dumps(meta, indent=2, default=str))
