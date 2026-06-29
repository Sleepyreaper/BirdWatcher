FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-dev python3-pip \
    ffmpeg libglib2.0-0 \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

# The CUDA base image ships no tzdata, so without this Python can't resolve the
# TZ name and falls back to UTC — which buckets detections onto the wrong day.
ENV TZ=America/New_York

WORKDIR /app

# Install PyTorch with CUDA 12.4 wheels before open_clip/ultralytics so they
# pick up the GPU build rather than the CPU-only wheels from the default index.
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# BioCLIP model cache lands on the data volume, not in the image layer.
ENV HF_HOME=/data/hf-cache

EXPOSE 8080
CMD ["python3", "-m", "birdwatcher.web.app"]
