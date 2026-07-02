#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"
CAR_DIR="${CAR_DIR:-/home/pi/temp/inspection_robot}"

cd "$(dirname "$0")/.."

ssh "$CAR_HOST" "mkdir -p '$CAR_DIR'"
rsync -av --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.log' \
  --exclude 'data/*.json' \
  --exclude 'data/*.csv' \
  ./ "$CAR_HOST:$CAR_DIR/"

echo "Deployed to $CAR_HOST:$CAR_DIR"
