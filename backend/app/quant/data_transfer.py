from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable
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

COPY_ONLY_IF_MISSING = {
    "ai_analysis_state.json",
    "biying_intraday_sync_state.json",
    "market_sentiment_cache.json",
    "news_fetch_state.json",
    "quant_job_state.json",
    "quant_state.json",
    "strategy_evolution_state.json",
    "trade_notification_state.json",
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


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _item_key(file_name: str, item: Any) -> str:
    if not isinstance(item, dict):
        return _digest(item)
    for key in ("id", "record_key", "event_id", "cache_key", "trade_id", "position_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    if file_name.endswith(".json") and str(item.get("date") or "").strip() and any(key in item for key in ("open", "close", "high", "low", "volume", "amount")):
        return "bar_date:" + str(item.get("date") or "").strip()[:10]
    if file_name == "news_history.json":
        return "news:" + (str(item.get("timestamp") or item.get("time_str") or "") + "|" + str(item.get("text") or ""))
    if file_name == "lhb_history.csv":
        return "lhb:" + "|".join(str(item.get(key) or "") for key in ("trade_date", "stock_code", "buyer_seat_name", "buy_amount", "sell_amount"))
    if file_name == "watchlist.json":
        return "watch:" + "|".join(str(item.get(key) or "") for key in ("code", "name", "strategy_type", "news_summary"))
    return _digest(item)


def _date_value(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("date", "trade_date", "time", "time_str", "ts", "analyzed_at", "updated_at", "created_at"):
        value = str(item.get(key) or "")
        if value:
            return value
    return ""


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _merge_list(file_name: str, existing: list[Any], incoming: list[Any]) -> tuple[list[Any], int]:
    merged: dict[str, Any] = {}
    for item in existing:
        merged[_item_key(file_name, item)] = item
    before = len(merged)
    for item in incoming:
        merged[_item_key(file_name, item)] = item
    values = list(merged.values())
    values.sort(key=_date_value, reverse=True)
    return values, max(0, len(merged) - before)


def _merge_dict_payload(file_name: str, existing: dict[str, Any], incoming: dict[str, Any]) -> tuple[dict[str, Any], int]:
    merged = dict(existing)
    added = 0
    if isinstance(existing.get("events"), list) or isinstance(incoming.get("events"), list):
        events, added = _merge_list(file_name, existing.get("events") if isinstance(existing.get("events"), list) else [], incoming.get("events") if isinstance(incoming.get("events"), list) else [])
        merged.update({key: value for key, value in incoming.items() if key != "events"})
        merged["events"] = events
        return merged, added
    if isinstance(existing.get("rows"), list) or isinstance(incoming.get("rows"), list):
        rows, added = _merge_list(file_name, existing.get("rows") if isinstance(existing.get("rows"), list) else [], incoming.get("rows") if isinstance(incoming.get("rows"), list) else [])
        merged.update({key: value for key, value in incoming.items() if key != "rows"})
        merged["rows"] = rows
        return merged, added
    if isinstance(existing.get("items"), list) or isinstance(incoming.get("items"), list):
        items, added = _merge_list(file_name, existing.get("items") if isinstance(existing.get("items"), list) else [], incoming.get("items") if isinstance(incoming.get("items"), list) else [])
        merged.update({key: value for key, value in incoming.items() if key != "items"})
        merged["items"] = items
        return merged, added
    before = set(merged.keys())
    merged.update(incoming)
    added = len(set(merged.keys()) - before)
    return merged, added


def _merge_json_file(target_file: Path, incoming_bytes: bytes) -> tuple[str, int]:
    file_name = target_file.name
    incoming = json.loads(incoming_bytes.decode("utf-8-sig"))
    if file_name in COPY_ONLY_IF_MISSING and target_file.exists():
        return "kept_existing", 0
    if not target_file.exists():
        _write_json(target_file, incoming)
        if isinstance(incoming, list):
            return "created", len(incoming)
        if isinstance(incoming, dict):
            if isinstance(incoming.get("events"), list):
                return "created", len(incoming["events"])
            if isinstance(incoming.get("rows"), list):
                return "created", len(incoming["rows"])
            if isinstance(incoming.get("items"), list):
                return "created", len(incoming["items"])
            return "created", len(incoming)
        return "created", 1
    existing = _read_json(target_file, [] if isinstance(incoming, list) else {})
    if isinstance(existing, list) and isinstance(incoming, list):
        merged, added = _merge_list(file_name, existing, incoming)
        _write_json(target_file, merged)
        return "merged", added
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged, added = _merge_dict_payload(file_name, existing, incoming)
        _write_json(target_file, merged)
        return "merged", added
    backup = target_file.with_suffix(target_file.suffix + f".before_import_{_now_stamp()}.bak")
    shutil.copy2(target_file, backup)
    target_file.write_bytes(incoming_bytes)
    return "replaced_incompatible", 1


def _read_csv_bytes(raw: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    return list(reader.fieldnames or []), [dict(row) for row in reader]


def _merge_csv_file(target_file: Path, incoming_bytes: bytes, rel_path: PurePosixPath) -> tuple[str, int]:
    incoming_fields, incoming_rows = _read_csv_bytes(incoming_bytes)
    if not target_file.exists():
        target_file.write_bytes(incoming_bytes)
        return "created", len(incoming_rows)
    with target_file.open("rb") as handle:
        existing_fields, existing_rows = _read_csv_bytes(handle.read())
    fields = existing_fields or incoming_fields
    for field in incoming_fields:
        if field not in fields:
            fields.append(field)
    merged: dict[str, dict[str, str]] = {}
    for row in existing_rows:
        key = str(row.get("time") or "") if rel_path.parts and rel_path.parts[0] == "kline_cache" else _item_key(target_file.name, row)
        merged[key or _digest(row)] = row
    before = len(merged)
    for row in incoming_rows:
        key = str(row.get("time") or "") if rel_path.parts and rel_path.parts[0] == "kline_cache" else _item_key(target_file.name, row)
        merged[key or _digest(row)] = row
    rows = list(merged.values())
    rows.sort(key=_date_value)
    tmp = target_file.with_suffix(target_file.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    tmp.replace(target_file)
    return "merged", max(0, len(merged) - before)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _merge_sqlite_file(target_file: Path, incoming_bytes: bytes) -> tuple[str, int]:
    if not target_file.exists():
        target_file.write_bytes(incoming_bytes)
        return "created", 1
    fd, temp_name = tempfile.mkstemp(prefix="qt_import_db_", suffix=".sqlite3")
    os.close(fd)
    try:
        with Path(temp_name).open("wb") as handle:
            handle.write(incoming_bytes)
        target = sqlite3.connect(target_file)
        try:
            target.execute("ATTACH DATABASE ? AS incoming", (temp_name,))
            source_tables = [
                row[0]
                for row in target.execute("SELECT name FROM incoming.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            ]
            changed = 0
            for table in source_tables:
                table_q = _quote_identifier(table)
                target_exists = target.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
                if not target_exists:
                    ddl = target.execute("SELECT sql FROM incoming.sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
                    if ddl and ddl[0]:
                        target.execute(str(ddl[0]))
                source_cols = [row[1] for row in target.execute(f"PRAGMA incoming.table_info({table_q})")]
                target_cols = [row[1] for row in target.execute(f"PRAGMA table_info({table_q})")]
                cols = [col for col in source_cols if col in target_cols]
                if not cols:
                    continue
                cols_sql = ", ".join(_quote_identifier(col) for col in cols)
                before = target.total_changes
                target.execute(f"INSERT OR REPLACE INTO {table_q} ({cols_sql}) SELECT {cols_sql} FROM incoming.{table_q}")
                changed += target.total_changes - before
            target.commit()
            target.execute("DETACH DATABASE incoming")
            return "merged", changed
        finally:
            target.close()
    finally:
        try:
            Path(temp_name).unlink(missing_ok=True)
        except Exception:
            pass


def _merge_member(target_file: Path, rel_path: PurePosixPath, incoming_bytes: bytes) -> tuple[str, int]:
    if target_file.name == "quant_data.sqlite3":
        return _merge_sqlite_file(target_file, incoming_bytes)
    if target_file.suffix.lower() == ".json":
        return _merge_json_file(target_file, incoming_bytes)
    if target_file.suffix.lower() == ".csv":
        return _merge_csv_file(target_file, incoming_bytes, rel_path)
    if target_file.suffix.lower() == ".jsonl":
        if not target_file.exists():
            target_file.write_bytes(incoming_bytes)
            return "created", len(incoming_bytes.splitlines())
        existing_lines = set(target_file.read_text(encoding="utf-8-sig", errors="ignore").splitlines())
        incoming_lines = [line for line in incoming_bytes.decode("utf-8-sig", errors="ignore").splitlines() if line]
        new_lines = [line for line in incoming_lines if line not in existing_lines]
        if new_lines:
            with target_file.open("a", encoding="utf-8") as handle:
                for line in new_lines:
                    handle.write(line.rstrip("\n") + "\n")
        return "merged", len(new_lines)
    if target_file.exists():
        return "kept_existing", 0
    target_file.write_bytes(incoming_bytes)
    return "created", 1


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
    added_records = 0
    actions: dict[str, int] = {}
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
            with source:
                action, added = _merge_member(target_file, rel_path, source.read())
            actions[action] = actions.get(action, 0) + 1
            added_records += int(added or 0)
            imported += 1
    return {
        "status": "ok",
        "imported_files": imported,
        "merge_actions": actions,
        "added_records": added_records,
        "payload_bytes": validation["payload_bytes"],
        "target_dir": str(target_dir),
        "imported_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }
