# 🐦 BirdWatcher

Watch your birdfeeder through a UniFi camera (RTSP), automatically detect and
identify the birds that visit, and browse a weekly **Sun–Sat** heat-grid of who
showed up and how often.

See [PLAN.md](PLAN.md) for the full architecture and design.

## Quick start (preview the UI with fake data — no camera needed)

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows PowerShell
pip install flask pyyaml
python run.py seed        # insert random sightings for this week
python run.py web         # open http://127.0.0.1:8000
```

## Full setup (real camera)

1. **Install deps:** `pip install -r requirements.txt`
   (uncomment a species-ID backend in `requirements.txt` first — `tfhub` or `claude`).
2. **Configure:** `copy config.example.yaml config.yaml`, then paste your UniFi RTSP URL.
   - In **UniFi Protect**: open the camera → enable **RTSP** → copy the `rtsp://…` URL.
   - Or set it without editing the file: `$env:RTSP_URL = "rtsp://…"`.
3. **Run the watcher:** `python run.py watch` (RTSP → motion → YOLO → species → DB).
4. **Run the UI:** `python run.py web` in another terminal.

## Commands

| Command | What it does |
|---------|--------------|
| `python run.py initdb` | Create the SQLite database |
| `python run.py test`   | Grab one frame from the RTSP camera to verify the connection |
| `python run.py seed`   | Insert fake sightings to preview the UI |
| `python run.py watch`  | Run the capture/detect/classify pipeline |
| `python run.py web`    | Serve the weekly web UI |

## How it works

`capture.py` reads the RTSP stream and only wakes the detector when motion is
seen. `detector.py` (YOLO) confirms there's a bird and crops it. `classifier.py`
names the species (local TF-Hub model or Claude vision). `database.py` records one
row per *visit* (repeats within `visit_cooldown` are merged). The Flask app in
`web/` renders the grid: species are rows in the left column, and each day's
square is colored by how many times that species visited.

## Species ID backends

Set `classifier.backend` in `config.yaml`:

- `bioclip` — **default**; local BioCLIP zero-shot against the species catalog. No API key.
- `stub` — no ML; everything is "Unknown bird" (use to test plumbing).
- `tfhub` — Google `aiy/birds_V1` (needs TensorFlow; no Python 3.14 wheels yet).
- `claude` — Claude vision; needs `ANTHROPIC_API_KEY`.

## Deploy on a Raspberry Pi (M7)

The intended home for BirdWatcher: a small always-on Pi on your LAN, no cloud.

1. **Flash** Raspberry Pi OS (64-bit). In Raspberry Pi Imager → advanced settings,
   enable **SSH**, set the hostname/wifi. Use a **64 GB+ card or USB SSD** (the OS +
   crops outgrow a small card, and SD cards wear out under 24/7 writes).
2. **SSH in**, then:
   ```bash
   git clone https://github.com/Sleepyreaper/BirdWatcher.git
   cd BirdWatcher
   bash scripts/install_pi.sh        # deps, venv, model, reference images
   nano config.yaml                  # set rtsp_url (1080 stream), web.host: 0.0.0.0
   .venv/bin/python run.py test      # confirm the camera
   bash scripts/install_services.sh  # systemd: auto-start + survive reboots
   ```
3. Open `http://<pi-ip>:8000` from any device on your network (or the wall screen).

Notes: feed the Pi the **1080** stream (Pi 4 H.264 decode caps ~1080p); optionally
`yolo export model=yolov8n.pt format=ncnn` for a few extra FPS on ARM.

## Status

M1–M4 done: RTSPS capture → motion gate → YOLO detect → visit dedup (best frame) →
local BioCLIP species ID → weekly reference-photo dashboard, with a 32-species
Cobb County / Cole's Special Feeder catalog. Next: **M7** (this Pi deploy), then
**M6** (audio / BirdNET). See [PLAN.md](PLAN.md).
