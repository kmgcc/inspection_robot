#!/usr/bin/env bash
set -euo pipefail

CAR_HOST="${CAR_HOST:-pi@192.168.1.11}"

ssh "$CAR_HOST" "fuser -k 5000/tcp 2>/dev/null || true"
