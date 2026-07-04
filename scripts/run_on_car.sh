#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"
RUN_MODE="${RUN_MODE:-robot}"
AUTO_START_RUNTIME="${AUTO_START_RUNTIME:-0}"
PORT="${PORT:-5000}"
LINE_FOLLOW_ENABLED="${LINE_FOLLOW_ENABLED:-0}"
# Low-speed constant-velocity cruise: continuous gyro heading correction +
# recognise-while-moving orange flash, no stop-go patrol. Override with 0 to
# fall back to the classic short-step patrol.
SMOOTH_CRUISE_ENABLED="${SMOOTH_CRUISE_ENABLED:-1}"

case "$RUN_MODE" in
  simulate|robot)
    ;;
  *)
    echo "RUN_MODE 只能是 simulate 或 robot。" >&2
    exit 1
    ;;
esac

ssh "$CAR_HOST" "
cd '$CAR_DIR'
PYTHON_CMD=\$(for candidate in .venv/bin/python python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v \"\$candidate\" >/dev/null 2>&1 && \"\$candidate\" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    printf '%s' \"\$candidate\"
    exit 0
  fi
done)
if [ -z \"\$PYTHON_CMD\" ]; then
  echo '未找到可用的 Python 3.10+ 解释器。' >&2
  exit 1
fi
if [ '$RUN_MODE' = 'robot' ] && [ -x /home/pi/project_demo/raspbot/killprocess.sh ]; then
  /bin/sh /home/pi/project_demo/raspbot/killprocess.sh || true
fi
RUN_MODE='$RUN_MODE' AUTO_START_RUNTIME='$AUTO_START_RUNTIME' LINE_FOLLOW_ENABLED='$LINE_FOLLOW_ENABLED' SMOOTH_CRUISE_ENABLED='$SMOOTH_CRUISE_ENABLED' PORT='$PORT' FLAGS_use_mkldnn=0 OMP_NUM_THREADS=1 CPU_NUM=1 nohup \"\$PYTHON_CMD\" app.py > app.log 2>&1 & echo \$!
"
echo "Started on $CAR_HOST:$CAR_DIR with RUN_MODE=$RUN_MODE, AUTO_START_RUNTIME=$AUTO_START_RUNTIME, LINE_FOLLOW_ENABLED=$LINE_FOLLOW_ENABLED, SMOOTH_CRUISE_ENABLED=$SMOOTH_CRUISE_ENABLED, PORT=$PORT"
