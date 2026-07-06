#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"
PORT="${PORT:-5000}"
SERVICE_NAME="${SERVICE_NAME:-inspection-robot.service}"
LINE_FOLLOW_ENABLED="${LINE_FOLLOW_ENABLED:-0}"
TRANSFER_LINE_ENABLED="${TRANSFER_LINE_ENABLED:-0}"
ROBOT_PIPER_DIR="${ROBOT_PIPER_DIR:-/home/pi/temp/piper}"
ROBOT_PIPER_MODEL="${ROBOT_PIPER_MODEL:-$ROBOT_PIPER_DIR/zh_CN-huayan-x_low.onnx}"

SERVICE_FILE="/tmp/$SERVICE_NAME"

cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Inspection Robot Patrol Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=$CAR_DIR
Environment=RUN_MODE=robot
Environment=PORT=$PORT
Environment=ENABLE_OCR=0
Environment=ENABLE_COLOR=0
Environment=ROBOT_SLOW_SPEED=16
Environment=ROBOT_PATROL_SPEED=16
Environment=ROBOT_COMMAND_REPEAT=1
Environment=SMOOTH_CRUISE_ENABLED=1
Environment=CRUISE_SPEED=22
Environment=CRUISE_TICK_SECONDS=0.03
Environment=TIMED_STOP_SCAN_ENABLED=0
Environment=TIMED_STOP_SCAN_SPEED=15
Environment=TIMED_STOP_SCAN_DRIVE_SECONDS=0.8
Environment=TIMED_STOP_SCAN_SETTLE_SECONDS=0.2
Environment=MOTION_GUARD_POLL_SECONDS=0.005
Environment=ROBOT_STEP_SECONDS=0.14
Environment=ROBOT_TURN_SPEED=22
Environment=ROBOT_TURN_90_SECONDS=0.85
Environment=ROBOT_ACTION_SETTLE_SECONDS=0.35
Environment=BOUNDARY_MIN_BLACK_SENSORS=1
Environment=BOUNDARY_CONFIRM_SAMPLES=1
Environment=BOUNDARY_CONFIRM_GAP_SECONDS=0.0
Environment=BOUNDARY_WINDOW_SECONDS=0.25
Environment=BOUNDARY_RETREAT_COMMAND=backward
Environment=BOUNDARY_RETREAT_SECONDS=0.14
Environment=LINE_FOLLOW_ENABLED=$LINE_FOLLOW_ENABLED
Environment=TRANSFER_LINE_ENABLED=$TRANSFER_LINE_ENABLED
Environment=LINE_FOLLOW_SPEED=16
Environment=HEADING_HOLD_TOLERANCE_DEG=1.2
Environment=MPU6050_YAW_DEADBAND_DPS=0.7
Environment=MPU6050_YAW_LEAK_PER_SECOND=0.15
Environment=HEADING_HOLD_CORRECTION_SPEED=16
Environment=HEADING_HOLD_SPEED_GAIN=3.0
Environment=HEADING_HOLD_MIN_CORRECTION_SPEED=6
Environment=HEADING_HOLD_MAX_SPEED_FRACTION=0.8
Environment=HEADING_HOLD_MIN_INTERVAL_SECONDS=0.05
Environment=OBJECT_PRESENCE_COOLDOWN_SECONDS=1.5
Environment=OBJECT_PRESENCE_MIN_AREA_RATIO=0.008
Environment=BLOCKED_DISTANCE_MM=120
Environment=CLEAR_DISTANCE_MM=180
Environment=BLOCKED_SAMPLES=1
Environment=ROBOT_PIPER_DIR=$ROBOT_PIPER_DIR
Environment=ROBOT_PIPER_MODEL=$ROBOT_PIPER_MODEL
Environment=PATH=$ROBOT_PIPER_DIR:/home/pi/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=AVOIDANCE_TURN_DIRECTION=right
Environment=AVOIDANCE_SPEED=14
Environment=AVOIDANCE_BODY_SECONDS=1.00
ExecStartPre=/bin/sh -c '/home/pi/project_demo/raspbot/killprocess.sh || true'
ExecStart=/usr/bin/env bash -lc 'PYTHON_CMD=\$(for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do if command -v "\$candidate" >/dev/null 2>&1 && "\$candidate" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >/dev/null 2>&1; then echo -n "\$candidate"; exit 0; fi; done); test -n "\$PYTHON_CMD"; exec "\$PYTHON_CMD" app.py'
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

scp "$SERVICE_FILE" "$CAR_HOST:/tmp/$SERVICE_NAME"
ssh "$CAR_HOST" "
sudo mv '/tmp/$SERVICE_NAME' '/etc/systemd/system/$SERVICE_NAME'
sudo systemctl daemon-reload
sudo systemctl enable '$SERVICE_NAME'
sudo systemctl restart '$SERVICE_NAME'
sudo systemctl --no-pager --full status '$SERVICE_NAME' || true
"

rm -f "$SERVICE_FILE"
echo "Installed and restarted $SERVICE_NAME on $CAR_HOST. Dashboard: http://${CAR_HOST#*@}:$PORT"
