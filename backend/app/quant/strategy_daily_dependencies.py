from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

from app.quant.engine_utils import safe_float
from app.quant.quant_paths import QUANT_DB_FILE


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    if not str(table or "").replace("_", "").isalnum():
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            [table],
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _sqlite_date_count(
    conn: sqlite3.Connection,
    *,
    table: str,
    date_column: str,
    date: str,
    count_expr: str = "COUNT(*)",
) -> Dict[str, Any]:
    safe_table = str(table or "")
    safe_column = str(date_column or "")
    if not safe_table.replace("_", "").isalnum() or not safe_column.replace("_", "").isalnum():
        return {"table": safe_table, "date_column": safe_column, "exists": False, "count": 0}
    exists = _sqlite_table_exists(conn, safe_table)
    if not exists:
        return {"table": safe_table, "date_column": safe_column, "exists": False, "count": 0}
    try:
        row = conn.execute(
            f"SELECT {count_expr} FROM {safe_table} WHERE {safe_column} = ?",
            [date],
        ).fetchone()
        count = int(safe_float((row or [0])[0], 0))
    except Exception:
        count = 0
    return {"table": safe_table, "date_column": safe_column, "exists": True, "count": count}


def strategy_daily_data_dependencies(
    date: str,
    mode: str,
    *,
    db_file: Path = QUANT_DB_FILE,
) -> Dict[str, Any]:
    clean_date = str(date or "").strip()[:10]
    clean_mode = str(mode or "daily").strip().lower()
    payload: Dict[str, Any] = {
        "date": clean_date,
        "mode": clean_mode,
        "db_path": str(db_file),
        "status": "missing_db",
        "ready_for_strategy_run": False,
        "warnings": [],
        "checks": {},
    }
    if not clean_date:
        payload["warnings"] = ["missing_date"]
        return payload
    if not db_file.exists():
        payload["warnings"] = ["missing_quant_db"]
        return payload

    try:
        conn = sqlite3.connect(db_file)
        try:
            checks = {
                "news_raw": _sqlite_date_count(conn, table="news_raw", date_column="date", date=clean_date),
                "news_events": _sqlite_date_count(conn, table="news_events", date_column="date", date=clean_date),
                "market_daily_bars": _sqlite_date_count(
                    conn,
                    table="market_daily_bars",
                    date_column="date",
                    date=clean_date,
                    count_expr="COUNT(DISTINCT code)",
                ),
                "market_minute_bars": _sqlite_date_count(
                    conn,
                    table="market_minute_bars",
                    date_column="date",
                    date=clean_date,
                    count_expr="COUNT(DISTINCT code)",
                ),
                "lhb_records": _sqlite_date_count(conn, table="lhb_records", date_column="trade_date", date=clean_date),
            }
        finally:
            conn.close()
    except Exception as exc:
        payload["status"] = "error"
        payload["warnings"] = [f"dependency_query_failed:{str(exc)[:120]}"]
        return payload

    news_raw_count = int(checks["news_raw"].get("count", 0) or 0)
    news_event_count = int(checks["news_events"].get("count", 0) or 0)
    daily_code_count = int(checks["market_daily_bars"].get("count", 0) or 0)
    minute_code_count = int(checks["market_minute_bars"].get("count", 0) or 0)
    lhb_count = int(checks["lhb_records"].get("count", 0) or 0)
    warnings = []
    if news_raw_count <= 0:
        warnings.append("no_raw_news_for_date")
    if news_event_count <= 0:
        warnings.append("no_structured_news_events_for_date")
    if daily_code_count <= 0:
        warnings.append("no_daily_market_bars_for_date")
    if clean_mode == "intraday" and minute_code_count <= 0:
        warnings.append("no_intraday_market_bars_for_date")
    news_ready = news_raw_count > 0 or news_event_count > 0
    market_ready = daily_code_count > 0 and (clean_mode != "intraday" or minute_code_count > 0)
    ready = bool(news_ready and market_ready)
    payload.update(
        {
            "status": "ok" if ready else "partial",
            "ready_for_strategy_run": ready,
            "news_ready": bool(news_ready),
            "market_ready": bool(market_ready),
            "structured_event_ready": news_event_count > 0,
            "daily_market_code_count": daily_code_count,
            "intraday_market_code_count": minute_code_count,
            "raw_news_count": news_raw_count,
            "structured_event_count": news_event_count,
            "lhb_count": lhb_count,
            "warnings": warnings,
            "checks": checks,
        }
    )
    return payload
