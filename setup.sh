#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# WSL/Linux alternative. Python and Node must be available inside this OS.
python_cmd="${HYDRO_SETUP_PYTHON:-python3.11}"
if ! command -v "$python_cmd" >/dev/null; then
  echo 'ERROR: Install Python 3.11 in WSL/Linux (or set HYDRO_SETUP_PYTHON to it). Windows users can use setup.ps1 without WSL.' >&2
  exit 1
fi
exec "$python_cmd" -m src.local_setup.install "$@"
