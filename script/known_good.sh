#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT/app/backend"

mode="${1:-quick}"

run_backend_import_gate() {
  cd "$BACKEND_DIR"
  PYTHONPATH=. uv run python scripts/check_imports.py
  PYTHONPATH=. uv run python -m py_compile \
    mlx_worker.py \
    session.py \
    protocol.py \
    server.py
}

run_native_checks() {
  swift build -c release --package-path "$ROOT/macos/LiveTR3Mac"
  swift test --package-path "$ROOT/macos/LiveTR3Mac"
  cd "$BACKEND_DIR"
  uv run --extra test python -m pytest tests -q
}

run_short_soak() {
  cd "$BACKEND_DIR"
  PYTHONPATH=. uv run python scripts/soak.py \
    --duration-seconds 90 \
    --metric-interval-seconds 10 \
    --drain-seconds 15 \
    --no-polish
}

run_fault_soak() {
  cd "$BACKEND_DIR"
  PYTHONPATH=. uv run python scripts/soak.py \
    --duration-seconds 180 \
    --metric-interval-seconds 15 \
    --drain-seconds 20 \
    --inject-fault \
    --fault-at-seconds 75 \
    --no-polish
}

case "$mode" in
  quick)
    run_backend_import_gate
    run_native_checks
    ;;
  soak)
    run_short_soak
    ;;
  full)
    run_backend_import_gate
    run_native_checks
    run_fault_soak
    ;;
  *)
    echo "Usage: $0 [quick|soak|full]" >&2
    exit 2
    ;;
esac

echo "LiveTR3 known-good validation passed: $mode"
