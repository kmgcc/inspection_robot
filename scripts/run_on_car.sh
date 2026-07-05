#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"
RUN_MODE="${RUN_MODE:-robot}"
PORT="${PORT:-5000}"
LINE_FOLLOW_ENABLED="${LINE_FOLLOW_ENABLED:-0}"  # 强制禁用循线，避免干扰测试
# Low-speed constant-velocity cruise: continuous gyro heading correction +
# recognise-while-moving orange flash, no stop-go patrol. Override with 0 to
# fall back to the classic short-step patrol.
SMOOTH_CRUISE_ENABLED="${SMOOTH_CRUISE_ENABLED:-1}"
CRUISE_SPEED="${CRUISE_SPEED:-14}"
CRUISE_TICK_SECONDS="${CRUISE_TICK_SECONDS:-0.03}"
BOUNDARY_MIN_BLACK_SENSORS="${BOUNDARY_MIN_BLACK_SENSORS:-2}"
BOUNDARY_CONFIRM_SAMPLES="${BOUNDARY_CONFIRM_SAMPLES:-2}"
BOUNDARY_CONFIRM_GAP_SECONDS="${BOUNDARY_CONFIRM_GAP_SECONDS:-0.02}"
BOUNDARY_RETREAT_COMMAND="${BOUNDARY_RETREAT_COMMAND:-forward}"
HEADING_HOLD_CORRECTION_SPEED="${HEADING_HOLD_CORRECTION_SPEED:-16}"
HEADING_HOLD_SPEED_GAIN="${HEADING_HOLD_SPEED_GAIN:-2.4}"
HEADING_HOLD_MIN_INTERVAL_SECONDS="${HEADING_HOLD_MIN_INTERVAL_SECONDS:-0.05}"
OBJECT_PRESENCE_COOLDOWN_SECONDS="${OBJECT_PRESENCE_COOLDOWN_SECONDS:-1.5}"
OBJECT_PRESENCE_MIN_AREA_RATIO="${OBJECT_PRESENCE_MIN_AREA_RATIO:-0.008}"
BLOCKED_DISTANCE_MM="${BLOCKED_DISTANCE_MM:-100}"
CLEAR_DISTANCE_MM="${CLEAR_DISTANCE_MM:-160}"
BLOCKED_SAMPLES="${BLOCKED_SAMPLES:-3}"

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
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
  PYTHON_CMD=\"\$PWD/.venv/bin/python\"
else
  PYTHON_CMD=\$(for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v \"\$candidate\" >/dev/null 2>&1 && \"\$candidate\" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
      printf '%s' \"\$candidate\"
      exit 0
    fi
  done)
fi
if [ -z \"\$PYTHON_CMD\" ]; then
  echo '未找到可用的 Python 3.10+ 解释器。' >&2
  exit 1
fi
if command -v fuser >/dev/null 2>&1; then
  fuser -k '$PORT'/tcp >/dev/null 2>&1 || true
  sleep 0.5
fi
PYTHON_REAL=\$(\"\$PYTHON_CMD\" -c 'import sys; print(sys.executable)')
echo \"Using Python: \$PYTHON_REAL\"
if [ \"\$PYTHON_CMD\" = \"\$PWD/.venv/bin/python\" ]; then
  export VIRTUAL_ENV=\"\$PWD/.venv\"
  export PATH=\"\$VIRTUAL_ENV/bin:\$PATH\"
fi
if [ '$RUN_MODE' = 'robot' ] && [ -x /home/pi/project_demo/raspbot/killprocess.sh ]; then
  /bin/sh /home/pi/project_demo/raspbot/killprocess.sh || true
fi
RUN_MODE='$RUN_MODE' LINE_FOLLOW_ENABLED='$LINE_FOLLOW_ENABLED' SMOOTH_CRUISE_ENABLED='$SMOOTH_CRUISE_ENABLED' CRUISE_SPEED='$CRUISE_SPEED' CRUISE_TICK_SECONDS='$CRUISE_TICK_SECONDS' BOUNDARY_MIN_BLACK_SENSORS='$BOUNDARY_MIN_BLACK_SENSORS' BOUNDARY_CONFIRM_SAMPLES='$BOUNDARY_CONFIRM_SAMPLES' BOUNDARY_CONFIRM_GAP_SECONDS='$BOUNDARY_CONFIRM_GAP_SECONDS' BOUNDARY_RETREAT_COMMAND='$BOUNDARY_RETREAT_COMMAND' HEADING_HOLD_CORRECTION_SPEED='$HEADING_HOLD_CORRECTION_SPEED' HEADING_HOLD_SPEED_GAIN='$HEADING_HOLD_SPEED_GAIN' HEADING_HOLD_MIN_INTERVAL_SECONDS='$HEADING_HOLD_MIN_INTERVAL_SECONDS' OBJECT_PRESENCE_COOLDOWN_SECONDS='$OBJECT_PRESENCE_COOLDOWN_SECONDS' OBJECT_PRESENCE_MIN_AREA_RATIO='$OBJECT_PRESENCE_MIN_AREA_RATIO' BLOCKED_DISTANCE_MM='$BLOCKED_DISTANCE_MM' CLEAR_DISTANCE_MM='$CLEAR_DISTANCE_MM' BLOCKED_SAMPLES='$BLOCKED_SAMPLES' PORT='$PORT' FLAGS_use_mkldnn=0 OMP_NUM_THREADS=1 CPU_NUM=1 nohup \"\$PYTHON_CMD\" app.py > app.log 2>&1 & echo \$!
"
echo "Started on $CAR_HOST:$CAR_DIR with RUN_MODE=$RUN_MODE, LINE_FOLLOW_ENABLED=$LINE_FOLLOW_ENABLED, SMOOTH_CRUISE_ENABLED=$SMOOTH_CRUISE_ENABLED, CRUISE_SPEED=$CRUISE_SPEED, BOUNDARY_MIN_BLACK_SENSORS=$BOUNDARY_MIN_BLACK_SENSORS, BOUNDARY_CONFIRM_SAMPLES=$BOUNDARY_CONFIRM_SAMPLES, PORT=$PORT"
