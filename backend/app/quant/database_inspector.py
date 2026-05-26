from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.quant.quant_paths import DATA_DIR, QUANT_DB_FILE


def file_meta(path: Path) -> Dict[str, Any]:
    try:
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        return {
            "name": path.name,
            "path": str(path),
            "exists": True,
            "size_bytes": stat.st_size,
            "modified_at": modified_at,
        }
    except OSError:
        return {"name": path.name, "path": str(path), "exists": False, "size_bytes": 0, "modified_at": ""}


def quote_sqlite_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def sqlite_user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"] if isinstance(row, sqlite3.Row) else row[0]) for row in rows]


def sqlite_columns(conn: sqlite3.Connection, table_name: str) -> list[Dict[str, Any]]:
    rows = conn.execute(f"PRAGMA table_info({quote_sqlite_identifier(table_name)})").fetchall()
    return [
        {
            "name": str(row["name"]),
            "type": str(row["type"] or ""),
            "notnull": bool(row["notnull"]),
            "primary_key": bool(row["pk"]),
        }
        for row in rows
    ]


def sqlite_count(conn: sqlite3.Connection, table_name: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {quote_sqlite_identifier(table_name)}").fetchone()
        return int(row["count"] if isinstance(row, sqlite3.Row) else row[0])
    except sqlite3.Error:
        return 0


def db_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<bytes {len(value)}>"
    if value is None or isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    if len(text) > 1200:
        return text[:1200] + "...（已截断）"
    return text


def strategy_state_files(data_dir: Optional[Path] = None) -> list[Dict[str, Any]]:
    root = data_dir or DATA_DIR
    files: list[Dict[str, Any]] = []
    for path in sorted(root.glob("strategy_evolution_state*.json")):
        meta = file_meta(path)
        meta["role"] = "current" if path.name == "strategy_evolution_state.json" else "archived"
        files.append(meta)
    return files


def database_overview(db_path: Optional[Path] = None, data_dir: Optional[Path] = None) -> Dict[str, Any]:
    database = db_path or QUANT_DB_FILE
    root = data_dir or DATA_DIR
    payload: Dict[str, Any] = {
        "status": "ok",
        "database": file_meta(database),
        "data_dir": str(root),
        "state_files": strategy_state_files(root),
        "tables": [],
        "table_count": 0,
        "total_rows": 0,
    }
    if not database.exists():
        payload["status"] = "missing"
        payload["message"] = "SQLite 数据库不存在，请先运行 qt migrate 或等待服务器自动迁移"
        return payload
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        tables = []
        for name in sqlite_user_tables(conn):
            columns = sqlite_columns(conn, name)
            row_count = sqlite_count(conn, name)
            tables.append(
                {
                    "name": name,
                    "row_count": row_count,
                    "column_count": len(columns),
                    "columns": [item["name"] for item in columns],
                }
            )
        payload["tables"] = tables
        payload["table_count"] = len(tables)
        payload["total_rows"] = sum(int(item.get("row_count") or 0) for item in tables)
        return payload
    finally:
        conn.close()


def database_table_rows(
    table_name: str,
    limit: int = 50,
    offset: int = 0,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    table_name = str(table_name or "").strip()
    if not table_name:
        raise ValueError("table_name is required")
    database = db_path or QUANT_DB_FILE
    if not database.exists():
        raise FileNotFoundError("SQLite database not found")
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        tables = set(sqlite_user_tables(conn))
        if table_name not in tables:
            raise LookupError(f"table not found: {table_name}")
        clean_limit = max(1, min(int(limit or 50), 200))
        clean_offset = max(0, int(offset or 0))
        columns = sqlite_columns(conn, table_name)
        column_names = [item["name"] for item in columns]
        order_sql = ""
        for candidate in (
            "updated_at",
            "generated_at",
            "finished_at",
            "created_at",
            "analyzed_at",
            "date",
            "ts",
            "timestamp",
            "id",
        ):
            if candidate in column_names:
                order_sql = f" ORDER BY {quote_sqlite_identifier(candidate)} DESC"
                break
        rows = conn.execute(
            f"SELECT * FROM {quote_sqlite_identifier(table_name)}{order_sql} LIMIT ? OFFSET ?",
            (clean_limit, clean_offset),
        ).fetchall()
        return {
            "status": "ok",
            "table": table_name,
            "columns": columns,
            "rows": [{key: db_cell(row[key]) for key in row.keys()} for row in rows],
            "limit": clean_limit,
            "offset": clean_offset,
            "row_count": sqlite_count(conn, table_name),
        }
    finally:
        conn.close()
