from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from app.quant.engine_utils import safe_float


MODEL_RECORD_KEYS = ("trade_records", "delivery_records", "daily_settlements", "equity_curve", "days")
MODEL_RECORD_TYPE_KEYS = {
    "trade": "trade_records",
    "delivery": "delivery_records",
    "settlement": "daily_settlements",
}


class StrategyEvolutionRepository:
    def __init__(
        self,
        *,
        db_exists: Callable[[], bool],
        connect_db: Callable[[], sqlite3.Connection],
        json_text: Callable[[Any], str],
        digest: Callable[..., str],
        compact_result: Callable[[Dict[str, Any]], Dict[str, Any]],
        strip_model_records: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        self._db_exists = db_exists
        self._connect_db = connect_db
        self._json_text = json_text
        self._digest = digest
        self._compact_result = compact_result
        self._strip_model_records = strip_model_records

    def persist_result(self, result: Dict[str, Any]) -> None:
        if not isinstance(result, dict):
            return
        run_id = self._run_id(result)
        result["run_id"] = run_id
        compact_result = self._compact_result(result)
        compact_result["run_id"] = run_id
        best_model = result.get("best_model") if isinstance(result.get("best_model"), dict) else {}
        best = result.get("best") if isinstance(result.get("best"), dict) else {}
        best_source = best_model or best
        finished_at = str(result.get("finished_at") or result.get("updated_at") or "")
        conn = self._connect_db()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO strategy_runs
                    (run_id, status, started_at, finished_at, duration_ms, generations, population_size,
                     start_date, end_date, applied, objective, return_pct, max_drawdown_pct, win_rate,
                     closed_trades, best_params_json, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(result.get("status") or ""),
                        str(result.get("started_at") or ""),
                        finished_at,
                        safe_float(result.get("duration_ms"), 0),
                        int(safe_float(result.get("generations"), 0)),
                        int(safe_float(result.get("population_size"), 0)),
                        str(result.get("start_date") or ""),
                        str(result.get("end_date") or ""),
                        1 if result.get("applied") else 0,
                        safe_float(best_source.get("objective"), safe_float(best.get("objective"), 0)),
                        safe_float(best_source.get("return_pct"), safe_float(best.get("return_pct"), 0)),
                        safe_float(best_source.get("max_drawdown_pct"), safe_float(best.get("max_drawdown_pct"), 0)),
                        safe_float(best_source.get("win_rate"), safe_float(best.get("win_rate"), 0)),
                        int(safe_float(best_source.get("closed_trades"), safe_float(best.get("closed_trades"), 0))),
                        self._json_text(best_source.get("params") or best.get("params") or {}),
                        self._json_text(compact_result),
                    ),
                )
                for item in result.get("history", []) if isinstance(result.get("history"), list) else []:
                    if not isinstance(item, dict):
                        continue
                    metric_id = self._digest("strategy_metric", run_id, item.get("generation"), item)[:32]
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO strategy_model_metrics
                        (metric_id, run_id, generation, best_objective, best_return_pct,
                         best_drawdown_pct, best_win_rate, population, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            metric_id,
                            run_id,
                            int(safe_float(item.get("generation"), 0)),
                            safe_float(item.get("best_objective"), 0),
                            safe_float(item.get("best_return_pct"), 0),
                            safe_float(item.get("best_drawdown_pct"), 0),
                            safe_float(item.get("best_win_rate"), 0),
                            int(safe_float(item.get("population"), 0)),
                            self._json_text(item),
                        ),
                    )
                for item in result.get("candidate_records", []) if isinstance(result.get("candidate_records"), list) else []:
                    if not isinstance(item, dict):
                        continue
                    params = item.get("params") if isinstance(item.get("params"), dict) else {}
                    params_hash = str(item.get("params_hash") or self._digest("params", params)[:16])
                    candidate_id = str(
                        item.get("candidate_id")
                        or self._digest("strategy_candidate", run_id, item.get("generation"), item.get("rank"), params_hash)[:32]
                    )
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO strategy_candidates
                        (candidate_id, run_id, generation, rank, selected, selection_role,
                         elimination_reason, objective, return_pct, max_drawdown_pct,
                         sharpe_ratio, profit_factor, win_rate, closed_trades,
                         params_hash, params_json, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            candidate_id,
                            run_id,
                            int(safe_float(item.get("generation"), 0)),
                            int(safe_float(item.get("rank"), 0)),
                            1 if item.get("selected") else 0,
                            str(item.get("selection_role") or ""),
                            str(item.get("elimination_reason") or ""),
                            safe_float(item.get("objective"), 0),
                            safe_float(item.get("return_pct"), 0),
                            safe_float(item.get("max_drawdown_pct"), 0),
                            safe_float(item.get("sharpe_ratio"), 0),
                            safe_float(item.get("profit_factor"), 0),
                            safe_float(item.get("win_rate"), 0),
                            int(safe_float(item.get("closed_trades"), 0)),
                            params_hash,
                            self._json_text(params),
                            self._json_text(item),
                        ),
                    )
                models = result.get("models") if isinstance(result.get("models"), list) else []
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    model_id = str(model.get("id") or self._digest("strategy_model", run_id, model)[:24])
                    compact_model = self._strip_model_records(model)
                    compact_model["id"] = model_id
                    compact_model["run_id"] = run_id
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO strategy_models
                        (model_id, run_id, generated_at, rank, name, source, reusable, objective, return_pct,
                         max_drawdown_pct, sharpe_ratio, profit_factor, win_rate, closed_trades,
                         params_json, backtest_json, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            model_id,
                            run_id,
                            str(model.get("generated_at") or finished_at),
                            int(safe_float(model.get("rank"), 0)),
                            str(model.get("name") or model_id),
                            str(model.get("source") or ""),
                            1 if model.get("reusable", True) else 0,
                            safe_float(model.get("objective"), 0),
                            safe_float(model.get("return_pct"), 0),
                            safe_float(model.get("max_drawdown_pct"), 0),
                            safe_float(model.get("sharpe_ratio"), 0),
                            safe_float(model.get("profit_factor"), 0),
                            safe_float(model.get("win_rate"), 0),
                            int(safe_float(model.get("closed_trades"), 0)),
                            self._json_text(model.get("params") or {}),
                            self._json_text(model.get("backtest") or {}),
                            self._json_text(compact_model),
                        ),
                    )
                    record_groups = (
                        ("trade", model.get("trade_records")),
                        ("delivery", model.get("delivery_records")),
                        ("settlement", model.get("daily_settlements")),
                    )
                    for record_type, records in record_groups:
                        if not isinstance(records, list):
                            continue
                        for seq, record in enumerate(records, start=1):
                            if not isinstance(record, dict):
                                continue
                            record_id = self._digest("strategy_record", model_id, record_type, seq, record)[:32]
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO strategy_model_records
                                (record_id, model_id, run_id, record_type, seq, date, time, side,
                                 code, name, qty, price, pnl_pct, raw_json)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    record_id,
                                    model_id,
                                    run_id,
                                    record_type,
                                    seq,
                                    str(record.get("date") or ""),
                                    str(record.get("time") or ""),
                                    str(record.get("side") or record.get("direction") or ""),
                                    str(record.get("code") or ""),
                                    str(record.get("name") or ""),
                                    safe_float(record.get("qty"), 0),
                                    safe_float(record.get("price"), 0),
                                    safe_float(record.get("pnl_pct"), safe_float(record.get("return_pct"), 0)),
                                    self._json_text(record),
                                ),
                            )
        finally:
            conn.close()

    def load_model_record_counts(self, model_ids: List[str]) -> Dict[str, Dict[str, int]]:
        clean_ids = [str(item).strip() for item in model_ids if str(item or "").strip()]
        if not clean_ids or not self._db_exists():
            return {}
        placeholders = ",".join("?" for _ in clean_ids)
        try:
            conn = self._connect_db()
            try:
                rows = conn.execute(
                    f"""
                    SELECT model_id, record_type, COUNT(*) AS count
                    FROM strategy_model_records
                    WHERE model_id IN ({placeholders})
                    GROUP BY model_id, record_type
                    """,
                    clean_ids,
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return {}
        counts: Dict[str, Dict[str, int]] = {}
        for row in rows:
            model_id = str(row["model_id"] or "")
            key = MODEL_RECORD_TYPE_KEYS.get(str(row["record_type"] or ""), str(row["record_type"] or ""))
            if not model_id or not key:
                continue
            counts.setdefault(model_id, {})[key] = int(row["count"] or 0)
        return counts

    def load_model_records(self, model_id: str) -> Dict[str, Any]:
        model_id = str(model_id or "").strip()
        if not model_id or not self._db_exists():
            return {}
        groups: Dict[str, List[Dict[str, Any]]] = {key: [] for key in MODEL_RECORD_TYPE_KEYS.values()}
        try:
            conn = self._connect_db()
            try:
                rows = conn.execute(
                    """
                    SELECT record_type, raw_json
                    FROM strategy_model_records
                    WHERE model_id = ?
                    ORDER BY record_type, seq ASC
                    """,
                    (model_id,),
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return {}
        for row in rows:
            key = MODEL_RECORD_TYPE_KEYS.get(str(row["record_type"] or ""))
            if not key:
                continue
            try:
                record = json.loads(str(row["raw_json"] or "{}"))
            except Exception:
                continue
            if isinstance(record, dict):
                groups.setdefault(key, []).append(record)
        payload = {key: value for key, value in groups.items() if value}
        if payload:
            payload["record_counts"] = {key: len(value) for key, value in payload.items() if isinstance(value, list)}
        return payload

    def load_persisted_models(self, limit: int = 80, include_records: bool = False) -> List[Dict[str, Any]]:
        if not self._db_exists():
            return []
        try:
            conn = self._connect_db()
            try:
                rows = conn.execute(
                    """
                    SELECT model_id, run_id, generated_at, rank, name, source, reusable,
                           objective, return_pct, max_drawdown_pct, sharpe_ratio, profit_factor,
                           win_rate, closed_trades, params_json, backtest_json
                    FROM strategy_models
                    ORDER BY generated_at DESC, rank ASC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit or 80), 500)),),
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return []
        items: List[Dict[str, Any]] = []
        counts_by_model = self.load_model_record_counts([str(row["model_id"] or "") for row in rows])
        for row in rows:
            item = self.persisted_model_from_row(
                row,
                include_records=include_records,
                record_counts=counts_by_model.get(str(row["model_id"] or ""), {}),
            )
            if item:
                items.append(item)
        return items

    def persisted_model_from_row(
        self,
        row: sqlite3.Row,
        include_records: bool = False,
        record_counts: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        item: Dict[str, Any] = {}
        try:
            params = json.loads(str(row["params_json"] or "{}"))
        except Exception:
            params = {}
        try:
            backtest = json.loads(str(row["backtest_json"] or "{}"))
        except Exception:
            backtest = {}
        item.update(
            {
                "id": str(row["model_id"] or ""),
                "run_id": str(row["run_id"] or ""),
                "generated_at": str(row["generated_at"] or ""),
                "rank": int(row["rank"] or 0),
                "name": str(row["name"] or ""),
                "source": str(row["source"] or "sqlite"),
                "reusable": bool(row["reusable"]),
                "objective": safe_float(row["objective"], 0),
                "return_pct": safe_float(row["return_pct"], 0),
                "max_drawdown_pct": safe_float(row["max_drawdown_pct"], 0),
                "sharpe_ratio": safe_float(row["sharpe_ratio"], 0),
                "profit_factor": safe_float(row["profit_factor"], 0),
                "win_rate": safe_float(row["win_rate"], 0),
                "closed_trades": int(safe_float(row["closed_trades"], 0)),
                "params": params if isinstance(params, dict) else {},
                "backtest": backtest if isinstance(backtest, dict) else {},
            }
        )
        model_id = str(item.get("id") or "")
        if not model_id:
            return {}
        counts = record_counts if isinstance(record_counts, dict) else self.load_model_record_counts([model_id]).get(model_id, {})
        if counts:
            item["record_counts"] = counts
        if include_records:
            item.update(self.load_model_records(model_id))
        else:
            item = self._strip_model_records(item)
        return item

    def load_persisted_model(self, model_id: str, include_records: bool = False) -> Dict[str, Any]:
        model_id = str(model_id or "").strip()
        if not model_id or not self._db_exists():
            return {}
        try:
            conn = self._connect_db()
            try:
                row = conn.execute(
                    """
                    SELECT model_id, run_id, generated_at, rank, name, source, reusable,
                           objective, return_pct, max_drawdown_pct, sharpe_ratio, profit_factor,
                           win_rate, closed_trades, params_json, backtest_json
                    FROM strategy_models
                    WHERE model_id = ?
                    LIMIT 1
                    """,
                    (model_id,),
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            return {}
        if not row:
            return {}
        return self.persisted_model_from_row(row, include_records=include_records)

    def _run_id(self, result: Dict[str, Any]) -> str:
        existing = str(result.get("run_id") or "").strip()
        if existing:
            return existing
        return self._digest(
            "strategy_run",
            result.get("started_at"),
            result.get("finished_at") or result.get("updated_at"),
            result.get("start_date"),
            result.get("end_date"),
            result.get("mode"),
            result.get("best_model") or result.get("best") or {},
        )[:24]
