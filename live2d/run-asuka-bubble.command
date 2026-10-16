#!/bin/sh
set -eu
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$project/.venv/bin/python" "$project/launch_desktop.py"
