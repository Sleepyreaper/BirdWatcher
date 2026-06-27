#!/usr/bin/env bash
# Install + enable systemd services so watch + web auto-start and survive reboots.
# Run ON the Pi after install_pi.sh:  bash scripts/install_services.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
PY="$DIR/.venv/bin/python"

for svc in watch web; do
  sudo tee "/etc/systemd/system/birdwatcher-$svc.service" >/dev/null <<EOF
[Unit]
Description=BirdWatcher $svc
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=$PY -u run.py $svc
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
done

sudo systemctl daemon-reload
sudo systemctl enable --now birdwatcher-watch birdwatcher-web

IP="$(hostname -I | awk '{print $1}')"
echo "Installed. Dashboard: http://$IP:8000"
echo "Logs:  journalctl -u birdwatcher-watch -f"
