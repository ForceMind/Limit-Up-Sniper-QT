#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root
ensure_data_dir

BACKUP_FILE="$(bash "$SCRIPT_DIR/backup_data.sh")"
echo "数据备份已创建：$BACKUP_FILE"

if [[ -d "$ROOT_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
  git -C "$ROOT_DIR" pull --ff-only
else
  echo "未找到 Git 仓库，跳过 git pull"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$(venv_pip)" install --upgrade pip
"$(venv_pip)" install -r "$ROOT_DIR/backend/requirements.txt"

bash "$SCRIPT_DIR/restart_server.sh"

echo "更新完成"
