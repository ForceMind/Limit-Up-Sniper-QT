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
DATA_DIR="$(runtime_data_dir)"
STAGE_DIR="$(mktemp -d "$BACKUP_ROOT/backend_data_stage_XXXXXX")"

mkdir -p "$DATA_DIR"
mkdir -p "$STAGE_DIR/data"
tar -C "$DATA_DIR" -cf - . | tar -C "$STAGE_DIR/data" -xf -
tar -czf "$BACKUP_FILE" -C "$STAGE_DIR" data
rm -rf "$STAGE_DIR"

KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
find "$BACKUP_ROOT" -name "backend_data_*.tar.gz" -type f -mtime "+$KEEP_DAYS" -delete || true

echo "$BACKUP_FILE"
