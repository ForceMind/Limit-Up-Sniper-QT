#!/usr/bin/env bash
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF
qt command for this project

Usage:
  qt install              install dependencies and systemd service
  qt update               backup data, pull code, install dependencies, restart
  qt restart | start      restart service
  qt stop                 stop systemd service or nohup process
  qt status               show service/process and API status
  qt logs                 follow service logs
  qt backup               backup backend/data
  qt restore <tar.gz>     restore backend/data from backup
  qt scan                 run GitHub safety scan
  qt help                 show this help
EOF
}

systemd_unit_exists() {
  command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"
}

api_url() {
  echo "http://127.0.0.1:${PORT}/api/status"
}

cmd="${1:-help}"
case "$cmd" in
  install)
    "$SCRIPT_DIR/install_server.sh"
    ;;
  update)
    "$SCRIPT_DIR/update_server.sh"
    ;;
  restart|start)
    "$SCRIPT_DIR/restart_server.sh"
    ;;
  stop)
    if systemd_unit_exists; then
      if [[ "$(id -u)" -eq 0 ]]; then
        systemctl stop "$SERVICE_NAME"
      else
        sudo systemctl stop "$SERVICE_NAME"
      fi
    else
      pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
      if [[ -f "$pid_file" ]]; then
        pid="$(cat "$pid_file" || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
          kill "$pid"
          echo "stopped ${SERVICE_NAME} pid=$pid"
        else
          echo "${SERVICE_NAME} process is not running"
        fi
      else
        echo "pid file not found: $pid_file"
      fi
    fi
    ;;
  status)
    if systemd_unit_exists; then
      systemctl status "$SERVICE_NAME" --no-pager -l || true
    else
      pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
      if [[ -f "$pid_file" ]]; then
        pid="$(cat "$pid_file" || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
          echo "${SERVICE_NAME} running pid=$pid"
        else
          echo "${SERVICE_NAME} pid file exists but process is not running"
        fi
      else
        echo "${SERVICE_NAME} pid file not found"
      fi
    fi
    if command -v curl >/dev/null 2>&1; then
      curl -fsS "$(api_url)" || true
      echo
    fi
    ;;
  logs)
    if systemd_unit_exists; then
      journalctl -u "$SERVICE_NAME" -f
    else
      tail -f "$ROOT_DIR/backend/data/${SERVICE_NAME}.out.log" "$ROOT_DIR/backend/data/${SERVICE_NAME}.err.log"
    fi
    ;;
  backup)
    "$SCRIPT_DIR/backup_data.sh"
    ;;
  restore)
    if [[ $# -lt 2 ]]; then
      echo "error: qt restore requires a backup tar.gz path" >&2
      exit 2
    fi
    "$SCRIPT_DIR/restore_data.sh" "$2"
    ;;
  scan)
    if [[ -x "$(venv_python)" ]]; then
      "$(venv_python)" "$ROOT_DIR/scripts/security_scan.py"
    else
      "$PYTHON_BIN" "$ROOT_DIR/scripts/security_scan.py"
    fi
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
