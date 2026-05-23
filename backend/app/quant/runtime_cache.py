from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.quant.engine import QUANT_DB_FILE


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _digest(*parts: Any) -> str:
    text = "|".join(_json_text(part) for part in parts)
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def env_int(name: str, default: int, minimum: int = 0, maximum: Optional[int] = None) -> int:
    try:
        value = int(float(os.getenv(name, "") or default))
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def cache_key(payload_type: str, parts: Dict[str, Any]) -> str:
    return _digest("frontend_payload_cache", payload_type, parts)[:32]


def _connect() -> sqlite3.Connection:
    QUANT_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(QUANT_DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS frontend_payload_cache (
            cache_key TEXT PRIMARY KEY,
            payload_type TEXT,
            params_hash TEXT,
            generated_at TEXT,
            expires_at TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_frontend_payload_cache_type ON frontend_payload_cache(payload_type, generated_at);
        CREATE INDEX IF NOT EXISTS idx_frontend_payload_cache_expires ON frontend_payload_cache(expires_at);
        """
    )
    return conn


def load_payload_cache(payload_type: str, parts: Dict[str, Any], ttl_seconds: int) -> Optional[Dict[str, Any]]:
    ttl_seconds = max(0, int(ttl_seconds or 0))
    if ttl_seconds <= 0 or not QUANT_DB_FILE.exists():
        return None
    key = cache_key(payload_type, parts)
    now = datetime.now()
    try:
        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT generated_at, expires_at, payload_json
                FROM frontend_payload_cache
                WHERE cache_key = ?
                LIMIT 1
                """,
                (key,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if not row:
        return None
    try:
        expires_at = datetime.fromisoformat(str(row["expires_at"] or ""))
    except Exception:
        return None
    if expires_at < now:
        return None
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    payload["frontend_payload_cache"] = "hit"
    payload["frontend_payload_cache_key"] = key
    payload["frontend_payload_cache_generated_at"] = str(row["generated_at"] or "")
    return payload


def save_payload_cache(payload_type: str, parts: Dict[str, Any], payload: Dict[str, Any], ttl_seconds: int) -> None:
    ttl_seconds = max(0, int(ttl_seconds or 0))
    if ttl_seconds <= 0 or not isinstance(payload, dict):
        return
    key = cache_key(payload_type, parts)
    generated_at = datetime.now()
    expires_at = generated_at.timestamp() + ttl_seconds
    clean_payload = dict(payload)
    clean_payload.pop("frontend_payload_cache", None)
    clean_payload.pop("frontend_payload_cache_key", None)
    clean_payload.pop("frontend_payload_cache_generated_at", None)
    try:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO frontend_payload_cache
                (cache_key, payload_type, params_hash, generated_at, expires_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    str(payload_type or ""),
                    _digest("payload_parts", parts)[:24],
                    generated_at.isoformat(timespec="seconds"),
                    datetime.fromtimestamp(expires_at).isoformat(timespec="seconds"),
                    _json_text(clean_payload),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        return


def purge_expired_payload_cache() -> int:
    if not QUANT_DB_FILE.exists():
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    try:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM frontend_payload_cache WHERE expires_at < ?", (now,))
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()
    except Exception:
        return 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _table_count(conn: sqlite3.Connection, table: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}{where_sql}", params).fetchone()
    return int(row["count"] if isinstance(row, sqlite3.Row) else row[0])


def _table_max(conn: sqlite3.Connection, table: str, column: str) -> str:
    if not _table_exists(conn, table):
        return ""
    row = conn.execute(f"SELECT MAX({column}) AS value FROM {table}").fetchone()
    return str((row["value"] if isinstance(row, sqlite3.Row) else row[0]) or "")


def _group_counts(conn: sqlite3.Connection, table: str, column: str) -> list[Dict[str, Any]]:
    if not _table_exists(conn, table):
        return []
    rows = conn.execute(
        f"""
        SELECT {column} AS name, COUNT(*) AS count, MAX(generated_at) AS latest_generated_at
        FROM {table}
        GROUP BY {column}
        ORDER BY count DESC, name ASC
        LIMIT 20
        """
    ).fetchall()
    return [
        {
            "name": str(row["name"] or ""),
            "count": int(row["count"] or 0),
            "latest_generated_at": str(row["latest_generated_at"] or ""),
        }
        for row in rows
    ]


def runtime_cache_status() -> Dict[str, Any]:
    if not QUANT_DB_FILE.exists():
        return {
            "status": "missing",
            "database": str(QUANT_DB_FILE),
            "tables": {},
            "total_rows": 0,
            "message": "SQLite 数据库不存在",
        }
    now = datetime.now().isoformat(timespec="seconds")
    try:
        conn = _connect()
        try:
            frontend_rows = _table_count(conn, "frontend_payload_cache")
            frontend_expired = _table_count(conn, "frontend_payload_cache", " WHERE expires_at < ?", (now,))
            account_rows = _table_count(conn, "strategy_runtime_snapshots")
            account_ttl = env_int("QT_STRATEGY_ACCOUNT_CACHE_TTL_SECONDS", 1800, minimum=0, maximum=86400)
            account_cutoff = (datetime.now() - timedelta(seconds=account_ttl)).isoformat(timespec="seconds") if account_ttl > 0 else now
            account_expired = _table_count(
                conn,
                "strategy_runtime_snapshots",
                " WHERE generated_at < ?",
                (account_cutoff,),
            ) if account_ttl > 0 else account_rows
            payload = {
                "status": "ok",
                "database": str(QUANT_DB_FILE),
                "total_rows": frontend_rows + account_rows,
                "expired_rows": frontend_expired + account_expired,
                "ttl_seconds": {
                    "recommendations": env_int("QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS", 900, minimum=0, maximum=86400),
                    "daily_plan": env_int("QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS", 1800, minimum=0, maximum=86400),
                    "account": account_ttl,
                },
                "tables": {
                    "frontend_payload_cache": {
                        "row_count": frontend_rows,
                        "expired_rows": frontend_expired,
                        "latest_generated_at": _table_max(conn, "frontend_payload_cache", "generated_at"),
                        "by_type": _group_counts(conn, "frontend_payload_cache", "payload_type"),
                    },
                    "strategy_runtime_snapshots": {
                        "row_count": account_rows,
                        "expired_rows": account_expired,
                        "latest_generated_at": _table_max(conn, "strategy_runtime_snapshots", "generated_at"),
                        "by_source": _group_counts(conn, "strategy_runtime_snapshots", "source"),
                    },
                },
            }
            return payload
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "error", "database": str(QUANT_DB_FILE), "error": str(exc)}


def clear_runtime_cache(scope: str = "expired") -> Dict[str, Any]:
    scope = str(scope or "expired").strip().lower()
    if scope not in {"expired", "all", "payload", "account"}:
        scope = "expired"
    deleted = {"frontend_payload_cache": 0, "strategy_runtime_snapshots": 0}
    if not QUANT_DB_FILE.exists():
        return {"status": "missing", "scope": scope, "deleted": deleted, "cache": runtime_cache_status()}
    now = datetime.now()
    try:
        conn = _connect()
        try:
            if _table_exists(conn, "frontend_payload_cache"):
                if scope in {"all", "payload"}:
                    cur = conn.execute("DELETE FROM frontend_payload_cache")
                    deleted["frontend_payload_cache"] = int(cur.rowcount or 0)
                elif scope == "expired":
                    cur = conn.execute("DELETE FROM frontend_payload_cache WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
                    deleted["frontend_payload_cache"] = int(cur.rowcount or 0)
            if _table_exists(conn, "strategy_runtime_snapshots"):
                if scope in {"all", "account"}:
                    cur = conn.execute("DELETE FROM strategy_runtime_snapshots")
                    deleted["strategy_runtime_snapshots"] = int(cur.rowcount or 0)
                elif scope == "expired":
                    account_ttl = env_int("QT_STRATEGY_ACCOUNT_CACHE_TTL_SECONDS", 1800, minimum=0, maximum=86400)
                    if account_ttl <= 0:
                        cur = conn.execute("DELETE FROM strategy_runtime_snapshots")
                    else:
                        cutoff = (now - timedelta(seconds=account_ttl)).isoformat(timespec="seconds")
                        cur = conn.execute("DELETE FROM strategy_runtime_snapshots WHERE generated_at < ?", (cutoff,))
                    deleted["strategy_runtime_snapshots"] = int(cur.rowcount or 0)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        return {"status": "error", "scope": scope, "deleted": deleted, "error": str(exc)}
    return {"status": "ok", "scope": scope, "deleted": deleted, "cache": runtime_cache_status()}
