#!/bin/sh
set -eu

BASE_DIR="${BASE_DIR:-/mnt/sata/china-proxy-probe}"
CONFIG_FILE="${CONFIG_FILE:-$BASE_DIR/config/probe.json}"
ALIVE_FILE="${ALIVE_FILE:-$BASE_DIR/data/alive.json}"

mkdir -p "$BASE_DIR/data"

python3 "$BASE_DIR/probe/probe.py" \
  --config "$CONFIG_FILE" \
  --output "$ALIVE_FILE"

python3 "$BASE_DIR/probe/push_github.py" \
  --config "$CONFIG_FILE" \
  --file "$ALIVE_FILE"

