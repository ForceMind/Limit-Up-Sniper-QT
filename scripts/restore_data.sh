#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root

BACKUP_FILE="${1:-}"
if [[ -z "$BACKUP_FILE" || ! -f "$BACKUP_FILE" ]]; then
  echo "usage: scripts/restore_data.sh /path/to/backend_data_YYYYmmdd_HHMMSS.tar.gz" >&2
  exit 1
fi

CURRENT_BACKUP="$("$SCRIPT_DIR/backup_data.sh")"
echo "saved current data to $CURRENT_BACKUP"

rm -rf "$ROOT_DIR/backend/data.restore_tmp"
mkdir -p "$ROOT_DIR/backend/data.restore_tmp"
tar -xzf "$BACKUP_FILE" -C "$ROOT_DIR/backend/data.restore_tmp"

if [[ ! -d "$ROOT_DIR/backend/data.restore_tmp/data" ]]; then
  echo "error: backup archive does not contain backend/data" >&2
  exit 1
fi

rm -rf "$ROOT_DIR/backend/data"
mv "$ROOT_DIR/backend/data.restore_tmp/data" "$ROOT_DIR/backend/data"
rm -rf "$ROOT_DIR/backend/data.restore_tmp"

echo "restored backend/data from $BACKUP_FILE"
