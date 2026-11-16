FROM python:3.11-slim

# ffmpeg from Debian is built with libzimg (zscale) -> needed for iPhone HDR (HLG) tonemapping
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# CPU-only torch keeps the image ~1 GB smaller than the default CUDA build
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y opencv-python 2>/dev/null; pip install --no-cache-dir --force-reinstall opencv-python-headless==4.11.0.86

# bake the segmentation model into the image
RUN python -c "from ultralytics import YOLO; YOLO('yolo11n-seg.pt')"

COPY twinrun ./twinrun
COPY static ./static
COPY app.py .

ENV GL_DATA=/data YOLO_CONFIG_DIR=/tmp/ultralytics OMP_NUM_THREADS=4
VOLUME /data
EXPOSE 8000
HEALTHCHECK CMD curl -fs http://localhost:8000/healthz || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
