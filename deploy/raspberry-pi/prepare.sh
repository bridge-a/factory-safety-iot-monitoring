#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux || "$(id -u)" == 0 ]]; then
  echo 'Run as the normal project owner on Linux, without sudo.' >&2
  exit 1
fi
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd -- "$project_dir"
if [[ ! -f dashboard.py || ! -f requirements-dashboard.txt ]]; then
  echo 'Project entry point not found.' >&2
  exit 1
fi
if [[ -d .venv && ! -x .venv/bin/python ]]; then
  echo 'Existing .venv is not a Linux environment. Use a fresh independent checkout.' >&2
  exit 1
fi
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dashboard.txt
umask 077
if [[ ! -f deploy/raspberry-pi/runtime.env ]]; then
  sed 's/\r$//' deploy/raspberry-pi/runtime.env.example > deploy/raspberry-pi/runtime.env
fi
mkdir -p data
echo 'Ready. Review deploy/raspberry-pi/runtime.env, then run:'
echo 'bash deploy/raspberry-pi/run.sh'
