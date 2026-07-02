#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
elif command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
  PYTHON_CMD=(python)
elif command -v py >/dev/null 2>&1 && py -3 -c "import sys" >/dev/null 2>&1; then
  PYTHON_CMD=(py -3)
else
  echo "未找到可用的 Python 3 解释器。" >&2
  exit 1
fi

PORT="${PORT:-5050}"
echo "Starting local warehouse dashboard: http://127.0.0.1:${PORT}"
PORT="$PORT" "${PYTHON_CMD[@]}" app.py
