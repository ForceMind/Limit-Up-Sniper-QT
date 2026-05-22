#!/usr/bin/env bash
set -euo pipefail
if [[ "${QT_LOCALE_REEXEC:-0}" != "1" && "${LC_ALL:-}" != "C" ]]; then
  export QT_LOCALE_REEXEC=1
  export LC_ALL=C
  export LANG=C
  export PYTHONIOENCODING="${PYTHONIOENCODING:-UTF-8}"
  exec bash "$0" "$@"
fi
export LC_ALL=C
export LANG=C
export PYTHONIOENCODING="${PYTHONIOENCODING:-UTF-8}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root
ensure_data_dir

if refresh_systemd_service; then
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl restart "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager -l || true
  else
    sudo systemctl restart "$SERVICE_NAME"
    sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
  fi
  verify_running_backend
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
echo "$(zh '\xe5\xb7\xb2\xe5\x90\xaf\xe5\x8a\xa8')"" ${SERVICE_NAME}""$(zh '\xef\xbc\x8c')""pid=$(cat "$PID_FILE")""$(zh '\xef\xbc\x8c\xe8\xae\xbf\xe9\x97\xae\xe5\x9c\xb0\xe5\x9d\x80\xef\xbc\x9a')""http://127.0.0.1:${PORT}"
verify_running_backend
