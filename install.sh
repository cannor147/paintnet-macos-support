#!/bin/bash
set -euo pipefail
command -v python3 >/dev/null || { echo 'Python 3 is required to run the installer.' >&2; exit 1; }
exec python3 "$(cd "$(dirname "$0")" && pwd)/install.py" "$@"
