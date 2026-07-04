#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"
PORT="${PORT:-5000}"
SERVICE_NAME="${SERVICE_NAME:-inspection-robot.service}"
AUTO_START_RUNTIME="${AUTO_START_RUNTIME:-0}"
LINE_FOLLOW_ENABLED="${LINE_FOLLOW_ENABLED:-0}"

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
Environment=AUTO_START_RUNTIME=$AUTO_START_RUNTIME
Environment=PORT=$PORT
Environment=ROBOT_SLOW_SPEED=22
Environment=ROBOT_PATROL_SPEED=22
Environment=ROBOT_STEP_SECONDS=0.14
Environment=ROBOT_TURN_SPEED=22
Environment=ROBOT_TURN_90_SECONDS=0.85
Environment=ROBOT_ACTION_SETTLE_SECONDS=0.35
Environment=BOUNDARY_MIN_BLACK_SENSORS=4
Environment=BOUNDARY_CONFIRM_SAMPLES=2
Environment=LINE_FOLLOW_ENABLED=$LINE_FOLLOW_ENABLED
Environment=LINE_FOLLOW_SPEED=22
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
