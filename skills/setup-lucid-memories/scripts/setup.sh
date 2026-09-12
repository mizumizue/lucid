#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY=""

for candidate in \
  python3 \
  python \
  "$HOME/.local/bin/python3" \
  "$HOME/AppData/Local/Python/pythoncore-3.14-64/python.exe" \
  "$HOME/AppData/Local/Python/bin/python.exe"
do
  if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "[setup:error] Python 3.10+ is required but no python executable was found." >&2
  exit 1
fi

exec "$PY" "$SCRIPT_DIR/setup.py" "$@"
