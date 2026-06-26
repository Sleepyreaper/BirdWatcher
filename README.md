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

- `stub` — no ML; everything is "Unknown bird" (use to test plumbing).
- `tfhub` — Google `aiy/birds_V1`, ~964 species, fully local, free.
- `claude` — Claude vision; most accurate, needs `ANTHROPIC_API_KEY`.

## Status

Milestone **M1** — plumbing complete; UI renders from the DB and you can preview
it with `seed`. Next: point `config.yaml` at your real RTSP URL and enable a
detector/classifier backend (see PLAN.md milestones).
