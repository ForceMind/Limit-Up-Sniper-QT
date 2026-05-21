#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root
ensure_data_dir
mkdir -p "$BACKUP_ROOT"

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_ROOT/backend_data_${STAMP}.tar.gz"

tar -czf "$BACKUP_FILE" -C "$ROOT_DIR/backend" data

KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
find "$BACKUP_ROOT" -name "backend_data_*.tar.gz" -type f -mtime "+$KEEP_DAYS" -delete || true

echo "$BACKUP_FILE"
