#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root
ensure_data_dir

BACKUP_FILE="$("$SCRIPT_DIR/backup_data.sh")"
echo "backup created: $BACKUP_FILE"

if [[ -d "$ROOT_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
  git -C "$ROOT_DIR" pull --ff-only
else
  echo "git repository not found; skipping git pull"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$(venv_pip)" install --upgrade pip
"$(venv_pip)" install -r "$ROOT_DIR/backend/requirements.txt"

"$SCRIPT_DIR/restart_server.sh"

echo "update complete"
