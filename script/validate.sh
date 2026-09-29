#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Backend deterministic tests"
cd "$ROOT/app/backend"
uv run --extra test pytest -q tests

echo "==> Native Swift unit tests"
cd "$ROOT/macos/LiveTR3Mac"
swift test

echo "==> Native Swift release build"
swift build -c release

echo "==> LiveTR3 deterministic validation passed"

if [[ "${LIVETR3_RUN_SOAK:-0}" == "1" ]]; then
  echo "==> Running 5-minute MLX soak on this Mac"
  cd "$ROOT/app/backend"
  uv run --extra test python scripts/soak.py \
    --duration-seconds 300 \
    --metric-interval-seconds 30 \
    --drain-seconds 15
fi
