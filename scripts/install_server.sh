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

if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/.env.example" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "$(zh '\xe5\xb7\xb2\xe6\xa0\xb9\xe6\x8d\xae')"" .env.example ""$(zh '\xe5\x88\x9b\xe5\xbb\xba')"" .env""$(zh '\xef\xbc\x9b\xe7\x94\x9f\xe4\xba\xa7\xe4\xbd\xbf\xe7\x94\xa8\xe5\x89\x8d\xe8\xaf\xb7\xe5\x85\x88\xe5\xa1\xab\xe5\x86\x99\xe7\x9c\x9f\xe5\xae\x9e\xe9\x85\x8d\xe7\xbd\xae')"
fi

if [[ -f "$ROOT_DIR/.env" ]] && grep -q "zt-sniper" "$ROOT_DIR/.env"; then
  echo "$(zh '\xe8\xad\xa6\xe5\x91\x8a\xef\xbc\x9a')"".env ""$(zh '\xe4\xbb\x8d\xe5\xbc\x95\xe7\x94\xa8')"" zt-sniper""$(zh '\xef\xbc\x9b\xe8\xbf\x81\xe7\xa7\xbb\xe6\x97\xb6\xe8\xaf\xb7\xe6\x94\xb9\xe4\xb8\xba')"" QUANT_APP_DIR=/opt/qt ""$(zh '\xe5\x92\x8c')"" QUANT_SERVICE_NAME=qt"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$(venv_pip)" install --upgrade pip
"$(venv_pip)" install -r "$ROOT_DIR/backend/requirements.txt"

mkdir -p "$ROOT_DIR/backend/data" "$BACKUP_ROOT"

section "后台入口"
print_admin_entry_path

ensure_nginx_upload_limit || true

if refresh_systemd_service; then
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl restart "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager -l || true
  else
    sudo systemctl restart "$SERVICE_NAME"
    sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
  fi
  verify_running_backend
fi

chmod +x "$ROOT_DIR/qt.sh" "$ROOT_DIR/scripts/"*.sh || true
QT_BIN="/usr/local/bin/qt"
if [[ "$(id -u)" -eq 0 ]]; then
  ln -sf "$ROOT_DIR/scripts/qt.sh" "$QT_BIN"
  echo "qt ""$(zh '\xe5\xbf\xab\xe6\x8d\xb7\xe5\x91\xbd\xe4\xbb\xa4\xe5\xb7\xb2\xe5\xae\x89\xe8\xa3\x85\xef\xbc\x9a')""$QT_BIN"
elif command -v sudo >/dev/null 2>&1; then
  sudo ln -sf "$ROOT_DIR/scripts/qt.sh" "$QT_BIN"
  echo "qt ""$(zh '\xe5\xbf\xab\xe6\x8d\xb7\xe5\x91\xbd\xe4\xbb\xa4\xe5\xb7\xb2\xe5\xae\x89\xe8\xa3\x85\xef\xbc\x9a')""$QT_BIN"
else
  echo "$(zh '\xe5\xbd\x93\xe5\x89\x8d\xe6\xb2\xa1\xe6\x9c\x89')"" sudo/root ""$(zh '\xe6\x9d\x83\xe9\x99\x90\xef\xbc\x9b\xe5\x8f\xaf\xe9\x80\x89\xe7\x9a\x84')"" qt ""$(zh '\xe5\xbf\xab\xe6\x8d\xb7\xe5\x91\xbd\xe4\xbb\xa4\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"
  echo "$(zh '\xe5\x8f\xaf\xe6\x89\x8b\xe5\x8a\xa8\xe6\x89\xa7\xe8\xa1\x8c\xef\xbc\x9a')""ln -sf $ROOT_DIR/scripts/qt.sh $QT_BIN"
fi

echo "$(zh '\xe5\xae\x89\xe8\xa3\x85\xe5\xae\x8c\xe6\x88\x90')"
echo "$(zh '\xe9\xa1\xb9\xe7\x9b\xae\xe6\xa0\xb9\xe7\x9b\xae\xe5\xbd\x95\xe5\x91\xbd\xe4\xbb\xa4\xef\xbc\x9a')""bash qt.sh restart"
echo "$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe5\x99\xa8\xe5\xbf\xab\xe6\x8d\xb7\xe5\x91\xbd\xe4\xbb\xa4\xef\xbc\x9a')""qt ""$(zh '\xe6\x89\x93\xe5\xbc\x80\xe8\xbf\x90\xe7\xbb\xb4\xe9\x9d\xa2\xe6\x9d\xbf\xef\xbc\x9b')""qt update ""$(zh '\xe7\x9b\xb4\xe6\x8e\xa5\xe4\xb8\x80\xe9\x94\xae\xe6\x9b\xb4\xe6\x96\xb0')"
