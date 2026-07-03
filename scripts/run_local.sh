#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
      printf "%s\n" "$candidate"
      return 0
    fi
  done
  if command -v py >/dev/null 2>&1 && py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >/dev/null 2>&1; then
    printf "%s\n" "py -3"
    return 0
  fi
  return 1
}

PYTHON_CMD_TEXT="$(find_python || true)"
if [ -z "$PYTHON_CMD_TEXT" ]; then
  echo "未找到可用的 Python 3.10+ 解释器。" >&2
  exit 1
fi
IFS=' ' read -r -a PYTHON_CMD <<< "$PYTHON_CMD_TEXT"

PORT="${PORT:-5050}"
echo "Starting local warehouse dashboard: http://127.0.0.1:${PORT}"
PORT="$PORT" "${PYTHON_CMD[@]}" app.py
