#!/usr/bin/env bash
# Configure this project's camera only. Run with bash after stopping its dashboard.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
if [[ ! -x .venv/bin/python || ! -f deploy/raspberry-pi/runtime.env ]]; then
  echo 'First run: bash deploy/raspberry-pi/prepare.sh' >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null; then
  echo 'FFmpeg is missing. Install it with: sudo apt install ffmpeg' >&2
  exit 1
fi
camera_device="${1:-}"
if [[ -z "$camera_device" ]]; then
  shopt -s nullglob
  camera_candidates=(/dev/v4l/by-id/*-video-index0)
  if [[ ${#camera_candidates[@]} -ne 1 ]]; then
    echo 'Specify one capture device: bash deploy/raspberry-pi/enable-camera.sh /dev/video0' >&2
    exit 1
  fi
  camera_device="${camera_candidates[0]}"
fi
if [[ "$camera_device" != /dev/* || ! -c "$camera_device" || ! -r "$camera_device" || ! -w "$camera_device" ]]; then
  echo 'Camera device is missing or not readable/writable by this user.' >&2
  exit 1
fi
umask 077
.venv/bin/python - "$camera_device" <<'PY'
from datetime import datetime
from pathlib import Path
import shlex
import sys

path = Path('deploy/raspberry-pi/runtime.env')
original = path.read_text(encoding='utf-8-sig')
backup = path.with_name('runtime.env.bak-camera-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
backup.write_bytes(path.read_bytes())
updates = {
    'FACTORY_CAMERA_ENABLED': '1',
    'FACTORY_CAMERA_DEVICE': shlex.quote(sys.argv[1]),
    'FACTORY_CAMERA_WIDTH': '1280',
    'FACTORY_CAMERA_HEIGHT': '720',
    'FACTORY_CAMERA_FPS': '30',
}
lines = [line for line in original.splitlines() if line.strip().removeprefix('export ').split('=', 1)[0].strip() not in updates]
lines.extend(f'{key}={value}' for key, value in updates.items())
path.write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
print('Camera enabled: ' + sys.argv[1])
print('Capture: MJPEG 1280x720 / 30 fps; browser preview up to 10 fps.')
print('Existing MQTT, mode and mail settings retained. Restart: bash deploy/raspberry-pi/run.sh')
PY
