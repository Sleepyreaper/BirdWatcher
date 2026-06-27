# 🐦 BirdWatcher — Project Plan & Architecture

Capture birds at your feeder, identify them by **sight** (camera) and **sound**
(microphone), and visualize the week (Sun–Sat) in a local web dashboard that
cross-correlates the two.

## Goal

> Point a camera at the birdfeeder → automatically catch + identify the birds that
> land, *and* the birds you can hear → show a weekly grid: who was **seen**, who was
> **heard**, and who was **both** (🔊 confirmed at the feeder).

## Architecture

```
                        ┌─ run.py watch ──────────────────────────────┐
                        │  capture → YOLO detect → BioCLIP species     │
   camera ──RTSP──▶ go2rtc │  → visit tracking (best frame) → SQLite   │
   (video + audio)      │                                              │
                        └─ BirdNET-Go (Docker) ── acoustic ID → its SQLite
                                       │                    │
                                       ▼                    ▼
                        run.py web ── reads BOTH ── weekly dashboard (:8000)
                                       seen · heard · 🔊 confirmed
```

**go2rtc** is the keystone: it maintains a single connection to the camera and
re-serves it locally (`rtsp://127.0.0.1:8554/birdcam`). Every consumer reads from
go2rtc, so (a) the flaky camera link is held in one place, (b) the watcher and
BirdNET-Go share one camera connection, and (c) **swapping cameras is one line.**

Independent processes, each a systemd service on the Pi:
- **`go2rtc`** — camera restreamer.
- **`watch`** — visual pipeline → SQLite (`sightings`, one row per visit).
- **BirdNET-Go** — acoustic pipeline → its own SQLite (`detections` + `labels`).
- **`web`** — Flask dashboard, reads the visual DB + (read-only) BirdNET-Go's DB.

## Visual pipeline stages

| Stage | File | What it does | Tech |
|-------|------|--------------|------|
| 1. Stream | `capture.py` | Read RTSP (from go2rtc) with auto-reconnect | OpenCV `VideoCapture` |
| 2. Motion gate | `capture.py` | Only run detection when pixels change | MOG2 background subtraction |
| 3. Bird detect | `detector.py` | "Is there a bird, and where?" → padded crop | Ultralytics YOLO (COCO `bird`) |
| 4. Visit tracking | `pipeline.py` | Match a bird across frames (box IoU) into one **visit**; keep the sharpest crop (Laplacian × detector conf) | custom |
| 5. Species ID | `classifier.py` | Name the *best* crop once per visit | **BioCLIP** (local, default) / Claude / stub |
| 6. Store | `database.py` | One row per visit (best frame, first/last seen, frames) | SQLite |
| 7. Visualize | `web/` | Weekly grid + audio overlay | Flask + vanilla JS |

### Key design choices
- **Motion gate before YOLO** — a feeder is empty most of the time; stay idle until something moves.
- **Track into visits** — a bird sits for many frames. We match detections across frames
  by bounding-box overlap, collapse them into **one visit**, and classify only the
  **sharpest** frame (so the classifier runs once per visit, not per frame, and counts
  mean visits, not frames). Two birds at different ports = two visits.
- **Constrain to a local catalog** — `data/species_catalog.json` (Cobb County / Cole's
  Special Feeder, 32 species) is both the classifier's whitelist *and* the source of
  reference photos. Massively improves accuracy (e.g. Carolina vs. Black-capped Chickadee).

## Species identification (pluggable)

`classifier.py` exposes a `SpeciesClassifier` interface; pick via `classifier.backend`:

- **`bioclip`** (default) — local **BioCLIP** (`open_clip`, `hf-hub:imageomics/bioclip`),
  zero-shot against the catalog's common names. No API key, fully offline → fits the Pi.
- **`claude`** — Claude vision; needs `ANTHROPIC_API_KEY`.
- **`tfhub`** — Google `aiy/birds_V1`; needs TensorFlow (no Python 3.14 wheels).
- **`stub`** — labels everything "Unknown bird" (wiring tests).

## Acoustic pipeline (BirdNET-Go)

Rather than build our own audio worker, we run **BirdNET-Go** (a mature Go BirdNET):
continuous, efficient, location/date aware, own UI + SQLite + clip storage. We read its
DB read-only and map detections onto our catalog:

- BirdNET-Go schema: `detections(detected_at INTEGER epoch, confidence, label_id, …)`
  joined to `labels(id, scientific_name)`. Note: **only scientific names** — we map
  them to our catalog (which has both), which also filters to our species for free.
- `birdwatcher/birdnetgo.py` opens it `mode=ro` (never locks BirdNET-Go's writes); any
  error → empty result, so the audio layer simply doesn't appear.

## Data model

```sql
-- our visual visits (birdwatcher.db)
CREATE TABLE sightings (
    id INTEGER PRIMARY KEY, ts TEXT,        -- visit start (local ISO8601)
    species TEXT, confidence REAL,          -- classifier label + conf
    image_path TEXT, detector_conf REAL,    -- best crop + YOLO conf
    last_ts TEXT, frames INTEGER            -- visit end + frames seen
);
```
Audio lives in BirdNET-Go's own `birdnet.db` (read-only to us). The dashboard merges
the two at query time — nothing is copied.

## Web UI

A GitHub-contributions-style heat-grid: species are rows (reference photo + name on
the left), each day a colored square whose intensity = visit count, each species its
own hue (a "quilt"). Plus the acoustic overlay:

- 🔊 badge on a cell where the bird was **seen and heard** that day (confirmed).
- a **"heard nearby"** section (accent-outlined squares) for catalog species heard but
  not seen at the feeder.
- header stats: visits, species seen, species heard, busiest day.
- auto-refreshes every 30s (for a wall display); week navigation; click a cell for a
  reference-vs-captured lightbox.

`GET /api/week?start=YYYY-MM-DD` → `{ days, seen[], heard_only[], catalog[], stats, audio_on }`.

## Repo layout

```
BirdWatcher/
├── PLAN.md / README.md
├── requirements.txt
├── config.example.yaml         ← copy to config.yaml (git-ignored)
├── go2rtc.example.yaml          ← copy to go2rtc.yaml (git-ignored)
├── run.py                       ← watch | web | test | seed | initdb
├── birdwatcher/
│   ├── config.py  capture.py  detector.py  classifier.py  pipeline.py
│   ├── database.py              ← visits + weekly-grid query
│   ├── birdnetgo.py             ← read-only BirdNET-Go reader (audio overlay)
│   └── web/{app.py, templates/, static/}
├── data/species_catalog.json    ← Cobb County / Cole's list (32 species)
├── tools/fetch_reference_images.py
└── scripts/
    ├── install_pi.sh  install_services.sh  install_go2rtc.sh
```

## Stream stability — the saga, and the verdict

Symptom: the RTSP feed dropped/reconnected every ~3 minutes. We ruled out, in order:
hardware (Pi 5 idle, ethernet, no throttle), power, SRTP (`?enableSrtp`), resolution
(720p vs 4 MP), and our reconnect code. Adding **go2rtc** finally isolated it: go2rtc
logs `i/o timeout` reading from the UniFi NVR — **the camera/NVR stops sending data on
a timer.** Verdict: a UniFi Protect RTSP quirk. go2rtc minimizes the disruption (fast,
shared reconnect) but can't make a flaky camera stable. **The fix is a different camera**
(an optical-zoom Reolink) — which also fixes the framing/ID-accuracy problem.

## Milestones — all complete ✅

1. **M1 – Plumbing:** repo, config, DB, pluggable interfaces, web UI.
2. **M2 – See the stream:** RTSPS connects; `run.py test`.
3. **M3 – Detect birds:** YOLO wired into the watch loop.
4. **M4 – Name them:** local **BioCLIP** classifier, constrained to the catalog.
5. **M5 – Polish:** visit tracking + best-frame dedup, responsive grid, auto-refresh, lightbox.
6. **M6 – Hear them:** **BirdNET-Go** (pivoted from a hand-rolled `birdnetlib` worker —
   it's better) + read-only integration into our dashboard (🔊 seen-and-heard, "heard nearby").
7. **M7 – Host it:** self-hosted **Raspberry Pi 5** appliance on the home LAN, no cloud;
   data on a 4.6 TB USB; go2rtc + watch + web + BirdNET-Go as systemd services, auto-start.

*(Azure was evaluated and rejected for this home use case — the Pi is free, private, and
sits next to the camera. A friend's Frigate/Coral/Svelte/MQTT stack was reviewed; we
adopted only **go2rtc** and **BirdNET-Go**, which solved real problems.)*

## Open items

- **Camera swap** — to an optical-zoom RTSP camera with a mic (Reolink RLC-823A class):
  fixes the UniFi RTSP instability **and** tightens framing for better IDs / fewer
  one-bird-as-two splits. The only remaining rough edge.
- Optional polish: NCNN export of YOLO for more FPS headroom; quilt-vs-heatmap cell colors
  (a user preference); MQTT/Home-Assistant hooks if ever wanted.
