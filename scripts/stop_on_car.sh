#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
PORT="${PORT:-5000}"

ssh "$CAR_HOST" "fuser -k '$PORT'/tcp 2>/dev/null || true"
echo "Stopped dashboard on $CAR_HOST port $PORT"
