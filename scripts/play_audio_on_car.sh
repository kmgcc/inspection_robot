#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"

ssh "$CAR_HOST" "cd '$CAR_DIR' && PYTHONPATH=src python3 -m inspection_robot.audio"
