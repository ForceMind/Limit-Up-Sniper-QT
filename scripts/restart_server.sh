#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root
ensure_data_dir

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl restart "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager -l || true
  else
    sudo systemctl restart "$SERVICE_NAME"
    sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
  fi
  exit 0
fi

PID_FILE="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
LOG_FILE="$ROOT_DIR/backend/data/${SERVICE_NAME}.out.log"
ERR_FILE="$ROOT_DIR/backend/data/${SERVICE_NAME}.err.log"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" || true)"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    kill "$OLD_PID" || true
    sleep 2
  fi
fi

if [[ ! -x "$(venv_python)" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$(venv_pip)" install -r "$ROOT_DIR/backend/requirements.txt"
fi

cd "$ROOT_DIR/backend"
nohup "$(venv_python)" -m uvicorn app.main:app --host "$HOST" --port "$PORT" > "$LOG_FILE" 2> "$ERR_FILE" &
echo $! > "$PID_FILE"
echo "started ${SERVICE_NAME} pid=$(cat "$PID_FILE") url=http://127.0.0.1:${PORT}"
