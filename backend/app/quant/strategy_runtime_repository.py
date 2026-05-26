from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.quant.engine_utils import safe_float


class StrategyRuntimeRepository:
    def __init__(
        self,
        *,
        db_exists: Callable[[], bool],
        connect_db: Callable[[], sqlite3.Connection],
        json_text: Callable[[Any], str],
        digest: Callable[..., str],
        runtime_model_version: Callable[[Dict[str, Any]], str],
        runtime_date_filter: Callable[..., tuple[str, list[Any]]],
        daily_runtime_source_filter: Callable[[], tuple[str, list[str]]],
        runtime_snapshot_payload: Callable[..., Optional[Dict[str, Any]]],
        scale_runtime_trades: Callable[[List[Dict[str, Any]], float, float], List[Dict[str, Any]]],
        runtime_cache_key: Callable[..., tuple[str, str]],
        runtime_cache_is_fresh: Callable[[str], bool],
        quant_engine: Any,
    ) -> None:
        self._db_exists = db_exists
        self._connect_db = connect_db
        self._json_text = json_text
        self._digest = digest
        self._runtime_model_version = runtime_model_version
        self._runtime_date_filter_fn = runtime_date_filter
        self._daily_runtime_source_filter_fn = daily_runtime_source_filter
        self._runtime_snapshot_payload_fn = runtime_snapshot_payload
        self._scale_runtime_trades_fn = scale_runtime_trades
        self._runtime_cache_key_fn = runtime_cache_key
        self._runtime_cache_is_fresh_fn = runtime_cache_is_fresh
        self._quant_engine = quant_engine

    def runtime_model_version(self, model: Dict[str, Any]) -> str:
        return self._runtime_model_version(model)

    def _runtime_date_filter(
        self,
        conn: sqlite3.Connection,
        table: str,
        date_column: str,
        model_id: str,
        model_version: str,
        start_date: Optional[str],
        as_of: Optional[str],
        params_hash: str = "",
    ) -> tuple[str, list[Any]]:
        return self._runtime_date_filter_fn(
            conn,
            table,
            date_column,
            model_id,
            model_version,
            start_date,
            as_of,
            params_hash=params_hash,
        )

    def _daily_runtime_source_filter(self) -> tuple[str, list[str]]:
        return self._daily_runtime_source_filter_fn()

    def _runtime_snapshot_payload(
        self,
        snapshot_row: sqlite3.Row,
        model_id: str,
        selected_version: str,
        selected_start_date: Optional[str],
        requested_start_date: Optional[str],
        as_of: Optional[str],
        target_cash: float,
        generated_at: str,
        scope: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        return self._runtime_snapshot_payload_fn(
            snapshot_row,
            model_id,
            selected_version,
            selected_start_date,
            requested_start_date,
            as_of,
            target_cash,
            generated_at,
            scope,
        )

    def _scale_runtime_trades(self, trades: List[Dict[str, Any]], base_cash: float, target_cash: float) -> List[Dict[str, Any]]:
        return self._scale_runtime_trades_fn(trades, base_cash, target_cash)

    def _runtime_cache_key(
        self,
        model_id: str,
        params: Dict[str, Any],
        initial_cash: Any,
        start_date: Optional[str],
        as_of: Optional[str],
        limit: int,
        model_version: str = "",
    ) -> tuple[str, str]:
        return self._runtime_cache_key_fn(
            model_id,
            params,
            initial_cash,
            start_date,
            as_of,
            limit,
            model_version=model_version,
        )

    def _runtime_cache_is_fresh(self, generated_at: str) -> bool:
        return self._runtime_cache_is_fresh_fn(generated_at)

    def _runtime_rows_exist(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        model_version: str,
        start_date: Optional[str],
        as_of: Optional[str],
        params_hash: str = "",
    ) -> bool:
        checks = (
            ("strategy_runtime_trades", "date"),
            ("strategy_daily_signals", "date"),
            ("strategy_runtime_positions", "as_of"),
            ("strategy_runtime_settlements", "date"),
        )
        for table, date_column in checks:
            where_sql, params = self._runtime_date_filter(conn, table, date_column, model_id, model_version, start_date, as_of, params_hash=params_hash)
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {where_sql}", params).fetchone()
            if int((row["count"] if isinstance(row, sqlite3.Row) else row[0]) or 0) > 0:
                return True
        return False

    def _latest_runtime_scope(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        model_version: str,
        start_date: Optional[str],
        as_of: Optional[str],
        params_hash: str = "",
    ) -> Optional[Dict[str, str]]:
        where_sql, params = self._runtime_date_filter(
            conn,
            "strategy_runtime_snapshots",
            "as_of",
            model_id,
            model_version,
            start_date,
            as_of,
            params_hash=params_hash,
        )
        source_sql, source_params = self._daily_runtime_source_filter()
        row = conn.execute(
            f"""
            SELECT generated_at
            FROM strategy_runtime_snapshots
            WHERE {where_sql} AND {source_sql}
            ORDER BY generated_at DESC, as_of DESC
            LIMIT 1
            """,
            [*params, *source_params],
        ).fetchone()
        if not row:
            return None
        generated_at = str(row["generated_at"] if isinstance(row, sqlite3.Row) else row[0] or "").strip()
        if not generated_at:
            return None
        return {
            "model_version": str(model_version or ""),
            "start_date": str(start_date or ""),
            "params_hash": str(params_hash or ""),
            "generated_at": generated_at,
        }

    def _select_runtime_scope(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        model_version: str,
        start_date: Optional[str],
        as_of: Optional[str],
        params_hash: str = "",
    ) -> Optional[Dict[str, str]]:
        versions = []
        for value in (str(model_version or "").strip(), ""):
            if value not in versions:
                versions.append(value)
        hashes = []
        for value in (str(params_hash or "").strip(), ""):
            if value not in hashes:
                hashes.append(value)
        starts: List[Optional[str]] = []
        clean_start = str(start_date or "").strip() or None
        for value in (clean_start, None):
            if value not in starts:
                starts.append(value)

        for candidate_start in starts:
            for candidate_version in versions:
                for candidate_hash in hashes:
                    scope = self._latest_runtime_scope(
                        conn,
                        model_id,
                        candidate_version,
                        candidate_start,
                        as_of,
                        params_hash=candidate_hash,
                    )
                    if scope:
                        scope["requested_start_date"] = str(start_date or "")
                        scope["fallback_latest_snapshot"] = bool(candidate_start != clean_start)
                        scope["relaxed_model_version"] = bool(candidate_version != str(model_version or "").strip())
                        scope["relaxed_params_hash"] = bool(candidate_hash != str(params_hash or "").strip())
                        return scope
        return None

    def save_daily_runtime(
        self,
        model: Dict[str, Any],
        params: Dict[str, Any],
        timeline: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        source: str = "strategy_replay",
    ) -> Dict[str, Any]:
        if not isinstance(model, dict) or not isinstance(timeline, dict):
            return {"status": "skipped", "reason": "invalid_runtime_payload"}
        model_id = str(model.get("id") or model.get("model_id") or "active").strip() or "active"
        model_version = self.runtime_model_version(model)
        params_hash = self._digest("strategy_params", params or {})[:24]
        start_date = str(timeline.get("start_date") or start_date or "").strip()
        end_date = str(timeline.get("end_date") or end_date or "").strip()
        mode = str(timeline.get("mode") or mode or "").strip()
        generated_at = datetime.now().isoformat(timespec="seconds")
        initial_cash = safe_float(timeline.get("initial_cash"), safe_float((params or {}).get("account_initial_cash"), 0))
        snapshot_source = f"daily_runtime:{mode or 'unknown'}"
        days = timeline.get("days") if isinstance(timeline.get("days"), list) else []
        trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
        if not days and not trades:
            return {"status": "skipped", "model_id": model_id, "reason": "empty_runtime_payload"}

        signal_count = 0
        position_count = 0
        trade_count = 0
        settlement_count = 0
        snapshot_count = 0
        conn = self._connect_db()
        try:
            conn.execute(
                """
                DELETE FROM strategy_daily_signals
                WHERE model_id = ? AND source = ? AND date >= ? AND date <= ?
                """,
                (model_id, source, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_trades
                WHERE model_id = ? AND source = ? AND date >= ? AND date <= ?
                """,
                (model_id, source, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_positions
                WHERE model_id = ? AND source = ? AND as_of >= ? AND as_of <= ?
                """,
                (model_id, source, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_settlements
                WHERE model_id = ? AND source = ? AND date >= ? AND date <= ?
                """,
                (model_id, source, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_snapshots
                WHERE model_id = ? AND source = ? AND as_of >= ? AND as_of <= ?
                """,
                (model_id, snapshot_source, start_date, end_date),
            )
            equity_curve = timeline.get("equity_curve") if isinstance(timeline.get("equity_curve"), list) else []
            equity_by_date = {
                str(point.get("date") or ""): point
                for point in equity_curve
                if isinstance(point, dict)
            }
            trades_by_date: Dict[str, List[Dict[str, Any]]] = {}
            for trade in trades:
                if isinstance(trade, dict):
                    trades_by_date.setdefault(str(trade.get("date") or ""), []).append(trade)
            cumulative_deal_count = 0
            for day in days:
                if not isinstance(day, dict):
                    continue
                day_date = str(day.get("date") or "").strip()
                if not day_date:
                    continue
                cumulative_deal_count += len(trades_by_date.get(day_date, []))
                for seq, signal in enumerate(day.get("signals") if isinstance(day.get("signals"), list) else [], start=1):
                    if not isinstance(signal, dict):
                        continue
                    signal_id = self._digest("strategy_daily_signal", model_id, params_hash, day_date, seq, signal)[:32]
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO strategy_daily_signals
                        (signal_id, model_id, model_version, params_hash, start_date, date, execute_on, mode,
                         code, name, action, buy_score, sell_score, reason, source, generated_at, initial_cash, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            signal_id,
                            model_id,
                            model_version,
                            params_hash,
                            start_date,
                            day_date,
                            str(signal.get("execute_on") or ""),
                            mode,
                            str(signal.get("code") or ""),
                            str(signal.get("name") or ""),
                            str(signal.get("action") or "买入候选"),
                            safe_float(signal.get("buy_score"), 0),
                            safe_float(signal.get("sell_score"), 0),
                            str(signal.get("reason") or ""),
                            source,
                            generated_at,
                            initial_cash,
                            self._json_text(signal),
                        ),
                    )
                    signal_count += 1
                for seq, pos in enumerate(day.get("positions") if isinstance(day.get("positions"), list) else [], start=1):
                    if not isinstance(pos, dict):
                        continue
                    position_id = self._digest("strategy_runtime_position", model_id, params_hash, day_date, seq, pos)[:32]
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO strategy_runtime_positions
                        (position_id, model_id, model_version, params_hash, start_date, as_of, mode,
                         code, name, qty, entry_date, entry_price, last_price, market_value, pnl_pct,
                         source, generated_at, initial_cash, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            position_id,
                            model_id,
                            model_version,
                            params_hash,
                            start_date,
                            day_date,
                            mode,
                            str(pos.get("code") or ""),
                            str(pos.get("name") or ""),
                            safe_float(pos.get("qty"), 0),
                            str(pos.get("entry_date") or ""),
                            safe_float(pos.get("entry_price"), 0),
                            safe_float(pos.get("last_price"), 0),
                            safe_float(pos.get("market_value"), 0),
                            safe_float(pos.get("pnl_pct"), 0),
                            source,
                            generated_at,
                            initial_cash,
                            self._json_text(pos),
                        ),
                    )
                    position_count += 1
                day_positions = day.get("positions") if isinstance(day.get("positions"), list) else []
                day_trades = trades_by_date.get(day_date, [])
                equity_point = equity_by_date.get(day_date, {})
                total_asset = safe_float(day.get("total_value"), safe_float(equity_point.get("total_value"), initial_cash))
                cash = safe_float(day.get("cash"), 0)
                market_value = safe_float(day.get("market_value"), max(0.0, total_asset - cash))
                account_summary = {
                    "initial_cash": round(initial_cash, 2),
                    "total_asset": round(total_asset, 2),
                    "cash": round(cash, 2),
                    "available_cash": round(max(0.0, cash), 2),
                    "market_value": round(market_value, 2),
                    "total_pnl": round(total_asset - initial_cash, 2),
                    "return_pct": round(safe_float(equity_point.get("return_pct"), ((total_asset / initial_cash - 1) * 100 if initial_cash > 0 else 0)), 3),
                    "position_count": len([pos for pos in day_positions if isinstance(pos, dict)]),
                    "deal_count": cumulative_deal_count,
                }
                snapshot_payload = {
                    "status": "ok",
                    "as_of": day_date,
                    "start_date": start_date,
                    "strategy_account_source": "daily_runtime_snapshot",
                    "mode": mode,
                    "account": account_summary,
                    "positions": [pos for pos in day_positions if isinstance(pos, dict)],
                    "today_deals": [trade for trade in day_trades if isinstance(trade, dict)],
                    "portfolio": {
                        "cash": round(cash, 2),
                        "total_value": round(total_asset, 2),
                        "strategy_params": params or {},
                    },
                }
                snapshot_key = self._digest("strategy_daily_snapshot", model_id, params_hash, start_date, day_date, mode)[:32]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO strategy_runtime_snapshots
                    (cache_key, model_id, model_version, params_hash, start_date, as_of, initial_cash,
                     record_limit, source, generated_at, total_asset, return_pct, position_count,
                     deal_count, account_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_key,
                        model_id,
                        model_version,
                        params_hash,
                        start_date,
                        day_date,
                        initial_cash,
                        0,
                        snapshot_source,
                        generated_at,
                        account_summary["total_asset"],
                        account_summary["return_pct"],
                        account_summary["position_count"],
                        account_summary["deal_count"],
                        self._json_text(snapshot_payload),
                    ),
                )
                snapshot_count += 1
            for seq, trade in enumerate(trades, start=1):
                if not isinstance(trade, dict):
                    continue
                trade_date = str(trade.get("date") or "").strip()
                if not trade_date:
                    continue
                qty = safe_float(trade.get("qty"), 0)
                price = safe_float(trade.get("price"), 0)
                trade_id = self._digest("strategy_runtime_trade", model_id, params_hash, trade_date, seq, trade)[:32]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO strategy_runtime_trades
                    (trade_id, model_id, model_version, params_hash, start_date, date, time, mode,
                     side, code, name, qty, price, amount, score, pnl_pct, reason, source,
                     generated_at, initial_cash, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade_id,
                        model_id,
                        model_version,
                        params_hash,
                        start_date,
                        trade_date,
                        str(trade.get("time") or ""),
                        str(trade.get("mode") or mode),
                        str(trade.get("side") or "").upper(),
                        str(trade.get("code") or ""),
                        str(trade.get("name") or ""),
                        qty,
                        price,
                        safe_float(trade.get("amount"), qty * price),
                        safe_float(trade.get("score"), 0) if trade.get("score") is not None else None,
                        safe_float(trade.get("pnl_pct"), 0) if trade.get("pnl_pct") is not None else None,
                        str(trade.get("reason") or ""),
                        source,
                        generated_at,
                        initial_cash,
                        self._json_text(trade),
                    ),
                )
                trade_count += 1
            settlement_account = self._quant_engine.account_from_trades(
                trades,
                initial_cash=initial_cash,
                as_of=end_date,
                start_date=None,
                limit=0,
            )
            settlements = settlement_account.get("daily_settlements") if isinstance(settlement_account.get("daily_settlements"), list) else []
            for settlement in settlements:
                if not isinstance(settlement, dict):
                    continue
                settlement_date = str(settlement.get("date") or "").strip()
                if not settlement_date or (start_date and settlement_date < start_date) or (end_date and settlement_date > end_date):
                    continue
                settlement_id = self._digest("strategy_runtime_settlement", model_id, params_hash, settlement_date, mode, settlement)[:32]
                conn.execute(
                    """
                    INSERT OR REPLACE INTO strategy_runtime_settlements
                    (settlement_id, model_id, model_version, params_hash, start_date, date, mode,
                     buy_amount, sell_amount, commission, stamp_duty, transfer_fee, total_fee,
                     net_amount, realized_pnl, deal_count, source, generated_at, initial_cash, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        settlement_id,
                        model_id,
                        model_version,
                        params_hash,
                        start_date,
                        settlement_date,
                        mode,
                        safe_float(settlement.get("buy_amount"), 0),
                        safe_float(settlement.get("sell_amount"), 0),
                        safe_float(settlement.get("commission"), 0),
                        safe_float(settlement.get("stamp_duty"), 0),
                        safe_float(settlement.get("transfer_fee"), 0),
                        safe_float(settlement.get("total_fee"), 0),
                        safe_float(settlement.get("net_amount"), 0),
                        safe_float(settlement.get("realized_pnl"), 0),
                        int(safe_float(settlement.get("deal_count"), 0)),
                        source,
                        generated_at,
                        initial_cash,
                        self._json_text(settlement),
                    ),
                )
                settlement_count += 1
            conn.commit()
        finally:
            conn.close()
        return {
            "status": "ok",
            "model_id": model_id,
            "model_version": model_version,
            "params_hash": params_hash,
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode,
            "signal_count": signal_count,
            "trade_count": trade_count,
            "position_count": position_count,
            "settlement_count": settlement_count,
            "snapshot_count": snapshot_count,
            "generated_at": generated_at,
        }

    def load_runtime_account(
        self,
        model_id: str,
        initial_cash: Any,
        start_date: Optional[str],
        as_of: Optional[str],
        limit: int,
        model_version: str = "",
        params: Optional[Dict[str, Any]] = None,
        hydrate_trades: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not self._db_exists():
            return None
        model_id = str(model_id or "active").strip() or "active"
        as_of = str(as_of or self._quant_engine.latest_event_date() or "").strip()
        start_date = str(start_date or "").strip() or None
        target_cash = max(1.0, safe_float(initial_cash, 0))
        limit = max(1, min(int(limit or 500), 5000))
        params_hash = self._digest("strategy_params", params or {})[:24] if isinstance(params, dict) else ""
        try:
            conn = self._connect_db()
            try:
                scope = self._select_runtime_scope(
                    conn,
                    model_id,
                    str(model_version or "").strip(),
                    start_date,
                    as_of,
                    params_hash=params_hash,
                )
                if not scope:
                    return None
                selected_version = scope.get("model_version", "")
                selected_start_date = scope.get("start_date") or None
                params_hash = scope.get("params_hash", "")
                generated_at = scope.get("generated_at", "")
                if not hydrate_trades:
                    snapshot_where, snapshot_params = self._runtime_date_filter(
                        conn,
                        "strategy_runtime_snapshots",
                        "as_of",
                        model_id,
                        selected_version,
                        selected_start_date,
                        as_of,
                        params_hash=params_hash,
                    )
                    source_sql, source_params = self._daily_runtime_source_filter()
                    snapshot_where = f"{snapshot_where} AND {source_sql} AND generated_at = ?"
                    snapshot_params.extend([*source_params, generated_at])
                    snapshot_row = conn.execute(
                        f"""
                        SELECT as_of, start_date, source, generated_at, initial_cash, total_asset,
                               return_pct, position_count, deal_count, account_json
                        FROM strategy_runtime_snapshots
                        WHERE {snapshot_where}
                        ORDER BY as_of DESC
                        LIMIT 1
                        """,
                        snapshot_params,
                    ).fetchone()
                    if snapshot_row:
                        snapshot_payload = self._runtime_snapshot_payload(
                            snapshot_row,
                            model_id,
                            selected_version,
                            selected_start_date,
                            start_date,
                            as_of,
                            target_cash,
                            generated_at,
                            scope,
                        )
                        if snapshot_payload:
                            return snapshot_payload
                where_sql, sql_params = self._runtime_date_filter(
                    conn,
                    "strategy_runtime_trades",
                    "date",
                    model_id,
                    selected_version,
                    selected_start_date,
                    as_of,
                    params_hash=params_hash,
                )
                where_sql = f"{where_sql} AND generated_at = ?"
                sql_params.append(generated_at)
                rows = conn.execute(
                    f"""
                    SELECT date, time, side, code, name, qty, price, amount, score, pnl_pct,
                           reason, mode, initial_cash, raw_json
                    FROM strategy_runtime_trades
                    WHERE {where_sql}
                    ORDER BY date ASC, time ASC, trade_id ASC
                    """,
                    sql_params,
                ).fetchall()
                signal_where, signal_params = self._runtime_date_filter(
                    conn,
                    "strategy_daily_signals",
                    "date",
                    model_id,
                    selected_version,
                    selected_start_date,
                    as_of,
                    params_hash=params_hash,
                )
                signal_where = f"{signal_where} AND generated_at = ?"
                signal_params.append(generated_at)
                signal_count = conn.execute(
                    f"SELECT COUNT(*) AS count FROM strategy_daily_signals WHERE {signal_where}",
                    signal_params,
                ).fetchone()
                position_where, position_params = self._runtime_date_filter(
                    conn,
                    "strategy_runtime_positions",
                    "as_of",
                    model_id,
                    selected_version,
                    selected_start_date,
                    as_of,
                    params_hash=params_hash,
                )
                position_where = f"{position_where} AND generated_at = ?"
                position_params.append(generated_at)
                position_count = conn.execute(
                    f"SELECT COUNT(*) AS count FROM strategy_runtime_positions WHERE {position_where}",
                    position_params,
                ).fetchone()
                settlement_where, settlement_params = self._runtime_date_filter(
                    conn,
                    "strategy_runtime_settlements",
                    "date",
                    model_id,
                    selected_version,
                    selected_start_date,
                    as_of,
                    params_hash=params_hash,
                )
                settlement_where = f"{settlement_where} AND generated_at = ?"
                settlement_params.append(generated_at)
                settlement_count = conn.execute(
                    f"SELECT COUNT(*) AS count FROM strategy_runtime_settlements WHERE {settlement_where}",
                    settlement_params,
                ).fetchone()
                snapshot_where, snapshot_params = self._runtime_date_filter(
                    conn,
                    "strategy_runtime_snapshots",
                    "as_of",
                    model_id,
                    selected_version,
                    selected_start_date,
                    as_of,
                    params_hash=params_hash,
                )
                source_sql, source_params = self._daily_runtime_source_filter()
                snapshot_where = f"{snapshot_where} AND {source_sql} AND generated_at = ?"
                snapshot_params.extend([*source_params, generated_at])
                snapshot_row = conn.execute(
                    f"""
                    SELECT as_of, start_date, source, generated_at, initial_cash, total_asset,
                           return_pct, position_count, deal_count, account_json
                    FROM strategy_runtime_snapshots
                    WHERE {snapshot_where}
                    ORDER BY as_of DESC
                    LIMIT 1
                    """,
                    snapshot_params,
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            return None

        trades: List[Dict[str, Any]] = []
        base_cash = 0.0
        for row in rows:
            if base_cash <= 0:
                base_cash = safe_float(row["initial_cash"], 0)
            try:
                trade = json.loads(str(row["raw_json"] or "{}"))
            except Exception:
                trade = {}
            if not isinstance(trade, dict) or not trade:
                trade = {
                    "date": str(row["date"] or ""),
                    "time": str(row["time"] or ""),
                    "side": str(row["side"] or ""),
                    "code": str(row["code"] or ""),
                    "name": str(row["name"] or ""),
                    "qty": safe_float(row["qty"], 0),
                    "price": safe_float(row["price"], 0),
                    "amount": safe_float(row["amount"], 0),
                    "score": safe_float(row["score"], 0),
                    "pnl_pct": safe_float(row["pnl_pct"], 0),
                    "reason": str(row["reason"] or ""),
                    "mode": str(row["mode"] or ""),
                }
            trades.append(trade)
        base_cash = base_cash or (safe_float(snapshot_row["initial_cash"], 0) if snapshot_row else 0) or target_cash

        account: Dict[str, Any] = {}
        if snapshot_row:
            try:
                loaded = json.loads(str(snapshot_row["account_json"] or "{}"))
                account = loaded if isinstance(loaded, dict) else {}
            except Exception:
                account = {}
        if account:
            account = dict(account)
            scaled_trades = self._scale_runtime_trades(trades, base_cash, target_cash)
            deal_account = self._quant_engine.account_from_trades(
                scaled_trades,
                initial_cash=target_cash,
                as_of=as_of,
                start_date=selected_start_date,
                limit=limit,
                drop_unmatched_sells=True,
            )
            account.setdefault("status", "ok")
            account.setdefault("as_of", str(snapshot_row["as_of"] or as_of) if snapshot_row else as_of)
            account.setdefault("start_date", str(snapshot_row["start_date"] or selected_start_date or "") if snapshot_row else str(selected_start_date or ""))
            positions = []
            for pos in account.get("positions", []) if isinstance(account.get("positions"), list) else []:
                if not isinstance(pos, dict):
                    continue
                item = dict(pos)
                qty = safe_float(item.get("qty"), 0)
                entry_price = safe_float(item.get("entry_price"), safe_float(item.get("cost_price"), 0))
                last_price = safe_float(item.get("last_price"), entry_price)
                market_value = safe_float(item.get("market_value"), qty * last_price)
                cost_amount = safe_float(item.get("cost_amount"), qty * entry_price)
                item.setdefault("available_qty", item.get("qty", 0))
                item.setdefault("cost_price", round(cost_amount / qty, 3) if qty > 0 and cost_amount > 0 else round(entry_price, 3))
                item.setdefault("cost_amount", round(cost_amount, 2))
                item.setdefault("market_value", round(market_value, 2))
                item.setdefault("pnl_amount", round(market_value - cost_amount, 2))
                if "pnl_pct" not in item:
                    item["pnl_pct"] = round((market_value - cost_amount) / cost_amount * 100, 3) if cost_amount > 0 else 0.0
                positions.append(item)
            account["positions"] = positions
            account["history_deals"] = deal_account.get("history_deals", [])
            account["delivery_records"] = deal_account.get("delivery_records", [])
            account["daily_settlements"] = deal_account.get("daily_settlements", [])
            snapshot_as_of = str(account.get("as_of") or as_of or "")
            today_deals = deal_account.get("today_deals", [])
            account["today_deals"] = today_deals if isinstance(today_deals, list) else [trade for trade in trades if str(trade.get("date") or "") == snapshot_as_of][:limit]
        else:
            scaled_trades = self._scale_runtime_trades(trades, base_cash, target_cash)
            account = self._quant_engine.account_from_trades(
                scaled_trades,
                initial_cash=target_cash,
                as_of=as_of,
                start_date=selected_start_date,
                limit=limit,
                drop_unmatched_sells=True,
            )
        signal_total = int((signal_count["count"] if isinstance(signal_count, sqlite3.Row) else signal_count[0]) or 0)
        position_total = int((position_count["count"] if isinstance(position_count, sqlite3.Row) else position_count[0]) or 0)
        settlement_total = int((settlement_count["count"] if isinstance(settlement_count, sqlite3.Row) else settlement_count[0]) or 0)
        account["strategy_account_source"] = "runtime_snapshot" if snapshot_row else "runtime_tables"
        account["strategy_account_cache"] = "runtime"
        account["follow_start_date"] = start_date or ""
        account["runtime_data_start_date"] = selected_start_date or ""
        account["runtime_model_id"] = model_id
        account["runtime_model_version"] = selected_version
        account["runtime_trade_count"] = len(trades)
        account["runtime_scaled_trade_count"] = len(account.get("history_deals", []))
        account["runtime_signal_count"] = signal_total
        account["runtime_position_count"] = position_total
        account["runtime_settlement_count"] = settlement_total
        account["runtime_scaled_from_cash"] = round(base_cash, 2)
        account["runtime_scaled_to_cash"] = round(target_cash, 2)
        account["runtime_generated_at"] = generated_at
        account["runtime_fallback_latest_snapshot"] = bool(scope.get("fallback_latest_snapshot"))
        account["runtime_relaxed_params_hash"] = bool(scope.get("relaxed_params_hash"))
        account["runtime_relaxed_model_version"] = bool(scope.get("relaxed_model_version"))
        if snapshot_row:
            account["runtime_snapshot_as_of"] = str(snapshot_row["as_of"] or "")
            account["runtime_snapshot_source"] = str(snapshot_row["source"] or "")
            account["runtime_snapshot_total_asset"] = round(safe_float(snapshot_row["total_asset"], 0), 2)
            account["runtime_snapshot_return_pct"] = round(safe_float(snapshot_row["return_pct"], 0), 3)
        return account

    def _runtime_summary_for_model(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        model_id = str(model_id or "").strip()
        if not model_id:
            return None
        params_hash = self._digest("strategy_params", params or {})[:24] if isinstance(params, dict) else ""
        source_sql, source_params = self._daily_runtime_source_filter()
        where = ["model_id = ?", source_sql]
        values: list[Any] = [model_id, *source_params]
        if params_hash:
            where.append("params_hash = ?")
            values.append(params_hash)
        where_sql = " AND ".join(where)
        latest = conn.execute(
            f"""
            SELECT generated_at
            FROM strategy_runtime_snapshots
            WHERE {where_sql}
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            values,
        ).fetchone()
        if not latest and params_hash:
            params_hash = ""
            source_sql, source_params = self._daily_runtime_source_filter()
            where = ["model_id = ?", source_sql]
            values = [model_id, *source_params]
            where_sql = " AND ".join(where)
            latest = conn.execute(
                f"""
                SELECT generated_at
                FROM strategy_runtime_snapshots
                WHERE {where_sql}
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                values,
            ).fetchone()
        if not latest:
            return None
        generated_at = str(latest["generated_at"] or "")
        rows = conn.execute(
            f"""
            SELECT as_of, start_date, source, initial_cash, total_asset, return_pct, position_count, deal_count
            FROM strategy_runtime_snapshots
            WHERE {where_sql} AND generated_at = ?
            ORDER BY as_of ASC
            """,
            [*values, generated_at],
        ).fetchall()
        if not rows:
            return None
        latest_row = rows[-1]
        peak = 0.0
        max_drawdown_pct = 0.0
        for row in rows:
            value = safe_float(row["total_asset"], 0)
            if value <= 0:
                continue
            peak = max(peak, value)
            if peak > 0:
                drawdown = (value / peak - 1) * 100
                max_drawdown_pct = min(max_drawdown_pct, drawdown)
        trade_row = conn.execute(
            f"""
            SELECT
              COUNT(*) AS trade_count,
              SUM(CASE WHEN UPPER(side) = 'SELL' THEN 1 ELSE 0 END) AS closed_trades,
              SUM(CASE WHEN UPPER(side) = 'SELL' AND pnl_pct > 0 THEN 1 ELSE 0 END) AS winning_trades
            FROM strategy_runtime_trades
            WHERE model_id = ? AND generated_at = ? {("AND params_hash = ?" if params_hash else "")}
            """,
            ([model_id, generated_at, params_hash] if params_hash else [model_id, generated_at]),
        ).fetchone()
        signal_row = conn.execute(
            f"""
            SELECT COUNT(*) AS signal_count
            FROM strategy_daily_signals
            WHERE model_id = ? AND generated_at = ? {("AND params_hash = ?" if params_hash else "")}
            """,
            ([model_id, generated_at, params_hash] if params_hash else [model_id, generated_at]),
        ).fetchone()
        trade_count = int((trade_row["trade_count"] if isinstance(trade_row, sqlite3.Row) else trade_row[0]) or 0)
        closed_trades = int((trade_row["closed_trades"] if isinstance(trade_row, sqlite3.Row) else trade_row[1]) or 0)
        winning_trades = int((trade_row["winning_trades"] if isinstance(trade_row, sqlite3.Row) else trade_row[2]) or 0)
        signal_count = int((signal_row["signal_count"] if isinstance(signal_row, sqlite3.Row) else signal_row[0]) or 0)
        win_rate = round(winning_trades / closed_trades * 100, 3) if closed_trades > 0 else 0.0
        return_pct = round(safe_float(latest_row["return_pct"], 0), 3)
        objective = round(return_pct - abs(max_drawdown_pct) * 0.8 + win_rate * 0.03 + min(closed_trades, 60) * 0.02, 4)
        return {
            "runtime_data_status": "ok",
            "has_runtime_data": True,
            "runtime_generated_at": generated_at,
            "runtime_start_date": str(latest_row["start_date"] or ""),
            "runtime_end_date": str(latest_row["as_of"] or ""),
            "runtime_source": str(latest_row["source"] or ""),
            "runtime_day_count": len(rows),
            "signal_count": signal_count,
            "trade_count": trade_count,
            "objective": objective,
            "return_pct": return_pct,
            "max_drawdown_pct": round(max_drawdown_pct, 3),
            "win_rate": win_rate,
            "closed_trades": closed_trades,
            "final_value": round(safe_float(latest_row["total_asset"], 0), 2),
            "initial_cash": round(safe_float(latest_row["initial_cash"], 0), 2),
            "position_count": int(safe_float(latest_row["position_count"], 0)),
            "deal_count": int(safe_float(latest_row["deal_count"], 0)),
        }

    def runtime_model_summaries(self, models: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        if not models or not self._db_exists():
            return {}
        summaries: Dict[str, Dict[str, Any]] = {}
        try:
            conn = self._connect_db()
            try:
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    model_id = str(model.get("id") or model.get("model_id") or "").strip()
                    if not model_id:
                        continue
                    params = model.get("params") if isinstance(model.get("params"), dict) else None
                    summary = self._runtime_summary_for_model(conn, model_id, params=params)
                    if summary:
                        summaries[model_id] = summary
            finally:
                conn.close()
        except Exception:
            return {}
        return summaries

    def model_signal_feed(
        self,
        as_of: Optional[str] = None,
        limit_models: int = 20,
        limit_per_model: int = 12,
        fallback_latest: bool = True,
    ) -> Dict[str, Any]:
        limit_models = max(1, min(int(safe_float(limit_models, 20)), 80))
        limit_per_model = max(1, min(int(safe_float(limit_per_model, 12)), 80))
        requested_as_of = str(as_of or "").strip()[:10]
        if not self._db_exists():
            return {
                "status": "ok",
                "as_of": requested_as_of,
                "data_date": "",
                "items": [],
                "total": 0,
                "model_count": 0,
                "message": "策略信号表还没有生成数据",
            }

        conn = self._connect_db()
        try:
            latest_row = conn.execute(
                """
                SELECT date
                FROM strategy_daily_signals
                WHERE date > ''
                ORDER BY date DESC
                LIMIT 1
                """
            ).fetchone()
            latest_date = str((latest_row["date"] if isinstance(latest_row, sqlite3.Row) and latest_row else latest_row[0] if latest_row else "") or "")
            if not latest_date:
                return {
                    "status": "ok",
                    "as_of": requested_as_of,
                    "data_date": "",
                    "items": [],
                    "total": 0,
                    "model_count": 0,
                    "message": "策略信号表还没有生成数据",
                }

            data_date = latest_date
            if requested_as_of:
                row = conn.execute(
                    """
                    SELECT date
                    FROM strategy_daily_signals
                    WHERE date <= ?
                    ORDER BY date DESC
                    LIMIT 1
                    """,
                    (requested_as_of,),
                ).fetchone()
                candidate_date = str((row["date"] if isinstance(row, sqlite3.Row) and row else row[0] if row else "") or "")
                if candidate_date:
                    data_date = candidate_date
                elif not fallback_latest:
                    return {
                        "status": "ok",
                        "as_of": requested_as_of,
                        "data_date": "",
                        "items": [],
                        "total": 0,
                        "model_count": 0,
                        "message": f"{requested_as_of} 没有模型信号",
                    }

            model_rows = conn.execute(
                """
                WITH latest AS (
                    SELECT model_id, MAX(generated_at) AS generated_at
                    FROM strategy_daily_signals
                    WHERE date = ?
                    GROUP BY model_id
                )
                SELECT
                  s.model_id,
                  s.model_version,
                  s.params_hash,
                  s.generated_at,
                  MIN(s.start_date) AS start_date,
                  MAX(s.initial_cash) AS initial_cash,
                  COUNT(*) AS signal_count,
                  COALESCE(m.name, '') AS model_name,
                  COALESCE(m.source, '') AS model_source,
                  m.run_id,
                  m.rank,
                  m.objective,
                  m.return_pct,
                  m.max_drawdown_pct,
                  m.win_rate,
                  m.closed_trades
                FROM strategy_daily_signals s
                JOIN latest l ON l.model_id = s.model_id AND l.generated_at = s.generated_at
                LEFT JOIN strategy_models m ON m.model_id = s.model_id
                WHERE s.date = ?
                GROUP BY s.model_id, s.model_version, s.params_hash, s.generated_at
                ORDER BY
                  CASE WHEN m.rank IS NULL THEN 999999 ELSE m.rank END ASC,
                  COALESCE(m.objective, 0) DESC,
                  signal_count DESC,
                  s.model_id ASC
                LIMIT ?
                """,
                (data_date, data_date, limit_models),
            ).fetchall()

            items: List[Dict[str, Any]] = []
            total = 0
            for model_row in model_rows:
                model_id = str(model_row["model_id"] or "")
                generated_at = str(model_row["generated_at"] or "")
                signal_rows = conn.execute(
                    """
                    SELECT signal_id, date, execute_on, mode, code, name, action, buy_score,
                           sell_score, reason, source, generated_at, initial_cash, raw_json
                    FROM strategy_daily_signals
                    WHERE date = ? AND model_id = ? AND generated_at = ?
                    ORDER BY buy_score DESC, sell_score ASC, code ASC
                    LIMIT ?
                    """,
                    (data_date, model_id, generated_at, limit_per_model),
                ).fetchall()
                signals: List[Dict[str, Any]] = []
                for row in signal_rows:
                    raw: Dict[str, Any] = {}
                    try:
                        loaded = json.loads(str(row["raw_json"] or "{}"))
                        raw = loaded if isinstance(loaded, dict) else {}
                    except Exception:
                        raw = {}
                    signal = {
                        "signal_id": str(row["signal_id"] or ""),
                        "model_id": model_id,
                        "date": str(row["date"] or ""),
                        "execute_on": str(row["execute_on"] or raw.get("execute_on") or ""),
                        "mode": str(row["mode"] or ""),
                        "code": str(row["code"] or raw.get("code") or ""),
                        "name": str(row["name"] or raw.get("name") or ""),
                        "action": str(row["action"] or raw.get("action") or "买入候选"),
                        "buy_score": round(safe_float(row["buy_score"], raw.get("buy_score") or 0), 2),
                        "sell_score": round(safe_float(row["sell_score"], raw.get("sell_score") or 0), 2),
                        "reason": str(row["reason"] or raw.get("reason") or ""),
                        "source": str(row["source"] or ""),
                        "generated_at": str(row["generated_at"] or ""),
                        "initial_cash": safe_float(row["initial_cash"], 0),
                    }
                    signals.append(signal)
                total += int(model_row["signal_count"] or 0)
                items.append(
                    {
                        "model_id": model_id,
                        "model_name": str(model_row["model_name"] or model_id),
                        "model_version": str(model_row["model_version"] or ""),
                        "params_hash": str(model_row["params_hash"] or ""),
                        "run_id": str(model_row["run_id"] or ""),
                        "rank": model_row["rank"],
                        "source": str(model_row["model_source"] or ""),
                        "start_date": str(model_row["start_date"] or ""),
                        "data_date": data_date,
                        "generated_at": generated_at,
                        "initial_cash": safe_float(model_row["initial_cash"], 0),
                        "objective": safe_float(model_row["objective"], 0),
                        "return_pct": safe_float(model_row["return_pct"], 0),
                        "max_drawdown_pct": safe_float(model_row["max_drawdown_pct"], 0),
                        "win_rate": safe_float(model_row["win_rate"], 0),
                        "closed_trades": int(model_row["closed_trades"] or 0),
                        "signal_count": int(model_row["signal_count"] or 0),
                        "signals": signals,
                    }
                )

            return {
                "status": "ok",
                "as_of": requested_as_of or data_date,
                "data_date": data_date,
                "latest_date": latest_date,
                "items": items,
                "total": total,
                "model_count": len(items),
                "limit_per_model": limit_per_model,
                "fallback_latest": bool(requested_as_of and data_date != requested_as_of),
            }
        finally:
            conn.close()

    def load_account_cache(
        self,
        model_id: str,
        params: Dict[str, Any],
        initial_cash: Any,
        start_date: Optional[str],
        as_of: Optional[str],
        limit: int,
        model_version: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not self._db_exists():
            return None
        cache_key, _params_hash = self._runtime_cache_key(
            model_id,
            params,
            initial_cash,
            start_date,
            as_of,
            limit,
            model_version=model_version,
        )
        try:
            conn = self._connect_db()
            try:
                row = conn.execute(
                    """
                    SELECT generated_at, account_json
                    FROM strategy_runtime_snapshots
                    WHERE cache_key = ?
                    LIMIT 1
                    """,
                    (cache_key,),
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            return None
        if not row or not self._runtime_cache_is_fresh(str(row["generated_at"] or "")):
            return None
        try:
            payload = json.loads(str(row["account_json"] or "{}"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        payload["strategy_account_cache"] = "hit"
        payload["strategy_account_cache_key"] = cache_key
        payload["strategy_account_cache_generated_at"] = str(row["generated_at"] or "")
        return payload

    def save_account_cache(
        self,
        model_id: str,
        params: Dict[str, Any],
        initial_cash: Any,
        start_date: Optional[str],
        as_of: Optional[str],
        limit: int,
        account: Dict[str, Any],
        model_version: str = "",
        source: str = "",
    ) -> None:
        if not isinstance(account, dict):
            return
        cache_key, params_hash = self._runtime_cache_key(
            model_id,
            params,
            initial_cash,
            start_date,
            as_of,
            limit,
            model_version=model_version,
        )
        account_payload = dict(account)
        account_payload.pop("strategy_account_cache", None)
        account_payload.pop("strategy_account_cache_key", None)
        generated_at = datetime.now().isoformat(timespec="seconds")
        summary = account_payload.get("account") if isinstance(account_payload.get("account"), dict) else {}
        try:
            conn = self._connect_db()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO strategy_runtime_snapshots
                    (cache_key, model_id, model_version, params_hash, start_date, as_of, initial_cash,
                     record_limit, source, generated_at, total_asset, return_pct, position_count,
                     deal_count, account_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cache_key,
                        str(model_id or "active"),
                        str(model_version or ""),
                        params_hash,
                        str(start_date or ""),
                        str(as_of or ""),
                        safe_float(initial_cash, 0),
                        int(limit or 0),
                        str(source or account_payload.get("strategy_account_source") or ""),
                        generated_at,
                        safe_float(summary.get("total_asset"), 0),
                        safe_float(summary.get("return_pct"), 0),
                        int(safe_float(summary.get("position_count"), 0)),
                        int(safe_float(summary.get("deal_count"), 0)),
                        self._json_text(account_payload),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            return
