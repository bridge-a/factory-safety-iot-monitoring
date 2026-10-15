#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd -- "$project_dir"
if [[ ! -x .venv/bin/python || ! -f deploy/raspberry-pi/runtime.env ]]; then
  echo 'First run: bash deploy/raspberry-pi/prepare.sh' >&2
  exit 1
fi
set -a
# Local configuration authored by the project owner, not downloaded input.
# Windows uploads may retain CRLF; remove only the end-of-line CR before Bash
# evaluates assignments, preserving quoted spaces and the original local file.
source <(sed 's/\r$//' deploy/raspberry-pi/runtime.env)
set +a
exec .venv/bin/python dashboard.py
