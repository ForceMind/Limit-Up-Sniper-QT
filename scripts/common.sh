#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

load_env() {
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.env"
    set +a
  fi
}

load_env

APP_DIR="${QUANT_APP_DIR:-$ROOT_DIR}"
SERVICE_NAME="${QUANT_SERVICE_NAME:-qt}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT_DIR/backups}"
HOST="${QUANT_HOST:-0.0.0.0}"
PORT="${QUANT_PORT:-8000}"

require_project_root() {
  if [[ ! -d "$ROOT_DIR/backend/app" || ! -d "$ROOT_DIR/frontend" ]]; then
    echo "error: run this script from the project checkout or keep scripts/ under project root" >&2
    exit 1
  fi
}

ensure_data_dir() {
  mkdir -p "$ROOT_DIR/backend/data"
}

venv_python() {
  echo "$VENV_DIR/bin/python"
}

venv_pip() {
  echo "$VENV_DIR/bin/pip"
}

systemctl_available() {
  command -v systemctl >/dev/null 2>&1 && [[ "$(id -u)" -eq 0 || -n "${SUDO_USER:-}" ]]
}
