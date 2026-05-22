from __future__ import annotations

import shutil
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo

from app.quant.engine import DATA_DIR


SAFE_FILES = {
    "news_history.json",
    "news_analysis_records.json",
    "quant_events_cache.json",
    "ai_cache.json",
    "ai_usage_logs.jsonl",
    "lhb_history.csv",
    "biying_all_market_cache.json",
    "biying_pool_cache.json",
    "biying_stock_list.json",
    "market_pools.json",
    "market_sentiment_cache.json",
    "watchlist.json",
    "news_fetch_state.json",
    "ai_analysis_state.json",
    "biying_intraday_sync_state.json",
    "quant_job_state.json",
    "quant_state.json",
    "strategy_evolution_state.json",
    "trade_notification_state.json",
    "quant_data.sqlite3",
}

SAFE_DIRS = {
    "kline_day_cache",
    "kline_cache",
}

OPTIONAL_LOG_FILES = {
    "access_logs.json",
    "quant_runtime_logs.jsonl",
    "runtime_logs.jsonl",
    "trade_notification_logs.jsonl",
}

BLOCKED_NAMES = {
    ".env",
    "auth.json",
    "admin_credentials.json",
    "admin_sessions.json",
    "config.json",
    "ws_token_secret.txt",
    "commercial.db",
    "biying_quota.sqlite3",
}


class DataPackageError(ValueError):
    pass


def _now_stamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")


def _safe_source_members(source_dir: Path, include_logs: bool = False) -> Iterable[tuple[Path, str]]:
    allowed_files = set(SAFE_FILES)
    if include_logs:
        allowed_files.update(OPTIONAL_LOG_FILES)
    for name in sorted(allowed_files):
        if name in BLOCKED_NAMES:
            continue
        path = source_dir / name
        if path.is_file():
            yield path, f"backend/data/{name}"
    for dirname in sorted(SAFE_DIRS):
        root = source_dir / dirname
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name not in BLOCKED_NAMES:
                rel = path.relative_to(root).as_posix()
                yield path, f"backend/data/{dirname}/{rel}"


def create_safe_data_package(
    output_dir: Path,
    source_dir: Path = DATA_DIR,
    include_logs: bool = False,
) -> Dict[str, Any]:
    source_dir = source_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"qt_safe_data_export_{_now_stamp()}.tar.gz"
    count = 0
    with tarfile.open(output_file, "w:gz") as archive:
        for path, arcname in _safe_source_members(source_dir, include_logs=include_logs):
            archive.add(path, arcname=arcname)
            count += 1
    return {
        "status": "ok",
        "package_file": str(output_file),
        "size_bytes": output_file.stat().st_size,
        "file_count": count,
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _member_rel_path(name: str) -> PurePosixPath:
    normalized = PurePosixPath(str(name).replace("\\", "/"))
    parts = normalized.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise DataPackageError(f"非法路径：{name}")
    if normalized.is_absolute():
        raise DataPackageError(f"不允许绝对路径：{name}")
    if len(parts) >= 3 and parts[0] == "backend" and parts[1] == "data":
        return PurePosixPath(*parts[2:])
    if len(parts) >= 2 and parts[0] == "data":
        return PurePosixPath(*parts[1:])
    raise DataPackageError(f"只允许 backend/data 下的数据：{name}")


def _is_allowed_rel(rel_path: PurePosixPath) -> bool:
    parts = rel_path.parts
    if not parts:
        return False
    name = parts[-1]
    if name in BLOCKED_NAMES:
        return False
    if len(parts) == 1:
        return name in SAFE_FILES or name in OPTIONAL_LOG_FILES
    if parts[0] in SAFE_DIRS:
        return name.endswith((".json", ".csv"))
    return False


def validate_data_package(package_file: Path) -> Dict[str, Any]:
    if not package_file.is_file():
        raise DataPackageError("上传文件不存在")
    if not tarfile.is_tarfile(package_file):
        raise DataPackageError("只支持 tar.gz 数据包")
    files = 0
    total_size = 0
    with tarfile.open(package_file, "r:*") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            if member.issym() or member.islnk() or not member.isfile():
                raise DataPackageError(f"不允许特殊文件：{member.name}")
            rel_path = _member_rel_path(member.name)
            if not _is_allowed_rel(rel_path):
                raise DataPackageError(f"不允许导入该文件：{member.name}")
            files += 1
            total_size += max(0, int(member.size or 0))
    return {"files": files, "payload_bytes": total_size}


def import_data_package(package_file: Path, target_dir: Path = DATA_DIR) -> Dict[str, Any]:
    target_dir = target_dir.resolve()
    validation = validate_data_package(package_file)
    imported = 0
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(package_file, "r:*") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            rel_path = _member_rel_path(member.name)
            target_file = (target_dir / Path(*rel_path.parts)).resolve()
            if target_dir not in target_file.parents and target_file != target_dir:
                raise DataPackageError(f"非法解压目标：{member.name}")
            target_file.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, target_file.open("wb") as output:
                shutil.copyfileobj(source, output)
            imported += 1
    return {
        "status": "ok",
        "imported_files": imported,
        "payload_bytes": validation["payload_bytes"],
        "target_dir": str(target_dir),
        "imported_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }
