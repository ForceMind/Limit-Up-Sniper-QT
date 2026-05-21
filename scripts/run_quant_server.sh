#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi
HOST="${QUANT_HOST:-0.0.0.0}"
PORT="${QUANT_PORT:-8000}"

cd "$ROOT_DIR/backend"
PYTHON_BIN="${PYTHON_BIN:-python}"
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi
exec "$PYTHON_BIN" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
