#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd -- "$project_dir"
if [[ ! -x .venv/bin/python || ! -f deploy/raspberry-pi/runtime.env ]]; then
  echo 'First run: bash deploy/raspberry-pi/prepare.sh' >&2
  exit 1
fi
.venv/bin/python -m pip install --only-binary=:all: -r requirements-vision.txt
.venv/bin/python deploy/raspberry-pi/setup_vision.py --enable
