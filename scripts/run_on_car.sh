#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"
RUN_MODE="${RUN_MODE:-simulate}"
PORT="${PORT:-5000}"

case "$RUN_MODE" in
  simulate|robot)
    ;;
  *)
    echo "RUN_MODE 只能是 simulate 或 robot。" >&2
    exit 1
    ;;
esac

ssh "$CAR_HOST" "cd '$CAR_DIR' && RUN_MODE='$RUN_MODE' PORT='$PORT' nohup python3 app.py > app.log 2>&1 & echo \$!"
echo "Started on $CAR_HOST:$CAR_DIR with RUN_MODE=$RUN_MODE, PORT=$PORT"
