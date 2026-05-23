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
PY_BIN="$(check_python_bin)"

mkdir -p "$DATA_DIR"
mkdir -p "$STAGE_DIR/data"
cleanup_stage() {
  rm -rf "$STAGE_DIR"
}
trap cleanup_stage EXIT

if [[ -z "$PY_BIN" ]]; then
  warn "找不到 Python，使用 tar 直接备份；如果后台正在写入文件，可能出现 file changed 警告"
  set +e
  tar --ignore-failed-read --warning=no-file-changed -C "$DATA_DIR" -cf - . | tar -C "$STAGE_DIR/data" -xf -
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    die "数据备份失败，请先停止服务后重试：systemctl stop ${SERVICE_NAME} && qt update"
  fi
else
  "$PY_BIN" - "$DATA_DIR" "$STAGE_DIR/data" <<'PY'
import os
import shutil
import sqlite3
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()

SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".tmp", ".part", ".swp"}
SKIP_SQLITE_SIDE_FILES = ("-wal", "-shm", "-journal")


def warn(message: str) -> None:
    print(f"[注意] {message}", file=sys.stderr)


def copy_sqlite(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=30)
        dst_conn = sqlite3.connect(tmp)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        tmp.replace(dst)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        warn(f"SQLite 在线备份失败，退回文件复制：{src.name}，原因：{exc}")
        shutil.copy2(src, dst)


def copy_regular(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except FileNotFoundError:
        warn(f"备份时文件已变化或删除，已跳过：{src.relative_to(source)}")
    except PermissionError as exc:
        warn(f"备份时无权限读取，已跳过：{src.relative_to(source)}，原因：{exc}")


for root, dirs, files in os.walk(source):
    root_path = Path(root)
    dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
    for name in files:
        src = root_path / name
        rel = src.relative_to(source)
        if name.endswith(SKIP_SQLITE_SIDE_FILES) or src.suffix in SKIP_SUFFIXES:
            continue
        dst = target / rel
        if src.suffix.lower() in {".sqlite3", ".db"}:
            copy_sqlite(src, dst)
        else:
            copy_regular(src, dst)
PY
fi

tar -czf "$BACKUP_FILE" -C "$STAGE_DIR" data
trap - EXIT
rm -rf "$STAGE_DIR"

KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
find "$BACKUP_ROOT" -name "backend_data_*.tar.gz" -type f -mtime "+$KEEP_DAYS" -delete || true

echo "$BACKUP_FILE"
