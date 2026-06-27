#!/usr/bin/env bash
# BirdWatcher — Raspberry Pi installer.
# Run ON the Pi after cloning:  git clone <repo> && cd BirdWatcher && bash scripts/install_pi.sh
# Raspberry Pi OS (Bookworm) ships Python 3.11, so torch/ultralytics/open_clip all
# have working aarch64 wheels (via piwheels). The torch install is the slow part.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/5] system packages (ffmpeg for RTSP, BLAS for numpy/torch)…"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip ffmpeg libatlas-base-dev

echo "[2/5] python venv + dependencies (torch can take several minutes)…"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install flask pyyaml numpy opencv-python ultralytics open_clip_torch pillow

echo "[3/5] config…"
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "    -> created config.yaml — EDIT IT:"
  echo "       camera.rtsp_url = your UniFi *1080* stream (Pi H.264 decode caps ~1080p)"
  echo "       web.host = 0.0.0.0  (so the wall screen / phones on your LAN can reach it)"
fi

echo "[4/5] models + reference images…"
python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"   # downloads yolov8n.pt
# Optional ARM speed-up (a few FPS): yolo export model=yolov8n.pt format=ncnn
#   then set detector.model: yolov8n_ncnn_model in config.yaml
python tools/fetch_reference_images.py || echo "    (reference fetch skipped/failed — re-run later)"

echo "[5/5] BioCLIP weights download on first watcher start (~hundreds of MB)."
echo
echo "Done. Test the camera:   .venv/bin/python run.py test"
echo "Then install services:   bash scripts/install_services.sh"
