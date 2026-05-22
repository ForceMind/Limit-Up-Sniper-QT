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

BACKUP_FILE="${1:-}"
if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "$(zh '\xe7\x94\xa8\xe6\xb3\x95\xef\xbc\x9a')""bash qt.sh restore /path/to/backend_data_YYYYmmdd_HHMMSS.tar.gz" >&2
  exit 1
fi

CURRENT_BACKUP="$(bash "$SCRIPT_DIR/backup_data.sh")"
echo "$(zh '\xe5\xbd\x93\xe5\x89\x8d\xe6\x95\xb0\xe6\x8d\xae\xe5\xb7\xb2\xe5\x85\x88\xe5\xa4\x87\xe4\xbb\xbd\xe5\x88\xb0\xef\xbc\x9a')""$CURRENT_BACKUP"

rm -rf "$ROOT_DIR/backend/data.restore_tmp"
mkdir -p "$ROOT_DIR/backend/data.restore_tmp"
tar -xzf "$BACKUP_FILE" -C "$ROOT_DIR/backend/data.restore_tmp"

if [[ ! -d "$ROOT_DIR/backend/data.restore_tmp/data" ]]; then
  echo "$(zh '\xe9\x94\x99\xe8\xaf\xaf\xef\xbc\x9a\xe5\xa4\x87\xe4\xbb\xbd\xe5\x8e\x8b\xe7\xbc\xa9\xe5\x8c\x85\xe4\xb8\xad\xe6\xb2\xa1\xe6\x9c\x89')"" backend/data ""$(zh '\xe7\x9b\xae\xe5\xbd\x95')" >&2
  exit 1
fi

rm -rf "$ROOT_DIR/backend/data"
mv "$ROOT_DIR/backend/data.restore_tmp/data" "$ROOT_DIR/backend/data"
rm -rf "$ROOT_DIR/backend/data.restore_tmp"

echo "$(zh '\xe5\xb7\xb2\xe4\xbb\x8e\xe5\xa4\x87\xe4\xbb\xbd\xe6\x81\xa2\xe5\xa4\x8d')"" backend/data""$(zh '\xef\xbc\x9a')""$BACKUP_FILE"
