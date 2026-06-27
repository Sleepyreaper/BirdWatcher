#!/usr/bin/env bash
# Install go2rtc as a local restreamer to stabilize the camera feed.
# Run ON the Pi:  bash scripts/install_go2rtc.sh
# Then edit go2rtc.yaml with your camera URL and point config.yaml at the restream.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
cd "$DIR"

if [ ! -x ./go2rtc ]; then
  echo "[1/3] downloading go2rtc…"
  case "$(uname -m)" in
    aarch64|arm64) BIN=go2rtc_linux_arm64 ;;
    armv7l)        BIN=go2rtc_linux_arm ;;
    x86_64)        BIN=go2rtc_linux_amd64 ;;
    *) echo "unsupported arch $(uname -m)"; exit 1 ;;
  esac
  curl -fsSL "https://github.com/AlexxIT/go2rtc/releases/latest/download/$BIN" -o go2rtc
  chmod +x go2rtc
fi

if [ ! -f go2rtc.yaml ]; then
  cp go2rtc.example.yaml go2rtc.yaml
  echo "[2/3] created go2rtc.yaml — EDIT IT: put your camera RTSPS URL in 'birdcam'."
fi

echo "[3/3] installing systemd service…"
sudo tee /etc/systemd/system/birdwatcher-go2rtc.service >/dev/null <<EOF
[Unit]
Description=BirdWatcher go2rtc restreamer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=$DIR/go2rtc -config $DIR/go2rtc.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now birdwatcher-go2rtc

echo
echo "Done. Next:"
echo "  1. nano go2rtc.yaml        # set birdcam: to your camera URL"
echo "  2. sudo systemctl restart birdwatcher-go2rtc"
echo "  3. point config.yaml rtsp_url at:  rtsp://127.0.0.1:8554/birdcam"
echo "  4. sudo systemctl restart birdwatcher-watch"
