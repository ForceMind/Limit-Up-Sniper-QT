from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("QUANT_DATA_DIR") or BACKEND_DIR / "data").expanduser().resolve()
QUANT_DB_FILE = DATA_DIR / "quant_data.sqlite3"
SAMPLE_CODES = {"600001", "600002"}
SAMPLE_MARKERS = ("样例", "Fixture", "样例算力", "样例电力")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "--"}:
            return default
        return float(text)
    except Exception:
        return default


def digits6(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) > 6:
        text = text[-6:]
    return text if len(text) == 6 else ""


def contains_sample_marker(value: Any) -> bool:
    if isinstance(value, dict):
        if digits6(value.get("code") or value.get("stock_code")) in SAMPLE_CODES:
            return True
        return any(contains_sample_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_sample_marker(item) for item in value)
    if isinstance(value, str):
        return any(marker in value for marker in SAMPLE_MARKERS)
    return False


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or QUANT_DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def latest_news_time(db_path: Optional[Path] = None) -> str:
    database = db_path or QUANT_DB_FILE
    if not database.exists():
        return ""
    try:
        conn = _connect(database)
        try:
            if not _has_table(conn, "news_raw"):
                return ""
            row = conn.execute(
                """
                SELECT time_str, date, timestamp
                FROM news_raw
                WHERE timestamp > 0
                ORDER BY timestamp DESC, date DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT time_str, date, timestamp
                    FROM news_raw
                    ORDER BY date DESC, time_str DESC
                    LIMIT 1
                    """
                ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return ""
    if not row:
        return ""
    time_str = str(row["time_str"] or "").strip()
    if time_str:
        return time_str
    timestamp = int(safe_float(row["timestamp"], 0))
    if timestamp > 0:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    return str(row["date"] or "")


def latest_news_time_query_plan(db_path: Path) -> list[str]:
    """Expose the hot latest-news query plan for tests and deployment diagnostics."""
    conn = _connect(db_path)
    try:
        if not _has_table(conn, "news_raw"):
            return []
        rows = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT time_str, date, timestamp
            FROM news_raw
            WHERE timestamp > 0
            ORDER BY timestamp DESC, date DESC
            LIMIT 1
            """
        ).fetchall()
        return [str(row[3]) for row in rows]
    finally:
        conn.close()


def news_feed_query_plan(db_path: Path, data_date: str) -> list[str]:
    """Expose the hot date-filtered news feed query plan for tests and diagnostics."""
    conn = _connect(db_path)
    try:
        if not _has_table(conn, "news_raw"):
            return []
        rows = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT id, date, timestamp, time_str, source, text
            FROM news_raw
            WHERE date = ?
            ORDER BY timestamp DESC, time_str DESC
            LIMIT 20
            """,
            (data_date,),
        ).fetchall()
        return [str(row[3]) for row in rows]
    finally:
        conn.close()


def news_events_query_plan(db_path: Path, data_date: str) -> list[str]:
    """Expose the hot date-filtered news event query plan for tests and diagnostics."""
    conn = _connect(db_path)
    try:
        if not _has_table(conn, "news_events"):
            return []
        rows = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT event_id, date, timestamp, source, text, code, name, industry, event_type,
                   sentiment, impact_score, ai_score, reason
            FROM news_events
            WHERE date = ?
            ORDER BY impact_score DESC, timestamp DESC
            LIMIT 20
            """,
            (data_date,),
        ).fetchall()
        return [str(row[3]) for row in rows]
    finally:
        conn.close()


def _filter_sql(
    source_filter: set[str],
    keyword_filter: str,
    code_filter: str,
    prefix: str = "",
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    col = lambda name: f"{prefix}.{name}" if prefix else name
    if source_filter:
        placeholders = ",".join("?" for _ in source_filter)
        clauses.append(f"LOWER({col('source')}) IN ({placeholders})")
        params.extend(sorted(source_filter))
    if keyword_filter:
        clauses.append(f"LOWER({col('text')}) LIKE ?")
        params.append(f"%{keyword_filter}%")
    if code_filter:
        clauses.append(f"{col('text')} LIKE ?")
        params.append(f"%{code_filter}%")
    return clauses, params


def _select_data_date(
    conn: sqlite3.Connection,
    requested_date: str,
    fallback_latest: bool,
    clauses: list[str],
    params: list[Any],
) -> tuple[str, bool]:
    where = " AND ".join(["date = ?", *clauses])
    if requested_date:
        exact = conn.execute(f"SELECT date FROM news_raw WHERE {where} LIMIT 1", [requested_date, *params]).fetchone()
        if exact:
            return requested_date, True
    if not fallback_latest:
        return "", False
    fallback_clauses = list(clauses)
    fallback_params = list(params)
    if requested_date:
        fallback_clauses.insert(0, "date <= ?")
        fallback_params.insert(0, requested_date)
    fallback_where = f"WHERE {' AND '.join(fallback_clauses)}" if fallback_clauses else ""
    row = conn.execute(
        f"SELECT date FROM news_raw {fallback_where} GROUP BY date ORDER BY date DESC LIMIT 1",
        fallback_params,
    ).fetchone()
    return (str(row["date"] or ""), False) if row else ("", False)


def lightweight_news_feed(
    as_of: Optional[str] = None,
    limit: int = 120,
    fallback_latest: bool = True,
    source: Optional[str] = None,
    keyword: Optional[str] = None,
    code: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    database = db_path or QUANT_DB_FILE
    if not database.exists():
        return None
    limit = max(1, min(int(limit or 120), 1000))
    source_filter = {part.strip().lower() for part in str(source or "").split(",") if part.strip()}
    keyword_filter = str(keyword or "").strip().lower()
    code_filter = digits6(code or "")

    try:
        conn = _connect(database)
        try:
            if not _has_table(conn, "news_raw"):
                return None
            clauses, params = _filter_sql(source_filter, keyword_filter, code_filter)
            available_where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            available_dates = [
                str(row["date"] or "")
                for row in conn.execute(
                    f"SELECT date FROM news_raw {available_where} GROUP BY date ORDER BY date DESC LIMIT 60",
                    params,
                ).fetchall()
                if row["date"]
            ]
            requested_date = str(as_of or "").strip() or (available_dates[0] if available_dates else "")
            data_date, has_requested_date_data = _select_data_date(
                conn,
                requested_date,
                fallback_latest=fallback_latest,
                clauses=clauses,
                params=params,
            )
            selected_rows = []
            if data_date:
                selected_rows = conn.execute(
                    f"""
                    SELECT id, date, timestamp, time_str, source, text
                    FROM news_raw
                    WHERE {' AND '.join(['date = ?', *clauses])}
                    ORDER BY timestamp DESC, time_str DESC
                    LIMIT ?
                    """,
                    [data_date, *params, limit],
                ).fetchall()
            items = []
            for row in selected_rows:
                item = {
                    "id": str(row["id"] or ""),
                    "date": str(row["date"] or "")[:10],
                    "time": str(row["time_str"] or ""),
                    "source": str(row["source"] or "未知来源"),
                    "text": str(row["text"] or ""),
                    "timestamp": int(safe_float(row["timestamp"], 0)),
                }
                if not contains_sample_marker(item):
                    items.append(item)

            event_items = []
            if data_date and _has_table(conn, "news_events"):
                event_rows = conn.execute(
                    """
                    SELECT event_id, date, timestamp, source, text, code, name, industry, event_type,
                           sentiment, impact_score, ai_score, reason
                    FROM news_events
                    WHERE date = ?
                    ORDER BY impact_score DESC, timestamp DESC
                    LIMIT ?
                    """,
                    (data_date, limit),
                ).fetchall()
                for row in event_rows:
                    event = {
                        "event_id": str(row["event_id"] or ""),
                        "date": str(row["date"] or "")[:10],
                        "timestamp": int(safe_float(row["timestamp"], 0)),
                        "source": str(row["source"] or "sqlite"),
                        "text": str(row["text"] or "")[:700],
                        "code": digits6(row["code"]),
                        "name": str(row["name"] or ""),
                        "industry": str(row["industry"] or ""),
                        "event_type": str(row["event_type"] or ""),
                        "sentiment": round(safe_float(row["sentiment"], 0), 3),
                        "impact_score": round(safe_float(row["impact_score"], 50), 2),
                        "ai_score": round(safe_float(row["ai_score"], 0), 2),
                        "reason": str(row["reason"] or "")[:240],
                    }
                    if event["code"] and not contains_sample_marker(event):
                        event_items.append(event)

            return {
                "status": "ok",
                "requested_date": requested_date,
                "data_date": data_date,
                "latest_available_date": available_dates[0] if available_dates else "",
                "has_requested_date_data": has_requested_date_data,
                "count": len(items),
                "items": items[:limit],
                "events": event_items[:limit],
                "available_dates": available_dates,
                "filters": {
                    "source": sorted(source_filter),
                    "keyword": keyword_filter,
                    "code": code_filter,
                },
                "source": "sqlite_light",
            }
        finally:
            conn.close()
    except sqlite3.Error:
        return None
