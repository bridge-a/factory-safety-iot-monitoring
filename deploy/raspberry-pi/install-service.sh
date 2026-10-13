#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux || "$(id -u)" == 0 ]]; then
  echo 'Run as the normal project owner, without sudo; only installation uses sudo.' >&2
  exit 1
fi
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
service_user="$(id -un)"
# These values are embedded in a systemd unit. Reject unsupported paths rather
# than generating an ambiguous ExecStart or a systemd specifier expansion.
if [[ ! "$project_dir" =~ ^/[a-zA-Z0-9_./-]+$ || ! "$service_user" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo 'Use a project path and username containing only letters, numbers, /, _, ., -.' >&2
  exit 1
fi
if [[ ! -x "$project_dir/.venv/bin/python" || ! -f "$project_dir/deploy/raspberry-pi/runtime.env" ]]; then
  echo 'Run prepare.sh and verify foreground operation first.' >&2
  exit 1
fi
service_path=/etc/systemd/system/factory-monitor.service
if [[ -e "$service_path" ]]; then
  echo 'factory-monitor.service already exists; inspect it before updating. Nothing overwritten.' >&2
  exit 1
fi
unit_file="$(mktemp --suffix=.service)"
trap 'rm -f -- "$unit_file"' EXIT
cat > "$unit_file" <<EOF
[Unit]
Description=Factory safety IoT monitor
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$service_user
WorkingDirectory=$project_dir
ExecStart=/bin/bash $project_dir/deploy/raspberry-pi/run.sh
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillSignal=SIGINT
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemd-analyze verify "$unit_file"
sudo install -m 644 -- "$unit_file" "$service_path"
sudo systemctl daemon-reload
sudo systemctl enable --now factory-monitor.service
echo 'Installed. Check: systemctl status factory-monitor.service'
echo 'Logs: journalctl -u factory-monitor.service -n 60 --no-pager'
