from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from app.quant.engine_utils import safe_float


EMPTY_FOLLOW_DIAGNOSTICS = {
    "current_period": {},
    "account_snapshot": {},
    "positions": [],
    "recent_trades": [],
    "periods": [],
}


class StrategyFollowRepository:
    def __init__(
        self,
        *,
        db_exists: Callable[[], bool],
        connect_db: Callable[[], sqlite3.Connection],
        json_text: Callable[[Any], str],
        digest: Callable[..., str],
        user_follow_snapshot_key: Callable[..., tuple[str, str]],
        user_follow_snapshot_is_fresh: Callable[[str], bool],
    ) -> None:
        self._db_exists = db_exists
        self._connect_db = connect_db
        self._json_text = json_text
        self._digest = digest
        self._user_follow_snapshot_key = user_follow_snapshot_key
        self._user_follow_snapshot_is_fresh = user_follow_snapshot_is_fresh

    def load_user_follow_account(
        self,
        username: str,
        model_id: str,
        initial_cash: Any,
        follow_start_date: Optional[str],
        as_of: Optional[str],
        limit: int,
        model_version: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self._db_exists():
            return None
        snapshot_id, _params_hash = self._user_follow_snapshot_key(
            username,
            model_id,
            params or {},
            initial_cash,
            follow_start_date,
            as_of,
            limit,
            model_version=model_version,
        )
        try:
            conn = self._connect_db()
            try:
                row = conn.execute(
                    """
                    SELECT generated_at, source, account_json
                    FROM user_follow_snapshots
                    WHERE snapshot_id = ?
                    LIMIT 1
                    """,
                    (snapshot_id,),
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            return None
        if not row or not self._user_follow_snapshot_is_fresh(str(row["generated_at"] or "")):
            return None
        try:
            payload = json.loads(str(row["account_json"] or "{}"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        payload["strategy_account_cache"] = "user_follow"
        payload["strategy_account_source"] = payload.get("strategy_account_source") or str(row["source"] or "user_follow_snapshot")
        payload["user_follow_snapshot_id"] = snapshot_id
        payload["user_follow_snapshot_generated_at"] = str(row["generated_at"] or "")
        return payload

    def save_user_follow_account(
        self,
        username: str,
        model_id: str,
        params: Dict[str, Any],
        initial_cash: Any,
        follow_start_date: Optional[str],
        as_of: Optional[str],
        limit: int,
        account: Dict[str, Any],
        model_version: str = "",
        source: str = "",
    ) -> None:
        if not isinstance(account, dict):
            return
        clean_username = str(username or "anonymous").strip() or "anonymous"
        clean_model_id = str(model_id or "active").strip() or "active"
        snapshot_id, params_hash = self._user_follow_snapshot_key(
            clean_username,
            clean_model_id,
            params or {},
            initial_cash,
            follow_start_date,
            as_of,
            limit,
            model_version=model_version,
        )
        account_payload = dict(account)
        account_payload.pop("strategy_account_cache", None)
        account_payload.pop("strategy_account_cache_key", None)
        account_payload.pop("user_follow_snapshot_id", None)
        account_payload.pop("user_follow_snapshot_generated_at", None)
        generated_at = datetime.now().isoformat(timespec="seconds")
        source_text = str(source or account_payload.get("strategy_account_source") or "user_follow_account")
        summary = account_payload.get("account") if isinstance(account_payload.get("account"), dict) else {}
        positions = [dict(item) for item in account_payload.get("positions", []) if isinstance(item, dict)]
        trade_rows = self._account_trade_rows(account_payload)
        try:
            conn = self._connect_db()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO user_follow_snapshots
                    (snapshot_id, username, model_id, model_version, params_hash, follow_start_date,
                     as_of, initial_cash, record_limit, source, generated_at, total_asset,
                     return_pct, position_count, deal_count, account_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        clean_username,
                        clean_model_id,
                        str(model_version or ""),
                        params_hash,
                        str(follow_start_date or ""),
                        str(as_of or ""),
                        safe_float(initial_cash, 0),
                        int(limit or 0),
                        source_text,
                        generated_at,
                        safe_float(summary.get("total_asset"), 0),
                        safe_float(summary.get("return_pct"), 0),
                        int(safe_float(summary.get("position_count"), len(positions))),
                        int(safe_float(summary.get("deal_count"), len(trade_rows))),
                        self._json_text(account_payload),
                    ),
                )
                conn.execute("DELETE FROM user_follow_positions WHERE snapshot_id = ?", (snapshot_id,))
                conn.execute("DELETE FROM user_follow_trades WHERE snapshot_id = ?", (snapshot_id,))
                for seq, position in enumerate(positions):
                    code = str(position.get("code") or "").strip()
                    position_id = self._digest("user_follow_position", snapshot_id, seq, code, position)[:40]
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO user_follow_positions
                        (position_id, snapshot_id, username, model_id, model_version, params_hash,
                         follow_start_date, as_of, code, name, qty, available_qty, entry_date,
                         entry_price, last_price, market_value, pnl_pct, source, generated_at, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            position_id,
                            snapshot_id,
                            clean_username,
                            clean_model_id,
                            str(model_version or ""),
                            params_hash,
                            str(follow_start_date or ""),
                            str(as_of or ""),
                            code,
                            str(position.get("name") or ""),
                            safe_float(position.get("qty"), 0),
                            safe_float(position.get("available_qty"), safe_float(position.get("qty"), 0)),
                            str(position.get("entry_date") or position.get("buy_date") or ""),
                            safe_float(position.get("entry_price"), safe_float(position.get("cost_price"), 0)),
                            safe_float(position.get("last_price"), safe_float(position.get("price"), 0)),
                            safe_float(position.get("market_value"), 0),
                            safe_float(position.get("pnl_pct"), safe_float(position.get("return_pct"), 0)),
                            source_text,
                            generated_at,
                            self._json_text(position),
                        ),
                    )
                for seq, trade in enumerate(trade_rows):
                    code = str(trade.get("code") or "").strip()
                    qty = safe_float(trade.get("qty"), 0)
                    price = safe_float(trade.get("price"), 0)
                    trade_id = self._digest("user_follow_trade", snapshot_id, seq, code, trade)[:40]
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO user_follow_trades
                        (trade_id, snapshot_id, username, model_id, model_version, params_hash,
                         follow_start_date, date, time, side, code, name, qty, price, amount,
                         pnl_pct, source, generated_at, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trade_id,
                            snapshot_id,
                            clean_username,
                            clean_model_id,
                            str(model_version or ""),
                            params_hash,
                            str(follow_start_date or ""),
                            str(trade.get("date") or trade.get("trade_date") or ""),
                            str(trade.get("time") or trade.get("trade_time") or ""),
                            str(trade.get("side") or ""),
                            code,
                            str(trade.get("name") or ""),
                            qty,
                            price,
                            safe_float(trade.get("amount"), qty * price),
                            safe_float(trade.get("pnl_pct"), safe_float(trade.get("return_pct"), 0)),
                            source_text,
                            generated_at,
                            self._json_text(trade),
                        ),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            return

    def record_user_follow_period(
        self,
        username: str,
        profile: Dict[str, Any],
        reason: str = "",
        source: str = "",
        previous_profile: Optional[Dict[str, Any]] = None,
        created_at: str = "",
    ) -> Dict[str, Any]:
        if not isinstance(profile, dict):
            return {"status": "invalid"}
        clean_username = str(username or "").strip()
        if not clean_username:
            return {"status": "invalid"}
        model_id = str(profile.get("strategy_model_id") or "active").strip() or "active"
        simulated_cash = round(max(0.0, safe_float(profile.get("simulated_cash"), 0)), 2)
        started_at = str(profile.get("follow_started_at") or created_at or datetime.now().isoformat(timespec="seconds")).strip()
        if len(started_at) == 10:
            started_at = f"{started_at}T00:00:00"
        start_date = str(profile.get("follow_start_date") or started_at[:10]).strip()[:10]
        now_text = datetime.now().isoformat(timespec="seconds")
        reason_text = str(reason or "profile_sync").strip()[:80]
        source_text = str(source or "user_profile").strip()[:80]
        period_id = self._digest("user_follow_period", clean_username, model_id, simulated_cash, started_at)[:40]
        raw_payload = {
            "username": clean_username,
            "profile": profile,
            "previous_profile": previous_profile if isinstance(previous_profile, dict) else {},
            "reason": reason_text,
            "source": source_text,
        }
        try:
            conn = self._connect_db()
            try:
                existing = conn.execute(
                    "SELECT period_id FROM user_follow_periods WHERE period_id = ? LIMIT 1",
                    (period_id,),
                ).fetchone()
                if not existing:
                    conn.execute(
                        """
                        UPDATE user_follow_periods
                        SET ended_at = ?, end_date = ?
                        WHERE username = ? AND (ended_at IS NULL OR ended_at = '') AND period_id <> ?
                        """,
                        (now_text, now_text[:10], clean_username, period_id),
                    )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO user_follow_periods
                    (period_id, username, model_id, simulated_cash, started_at, start_date,
                     ended_at, end_date, reason, source, created_at, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT ended_at FROM user_follow_periods WHERE period_id = ?), ''),
                            COALESCE((SELECT end_date FROM user_follow_periods WHERE period_id = ?), ''), ?, ?, ?, ?)
                    """,
                    (
                        period_id,
                        clean_username,
                        model_id,
                        simulated_cash,
                        started_at,
                        start_date,
                        period_id,
                        period_id,
                        reason_text,
                        source_text,
                        now_text,
                        self._json_text(raw_payload),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            return {"status": "error"}
        return {
            "status": "ok",
            "period_id": period_id,
            "username": clean_username,
            "model_id": model_id,
            "simulated_cash": simulated_cash,
            "started_at": started_at,
            "start_date": start_date,
            "reason": reason_text,
            "source": source_text,
        }

    def user_follow_diagnostics(
        self,
        username: str,
        profile: Optional[Dict[str, Any]] = None,
        position_limit: int = 8,
        trade_limit: int = 8,
        period_limit: int = 6,
    ) -> Dict[str, Any]:
        if not self._db_exists():
            return {"status": "missing", **EMPTY_FOLLOW_DIAGNOSTICS}
        clean_username = str(username or "").strip()
        if not clean_username:
            return {"status": "invalid", **EMPTY_FOLLOW_DIAGNOSTICS}
        profile = profile if isinstance(profile, dict) else {}
        model_id = str(profile.get("strategy_model_id") or "").strip()
        follow_start_date = str(profile.get("follow_start_date") or "").strip()[:10]
        simulated_cash = safe_float(profile.get("simulated_cash"), 0)
        try:
            conn = self._connect_db()
            try:
                period_rows = conn.execute(
                    """
                    SELECT period_id, username, model_id, simulated_cash, started_at, start_date,
                           ended_at, end_date, reason, source, created_at
                    FROM user_follow_periods
                    WHERE username = ?
                    ORDER BY started_at DESC, created_at DESC
                    LIMIT ?
                    """,
                    (clean_username, max(1, min(int(period_limit or 6), 20))),
                ).fetchall()
                current_period = conn.execute(
                    """
                    SELECT period_id, username, model_id, simulated_cash, started_at, start_date,
                           ended_at, end_date, reason, source, created_at
                    FROM user_follow_periods
                    WHERE username = ? AND (ended_at IS NULL OR ended_at = '')
                    ORDER BY started_at DESC, created_at DESC
                    LIMIT 1
                    """,
                    (clean_username,),
                ).fetchone()
                where = ["username = ?"]
                values: list[Any] = [clean_username]
                if model_id:
                    where.append("model_id = ?")
                    values.append(model_id)
                if follow_start_date:
                    where.append("follow_start_date = ?")
                    values.append(follow_start_date)
                if simulated_cash > 0:
                    where.append("initial_cash >= ? AND initial_cash <= ?")
                    values.extend([simulated_cash - 0.01, simulated_cash + 0.01])
                where_sql = " AND ".join(where)
                snapshot = conn.execute(
                    f"""
                    SELECT snapshot_id, username, model_id, model_version, follow_start_date, as_of,
                           initial_cash, record_limit, source, generated_at, total_asset,
                           return_pct, position_count, deal_count
                    FROM user_follow_snapshots
                    WHERE {where_sql}
                    ORDER BY as_of DESC, generated_at DESC
                    LIMIT 1
                    """,
                    values,
                ).fetchone()
                positions = []
                trades = []
                if snapshot:
                    snapshot_id = str(snapshot["snapshot_id"] or "")
                    positions = conn.execute(
                        """
                        SELECT code, name, qty, available_qty, entry_date, entry_price, last_price,
                               market_value, pnl_pct, source, generated_at
                        FROM user_follow_positions
                        WHERE snapshot_id = ?
                        ORDER BY market_value DESC, code ASC
                        LIMIT ?
                        """,
                        (snapshot_id, max(1, min(int(position_limit or 8), 50))),
                    ).fetchall()
                    trades = conn.execute(
                        """
                        SELECT date, time, side, code, name, qty, price, amount, pnl_pct, source, generated_at
                        FROM user_follow_trades
                        WHERE snapshot_id = ?
                        ORDER BY date DESC, time DESC, trade_id DESC
                        LIMIT ?
                        """,
                        (snapshot_id, max(1, min(int(trade_limit or 8), 50))),
                    ).fetchall()
            finally:
                conn.close()
        except Exception as exc:
            return {"status": "error", "error": str(exc), **EMPTY_FOLLOW_DIAGNOSTICS}

        return {
            "status": "ok",
            "current_period": self._row_dict(current_period),
            "account_snapshot": self._row_dict(snapshot),
            "positions": [self._row_dict(row) for row in positions],
            "recent_trades": [self._row_dict(row) for row in trades],
            "periods": [self._row_dict(row) for row in period_rows],
        }

    def _account_trade_rows(self, account_payload: Dict[str, Any]) -> list[Dict[str, Any]]:
        trade_rows: list[Dict[str, Any]] = []
        seen_trades: set[str] = set()
        for key in ("history_deals", "today_deals", "delivery_records", "trade_records", "trades"):
            values = account_payload.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, dict):
                    continue
                marker = self._digest(
                    str(item.get("date") or item.get("trade_date") or ""),
                    str(item.get("time") or item.get("trade_time") or ""),
                    str(item.get("side") or ""),
                    str(item.get("code") or ""),
                    safe_float(item.get("qty"), 0),
                    safe_float(item.get("price"), 0),
                    safe_float(item.get("amount"), 0),
                )
                if marker in seen_trades:
                    continue
                seen_trades.add(marker)
                trade_rows.append(dict(item))
        return trade_rows

    @staticmethod
    def _row_dict(row: Any) -> Dict[str, Any]:
        if not row:
            return {}
        return {key: row[key] for key in row.keys()}
