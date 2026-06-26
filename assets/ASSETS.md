# Asset list — what to stage on the Raspberry Pi

Tracks everything that isn't plain source code, so the Pi can be set up to match.
Git-ignored payloads are noted. Update this whenever we download/generate an asset.

## System packages (apt)
- `ffmpeg` — RTSP/audio decode (already on the PC via winget; on Pi: `sudo apt install ffmpeg`)

## Python packages (pip)
Core: `flask`, `pyyaml`
Vision: `opencv-python` (Pi: `opencv-python-headless`), `numpy`
Detection: `ultralytics` (pulls `torch`, `torchvision`)
Later (M4/M6, TBD): species classifier backend, `birdnetlib` for audio
> On the Pi we'll export YOLO to **NCNN** for speed (`yolo export format=ncnn`).

## Models
- `yolov8n.pt` — auto-downloaded by ultralytics to the project root (~6 MB, git-ignored).
  Regenerate on the Pi by running once, or copy over. NCNN export is the Pi target.

## Reference images  (`assets/reference/`, git-ignored)
One canonical field-guide photo per species, for the UI row avatars.
Fetched by `tools/fetch_reference_images.py` from Wikipedia/Wikimedia Commons.

| File | Species | Source | License |
|------|---------|--------|---------|
| downy-woodpecker.jpg | Downy Woodpecker | https://en.wikipedia.org/wiki/Downy_woodpecker (Wikimedia: Dryobates_pubescens_UL_03.jpg) | Wikimedia Commons — verify per-image before any redistribution; fine for personal use |

> TODO: bulk-fetch the rest of `data/species_catalog.json` (run the fetch script).
> Record each image's source + license here as it's added.

## Config / data (git-ignored, local only)
- `config.yaml` — contains the real RTSPS URL. Move/recreate on the Pi (use the **1080** stream there).
- `data/birdwatcher.db` — SQLite sightings. Local only; not synced to GitHub.
- `data/captures/` — saved bird crops. Local only.

## Notes for Pi staging
- Use the 1080 RTSP stream (Pi 4 H.264 decode caps ~1080p).
- Bind web to `0.0.0.0` so the LAN/wall-screen can reach it.
- Run `watch` + `web` as `systemd` services (survive reboots).
- Storage: 64 GB+ card or USB-SSD boot (not the 8 GB SD).
