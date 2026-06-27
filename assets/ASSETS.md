# Asset list — what to stage on the Raspberry Pi

Everything that isn't plain source code, so a Pi can be brought up to match.
Git-ignored payloads are noted. The install scripts under `scripts/` automate most of this.

## System packages (apt)
- `ffmpeg` — RTSP/audio decode (`sudo apt install ffmpeg`)
- `libopenblas-dev` — BLAS for numpy/torch (replaces the old `libatlas-base-dev` on Bookworm)
- Docker — installed by BirdNET-Go's own installer (for the audio engine)

## Python packages (pip — see `requirements.txt`)
- Core: `flask`, `pyyaml`
- Vision: `opencv-python`, `numpy`
- Detection: `ultralytics` (pulls `torch`, `torchvision`)
- Species ID (default): `open_clip_torch`, `pillow` (local **BioCLIP**)
> Pi OS is Python **3.11** → all of the above have working aarch64 wheels.
> Optional: `yolo export model=yolov8n.pt format=ncnn` for more FPS on ARM.

## Binaries / services not from pip
- **go2rtc** — single Go binary, downloaded by `scripts/install_go2rtc.sh`
  (`go2rtc_linux_arm64`), runs as systemd `birdwatcher-go2rtc`. Git-ignored (`go2rtc`).
  Config: `go2rtc.yaml` (git-ignored; copy from `go2rtc.example.yaml`, holds the camera URL).
- **BirdNET-Go** — third-party Docker container (`ghcr.io/tphakala/birdnet-go`), installed by
  its own installer **run as the normal user** (not root) so data stays readable.
  Service `birdnet-go`, UI on `:8080`.

## Models (downloaded at runtime, git-ignored)
- `yolov8n.pt` — auto-downloaded by ultralytics (~6 MB).
- **BioCLIP** (`hf-hub:imageomics/bioclip`) — fetched by open_clip into `~/.cache/huggingface`
  on first run (~hundreds of MB).
- **BirdNET** model — pulled inside the BirdNET-Go container on first start.

## Reference images (`assets/reference/`, git-ignored)
All **32** catalog species, one Wikimedia field-guide photo each, fetched by
`tools/fetch_reference_images.py` (uses the Wikipedia `pageimages` API + a
Wikimedia-compliant User-Agent with contact, plus 429 backoff). Re-run to regenerate on
the Pi. Verify per-image license on Commons before any redistribution; fine for personal use.

## Storage layout (the external drive)
- USB drive at **`/mnt/birddata`**, formatted **ext4** (NTFS breaks SQLite locking),
  in `/etc/fstab` by UUID with `nofail`.
- `config.yaml` `paths.db` / `paths.captures` point there.
- BirdNET-Go's `~/birdnet-go-app` is **moved to `/mnt/birddata/birdnet-go-app` and
  symlinked back**, so its DB + audio clips also land on the USB (off the SD card).

## Config / data (git-ignored, local only)
- `config.yaml` — camera URL (point at `rtsp://127.0.0.1:8554/birdcam` on the Pi),
  `web.host: 0.0.0.0`, `audio.birdnet_db: /mnt/birddata/birdnet-go-app/data/birdnet.db`.
- `go2rtc.yaml` — the real camera URL lives here.
- `data/birdwatcher.db`, `data/captures/` — visual sightings + crops (on the USB).

## Pi staging checklist
1. Pi OS 64-bit, SSH enabled, **64 GB+ card or USB SSD**.
2. `scripts/install_pi.sh` → `scripts/install_go2rtc.sh` → set up the USB → `scripts/install_services.sh`.
3. BirdNET-Go installer (as normal user); audio source `rtsp://<pi-ip>:8554/birdcam`;
   set `audio.birdnet_db` and restart `birdwatcher-web`.
4. Camera must have a **mic** for audio; an **optical-zoom** lens for good IDs.
