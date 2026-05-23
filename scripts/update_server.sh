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

if [[ -d "$ROOT_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
  git -C "$ROOT_DIR" pull --ff-only
else
  echo "$(zh '\xe6\x9c\xaa\xe6\x89\xbe\xe5\x88\xb0')"" Git ""$(zh '\xe4\xbb\x93\xe5\xba\x93\xef\xbc\x8c\xe8\xb7\xb3\xe8\xbf\x87')"" git pull"
fi

BACKUP_FILE="$(bash "$SCRIPT_DIR/backup_data.sh")"
echo "$(zh '\xe6\x95\xb0\xe6\x8d\xae\xe5\xa4\x87\xe4\xbb\xbd\xe5\xb7\xb2\xe5\x88\x9b\xe5\xbb\xba\xef\xbc\x9a')""$BACKUP_FILE"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$(venv_pip)" install --upgrade pip
"$(venv_pip)" install -r "$ROOT_DIR/backend/requirements.txt"

auto_migrate_sqlite

ensure_nginx_upload_limit || true

bash "$SCRIPT_DIR/restart_server.sh"

echo "$(zh '\xe6\x9b\xb4\xe6\x96\xb0\xe5\xae\x8c\xe6\x88\x90')"
