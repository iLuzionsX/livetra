#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="${1:-$ROOT/dist/LiveTR3.app}"
ENGINE_DIR="$BUNDLE/Contents/Resources/Engine"
BACKEND_DIR="$ENGINE_DIR/backend"

mkdir -p "$BACKEND_DIR"
rsync -a --delete --delete-excluded \
  --exclude '.venv' \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  "$ROOT/app/backend/" "$BACKEND_DIR/"

if [[ -n "${PYTHON_STANDALONE_DIR:-}" ]]; then
  rm -rf "$ENGINE_DIR/python"
  mkdir -p "$ENGINE_DIR"
  rsync -a "$PYTHON_STANDALONE_DIR/" "$ENGINE_DIR/python/"
fi

if [[ -n "${WHEELHOUSE_DIR:-}" && -x "$ENGINE_DIR/python/bin/python3" ]]; then
  "$ENGINE_DIR/python/bin/python3" -m venv "$ENGINE_DIR/venv"
  "$ENGINE_DIR/venv/bin/python" -m pip install --no-index --find-links "$WHEELHOUSE_DIR" -r "$BACKEND_DIR/requirements.txt"
fi

if [[ "${BUNDLE_MODEL:-0}" == "1" ]]; then
  if [[ -z "${MODEL_DIR:-}" ]]; then
    echo "MODEL_DIR is required when BUNDLE_MODEL=1" >&2
    exit 2
  fi
  mkdir -p "$ENGINE_DIR/models"
  rsync -a "$MODEL_DIR/" "$ENGINE_DIR/models/$(basename "$MODEL_DIR")/"
fi

echo "Packaged LiveTR3 engine resources at $ENGINE_DIR"
