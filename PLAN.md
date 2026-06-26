# 🐦 BirdWatcher — Project Plan

Capture birds at your feeder from a UniFi camera over RTSP, identify the species,
and visualize the week (Sun–Sat) in a local web UI.

## Goal

> Point a UniFi camera at the birdfeeder → automatically capture birds → identify
> the species → show a weekly grid where each species is a row and colored squares
> show how often we saw it per day.

## Architecture

```
                ┌──────────────────────────────────────────────────────────┐
   UniFi cam    │                    capture worker (run.py watch)          │
  ───RTSP──────▶│                                                           │
                │  capture.py        detector.py        classifier.py       │
                │  ┌──────────┐      ┌──────────┐       ┌───────────────┐    │
                │  │ RTSP read│─────▶│  YOLO    │──────▶│ species ID     │    │
                │  │ + motion │frame │ "is there│ crop  │ (TF-Hub birds  │    │
                │  │ gate     │      │  a bird?"│       │  or Claude)    │    │
                │  └──────────┘      └──────────┘       └───────┬───────┘    │
                │                                               │            │
                │                          database.py  ◀───────┘            │
                │                          (SQLite + saved crop image)       │
                └───────────────────────────────┬──────────────────────────┘
                                                 │
                                       ┌─────────▼─────────┐
                                       │  web/app.py       │   run.py web
                                       │  Flask + JS UI    │──▶ http://localhost:8000
                                       │  weekly heat-grid │
                                       └───────────────────┘
```

Two independent processes share one SQLite DB + image folder:
1. **`watch`** — long-running worker: RTSP → motion gate → bird detect → classify → store.
2. **`web`** — Flask server rendering the weekly grid from the DB.

## Pipeline stages

| Stage | File | What it does | Tech |
|-------|------|--------------|------|
| 1. Stream | `capture.py` | Read RTSP with auto-reconnect | OpenCV `VideoCapture` |
| 2. Motion gate | `capture.py` | Only run detection when pixels change (saves CPU) | MOG2 background subtraction |
| 3. Bird detect | `detector.py` | "Is there a bird, and where?" → crop bounding box | Ultralytics YOLO (COCO `bird` class) |
| 4. Species ID | `classifier.py` | Name the bird from the crop | TF-Hub `aiy/birds_V1` (~960 species, local) **or** Claude vision |
| 5. Store | `database.py` | One row per sighting + saved crop thumbnail | SQLite |
| 6. Visualize | `web/` | Weekly Sun–Sat grid, species rows, colored count squares | Flask + vanilla JS |

### Why this split?
- **Motion gate before YOLO** — a feeder is empty most of the time. Running YOLO on
  every frame wastes power; the motion gate keeps it idle until something moves.
- **Detect before classify** — YOLO cheaply rejects squirrels/leaves and crops tightly
  so the species classifier sees a clean bird, not a whole yard.
- **Debounce** — the same bird sits for many frames. We collapse sightings of the same
  species within a cooldown window (default 60s) into one "visit" so counts mean visits,
  not frames.

## Species identification options (pluggable)

`classifier.py` exposes a `SpeciesClassifier` interface so you can swap backends in config:

- **`tfhub`** (default, fully local, free): Google's `aiy/vision/classifier/birds_V1`
  — recognizes ~964 bird species, returns a label + confidence. No internet, no API key.
- **`claude`** (optional, best accuracy, costs API tokens): sends the crop to Claude vision
  and asks for the species. Good fallback when TF-Hub is unsure.
- **`stub`** (for wiring/testing without ML deps installed): labels everything "Unknown bird".

## Data model (SQLite)

```sql
CREATE TABLE sightings (
    id           INTEGER PRIMARY KEY,
    ts           TEXT NOT NULL,        -- ISO8601 local time
    species      TEXT NOT NULL,        -- e.g. "Northern Cardinal"
    confidence   REAL NOT NULL,        -- 0..1
    image_path   TEXT NOT NULL,        -- data/captures/2026-06-25/cardinal_1432.jpg
    detector_conf REAL                 -- YOLO bird confidence
);
CREATE INDEX idx_sightings_ts ON sightings(ts);
CREATE INDEX idx_sightings_species ON sightings(species);
```

## Web UI design

A **GitHub-contributions-style heat grid**, but for birds:

```
              Sun   Mon   Tue   Wed   Thu   Fri   Sat
  ┌────────┐  ┌─┐   ┌─┐   ┌─┐   ┌─┐   ┌─┐   ┌─┐   ┌─┐
  │🐦 thumb│  │ │   │▓│   │█│   │▓│   │ │   │░│   │█│   Northern Cardinal   (23)
  ├────────┤  └─┘   └─┘   └─┘   └─┘   └─┘   └─┘   └─┘
  │🐦 thumb│  │░│   │ │   │▓│   │█│   │█│   │▓│   │░│   Black-capped Chickadee (15)
  └────────┘
   ↑ left column: species appear dynamically, newest/most-seen on top
                  each row = best thumbnail + name + week total
                  each square's color intensity = sightings that day
```

Creative touches:
- **Square color = count bucket** (0 / 1–2 / 3–5 / 6–9 / 10+), each species can get its own hue
  so the grid reads like a quilt.
- **Hover a square** → tooltip with exact count and the times seen that day.
- **Click a square** → lightbox of that day's captured crops for that species.
- **Left column is dynamic** — species only appear once seen; sorted by weekly total.
- **Week picker** — jump to previous weeks; "This week" defaults to the current Sun–Sat.
- Header stats: total visits, distinct species, busiest day, "rarest visitor".

Endpoints:
- `GET /` — the page.
- `GET /api/week?start=YYYY-MM-DD` — JSON: `{ days:[...7 dates], species:[{name, thumb, total, counts:[7], times:[[..]]}] }`.
- `GET /captures/<path>` — serve saved crop images.

## Repo layout

```
BirdWatcher/
├── PLAN.md                  ← this file
├── README.md                ← setup / run instructions
├── requirements.txt
├── config.example.yaml      ← copy to config.yaml and fill in RTSP URL
├── .gitignore
├── run.py                   ← entrypoint:  python run.py watch | web | initdb
├── birdwatcher/
│   ├── __init__.py
│   ├── config.py            ← typed config loaded from config.yaml / env
│   ├── database.py          ← SQLite helpers + queries for the weekly grid
│   ├── capture.py           ← RTSP reader + motion gate
│   ├── detector.py          ← YOLO bird detector
│   ├── classifier.py        ← pluggable species ID (tfhub / claude / stub)
│   ├── pipeline.py          ← glues stages together, runs the watch loop
│   └── web/
│       ├── app.py           ← Flask app + API
│       ├── templates/index.html
│       └── static/{style.css, app.js}
└── data/
    ├── birdwatcher.db        (created at runtime, git-ignored)
    └── captures/             (saved crops, git-ignored)
```

## Getting the RTSP URL from UniFi Protect

1. Open **UniFi Protect** → **Settings → System** (or the camera's settings) → enable **RTSP**.
2. On the camera, toggle on a stream quality (High/Medium/Low) under **RTSP**.
3. Modern Protect gives an RTSPS URL like `rtsps://<NVR-IP>:7441/<streamId>?enableSrtp` — paste it into `config.yaml`.
4. Lower resolution stream = less CPU for motion/detection; High = better species crops.
   Medium is usually the sweet spot for a feeder.

## Build order (milestones)

1. **M1 – Plumbing:** ✅ repo, config, DB, pluggable interfaces, `stub` classifier,
   web UI. Runs end-to-end with seed data.
2. **M2 – See the stream:** ✅ RTSPS (TLS + SRTP) connects; `run.py test` grabs a frame.
   Camera set to **High = 2688×1512 (4MP)** for maximum detail on distant birds.
3. **M3 – Detect birds:** ✅ YOLO (`ultralytics`, installs cleanly on Python 3.14) wired
   into the watch loop; capturing live with `stub` labels.
4. **M4 – Name them:** ▶ pick a species backend and review accuracy. Note: TensorFlow /
   TF-Hub has no Python 3.14 wheels yet → **leaning Claude-vision backend** (also
   cloud-friendly), or run TF in a separate Python 3.12 env.
5. **M5 – Polish:** debounce tuning, low-confidence Claude fallback, week nav, lightbox,
   optional daily summary.
6. **M6 – Hear them (audio / BirdNET):** parallel acoustic pipeline — pull the camera's
   48 kHz audio track (ffmpeg/PyAV) → BirdNET (`birdnetlib`, location + date aware) →
   `audio_detections` table. Correlate with visual sightings: 🔊 confirm badge when
   *seen* and *heard* agree, plus a "heard nearby" soundscape layer for birds that never
   land. Caveats: TFLite on Py 3.14 may need its own env; wind noise hurts accuracy.
7. **M7 – Host it (self-hosted Pi appliance — CHOSEN):** run the whole stack (capture +
   detect + classify + SQLite + web) on a **Raspberry Pi 4 B / 8 GB** on the home LAN —
   no cloud. Wall screen + phones load `http://<pi-ip>:8000` (bind web host to `0.0.0.0`).
   Free, private, sits right next to the camera. To-dos: storage ≥64 GB or **boot from a
   USB SSD** (the 8 GB SD is too small + wears out under 24/7 writes); feed the Pi the
   **1080** stream (Pi 4 H.264 hw-decode caps ~1080p); export YOLO to **NCNN** for a few
   FPS; `systemd` unit so it survives reboots. *Azure hybrid (Container App + Postgres +
   Blob, edge pushes results) stays the fallback only if public/remote access is ever
   needed — and a free Tailscale tunnel to the Pi would cover remote without Azure.*

## Open questions / decisions

- **Deployment target: DECIDED — self-hosted Raspberry Pi 4 B (8 GB), home LAN only, no
  cloud** (see M7). Prereq: bigger / SSD storage before migrating off the desktop.
- **Species backend (M4):** still to confirm — fully-offline **local TFLite/ONNX** model on
  the Pi vs a **cloud vision API** (GPT-4o / Claude, needs internet). TF/TF-Hub still lacks
  Python 3.14 wheels, so a local model would run via TFLite/ONNX or a separate 3.12 env.
- Keep every crop, or only the best crop per visit (saves SD/SSD space)?
- **Resolution:** High / 4 MP for desktop testing now; **use 1080 on the Pi** (decode limit).
- Background **service**: `systemd` on the Pi so the watcher + web auto-start and survive
  reboots.
