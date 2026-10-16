#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$root/.venv/bin/python" "$root/run_tests.py" "$@"
