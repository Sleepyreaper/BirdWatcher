# 🐦 BirdWatcher

Watch your birdfeeder, automatically **see** which birds land (camera + AI) and
**hear** which birds are around (microphone + BirdNET), and browse a weekly
**Sun–Sat** dashboard that cross-correlates the two — so you know who actually
visited, who was just singing nearby, and who was **both** (🔊 *confirmed at the
feeder*).

Runs entirely **local** — built to live on a Raspberry Pi on your home LAN. No
cloud, no API keys required.

See [PLAN.md](PLAN.md) for the full architecture and the milestone history.

## Architecture

```
                       ┌─ watch (run.py) ─ YOLO detect → BioCLIP species → SQLite (visits)
 camera ──RTSP──▶ go2rtc ┤
                       └─ BirdNET-Go (Docker) ─── acoustic species → its own SQLite
                                  │
                                  ▼
                       web (run.py) ─ unified dashboard: seen · heard · 🔊 confirmed
```

- **go2rtc** holds ONE connection to the camera and restreams it locally, so both
  pipelines share it — and the **camera becomes a single swappable line** of config.
- **Visual** (`watch`): motion gate → YOLO ("is there a bird?") → BioCLIP (which of
  *your* local species) → one row per **visit** (sharpest frame kept).
- **Audio** (BirdNET-Go): continuous BirdNET on the soundscape, location/date aware.
- **Dashboard** (`web`): reads both, shows a weekly heat-grid with reference photos
  and a 🔊 badge where a bird was seen *and* heard.

| Process | Role | Runs as |
|---|---|---|
| go2rtc | camera restreamer (stability + sharing) | systemd `birdwatcher-go2rtc` |
| `run.py watch` | capture → YOLO → BioCLIP → SQLite | systemd `birdwatcher-watch` |
| BirdNET-Go | acoustic bird ID (third-party, Docker) | systemd `birdnet-go`, UI `:8080` |
| `run.py web` | dashboard (reads visual + audio DBs) | systemd `birdwatcher-web`, `:8000` |

## Quick start (preview the UI, no camera)

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows PowerShell
pip install flask pyyaml
python run.py seed     # fake sightings for this week
python run.py web      # http://127.0.0.1:8000
```

## Commands

| Command | What it does |
|---|---|
| `python run.py test`   | grab one frame to verify the camera/stream |
| `python run.py watch`  | capture → detect → classify → store |
| `python run.py web`    | serve the dashboard |
| `python run.py seed`   | fake sightings to preview the UI |
| `python run.py initdb` | create the SQLite DB |

## Species ID backends

Set `classifier.backend` in `config.yaml`:

- **`bioclip`** (default) — local BioCLIP zero-shot against your catalog. No API key;
  best fit for the offline Pi.
- `stub` — everything "Unknown bird" (plumbing tests).
- `claude` — Claude vision; needs `ANTHROPIC_API_KEY`.
- `tfhub` — Google `aiy/birds_V1`; needs TensorFlow (no Python 3.14 wheels).

> Accuracy is gated by **crop quality**: BioCLIP nails a clear bird but a small or
> cage-occluded crop can misfire (and can split one bird into two visits). Frame the
> feeder tightly — an **optical-zoom camera** is the single biggest accuracy lever.

## Stream stability — go2rtc

The watcher and BirdNET-Go both read from **go2rtc** (`rtsp://127.0.0.1:8554/birdcam`),
never the camera directly. That gives one stable, shared connection, fast reconnects,
and makes the camera a single line (`birdcam:` in `go2rtc.yaml`) you can swap without
touching the pipeline.

> ⚠️ **UniFi RTSP is flaky.** UniFi Protect periodically tears down the session
> (go2rtc logs `i/o timeout` reading from the NVR; drops ~every 3 min, recovers in
> seconds). It's the **camera/NVR**, not the Pi or the code — go2rtc proved it by
> isolating the failure to its own read from the camera. go2rtc softens it; the real
> cure is a camera with a saner RTSP stack (an optical-zoom Reolink also fixes framing).

## Audio (BirdNET-Go)

Acoustic ID runs as **[BirdNET-Go](https://github.com/tphakala/birdnet-go)** — a Go
implementation of BirdNET that's far better than rolling our own: continuous,
efficient, location-aware, with its own UI on `:8080`. We don't replace it — we
**read its SQLite read-only** (`config.audio.birdnet_db`) and overlay detections:

- 🔊 on a species **seen and heard** the same day → confirmed at the feeder.
- a **"heard nearby"** section for catalog birds heard but not seen.

The camera needs a **microphone** (go2rtc relays its audio track to BirdNET-Go).

## Deploy on a Raspberry Pi (the intended home)

A small always-on Pi on your LAN, no cloud. We run a **Pi 5 / 8 GB** over ethernet.
Pi OS is Python 3.11, so torch / open_clip / BirdNET all have clean ARM wheels.

```bash
# on the Pi, over SSH (run as your normal user, not root):
git clone https://github.com/Sleepyreaper/BirdWatcher.git && cd BirdWatcher
bash scripts/install_pi.sh         # apt deps, venv, YOLO model, reference images
bash scripts/install_go2rtc.sh     # restreamer; edit go2rtc.yaml -> your camera URL
sed -i 's|rtsp_url:.*|rtsp_url: "rtsp://127.0.0.1:8554/birdcam"|' config.yaml
sed -i 's|host: "127.0.0.1"|host: "0.0.0.0"|' config.yaml
bash scripts/install_services.sh   # systemd: watch + web, auto-start on boot
```
Open `http://<pi-ip>:8000`.

**Audio (optional):** install BirdNET-Go **as your normal user** (not `sudo`, so its
data stays readable):
```bash
curl -fsSL https://raw.githubusercontent.com/tphakala/birdnet-go/main/install.sh -o bn.sh
bash bn.sh    # interactive: audio source = rtsp://<pi-ip>:8554/birdcam ; set lat/lon
```
Then enable the overlay and restart the dashboard:
```bash
printf '\naudio:\n  birdnet_db: "/mnt/birddata/birdnet-go-app/data/birdnet.db"\n' >> config.yaml
sudo systemctl restart birdwatcher-web
```

**Storage:** put the growing data on an external drive. We use a **4.6 TB USB at
`/mnt/birddata`** (formatted **ext4**, mounted in `/etc/fstab` by UUID with `nofail`);
set `paths` in `config.yaml` there, and **symlink BirdNET-Go's `~/birdnet-go-app`** to
the drive too. SD cards die under 24/7 image/clip writes.

## Gotchas we learned the hard way

- **UniFi RTSP drops ~every 3 min** — camera/NVR, not your setup. go2rtc softens it;
  a different camera fixes it.
- **Docker `127.0.0.1` ≠ host** — BirdNET-Go (in Docker) must reach go2rtc via the Pi's
  **LAN IP**, and go2rtc must `listen: 0.0.0.0:8554`.
- **SQLite hates NTFS** — format the data drive **ext4**; NTFS breaks DB locking. (Keep
  the SQLite DB off NTFS even if clips go there.)
- **Pi 4 H.264 decode caps ~1080p** (Pi 5 handles 4 MP). YOLO downsizes to 640 anyway —
  ID quality is about *framing*, not stream resolution.
- **`%-I` strftime is Linux-only** — use portable formats (bit us on Windows).
- **BioCLIP prompts** — use common names; don't mix Latin labels with English negatives.
- **Wikimedia rate-limits** generic User-Agents — use the `pageimages` API + a contact
  UA (already in `tools/fetch_reference_images.py`).

## Status

**M1–M7 + M6 (audio) complete.** Deployed and running on a Pi 5: camera → go2rtc →
YOLO + BioCLIP (visual) and BirdNET-Go (audio) → one dashboard, data on a 4.6 TB USB,
auto-starting on boot. The remaining rough edge is the camera itself (UniFi RTSP
instability) — a planned swap to an optical-zoom camera fixes both stability and IDs.
