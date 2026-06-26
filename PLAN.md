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
3. Protect gives a URL like `rtsp://<NVR-IP>:7447/<streamId>` — paste it into `config.yaml`.
4. Lower resolution stream = less CPU for motion/detection; High = better species crops.
   Medium is usually the sweet spot for a feeder.

## Build order (milestones)

1. **M1 – Plumbing (this PR):** repo, config, DB, pluggable interfaces, `stub` classifier,
   web UI rendering from whatever rows exist. Runs end-to-end with fake data.
2. **M2 – See the stream:** `capture.py` connects to your real RTSP URL, motion gate tuned.
3. **M3 – Detect birds:** wire YOLO, confirm it crops birds at the feeder.
4. **M4 – Name them:** enable TF-Hub classifier; review accuracy on your local birds.
5. **M5 – Polish:** debounce tuning, Claude fallback for low-confidence, week navigation,
   lightbox, daily email/summary (optional).

## Open questions for you

- Which species backend do you want as default — fully-local TF-Hub, or Claude vision?
- Keep every crop, or only the single best crop per visit (saves disk)?
- Run the watcher as a background service (Windows Task Scheduler / NSSM) eventually?
