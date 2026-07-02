#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-5050}" python3 app.py
