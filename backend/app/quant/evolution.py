from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.quant.engine import DATA_DIR, QUANT_DB_FILE, quant_engine, read_json, safe_float, write_json


EVOLUTION_STATE_FILE = DATA_DIR / "strategy_evolution_state.json"
EVOLUTION_PAUSE_FILE = DATA_DIR / "strategy_evolution_pause.json"
EVOLUTION_STATE_MAX_INLINE_BYTES = 8 * 1024 * 1024
MODEL_RECORD_KEYS = ("trade_records", "delivery_records", "daily_settlements", "equity_curve", "days")
MODEL_RECORD_TYPE_KEYS = {
    "trade": "trade_records",
    "delivery": "delivery_records",
    "settlement": "daily_settlements",
}


def _env_int(name: str, default: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
    try:
        value = int(float(os.getenv(name, "") or default))
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


GENES: Dict[str, tuple[float, float]] = {
    "buy_threshold": (55, 90),
    "watch_threshold": (45, 80),
    "avoid_sell_threshold": (55, 92),
    "avoid_buy_ceiling": (45, 85),
    "sell_score_threshold": (55, 92),
    "stop_loss_pct": (-12, -2),
    "take_profit_pct": (3, 20),
    "max_hold_days": (1, 10),
    "max_positions": (2, 10),
    "top_n": (3, 20),
    "sentiment_weight": (0.10, 0.55),
    "event_weight": (0.10, 0.55),
    "technical_weight": (0.10, 0.55),
    "risk_weight": (0.05, 0.40),
    "sentiment_coef": (20, 90),
    "ai_score_coef": (1, 10),
    "event_impact_weight": (0.35, 0.85),
    "history_score_weight": (0.15, 0.65),
    "history_return_coef": (150, 700),
    "history_win_coef": (10, 100),
    "sell_negative_sentiment_coef": (5, 55),
    "sell_technical_risk_coef": (0.15, 1.25),
    "negative_sentiment_risk_penalty": (5, 35),
    "risk_event_penalty": (8, 45),
    "factor_score_coef": (0.08, 0.65),
    "factor_momentum_weight": (0.05, 0.60),
    "factor_volume_weight": (0.05, 0.45),
    "factor_breakout_weight": (0.05, 0.45),
    "factor_lhb_weight": (0.05, 0.55),
}


class StrategyEvolution:
    def __init__(self) -> None:
        self.state_file = EVOLUTION_STATE_FILE
        self._lock = threading.Lock()

    def _json_text(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def _digest(self, *parts: Any) -> str:
        text = "|".join(self._json_text(part) for part in parts)
        return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE IF NOT EXISTS strategy_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT,
                started_at TEXT,
                finished_at TEXT,
                duration_ms REAL,
                generations INTEGER,
                population_size INTEGER,
                start_date TEXT,
                end_date TEXT,
                applied INTEGER,
                objective REAL,
                return_pct REAL,
                max_drawdown_pct REAL,
                win_rate REAL,
                closed_trades INTEGER,
                best_params_json TEXT,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_runs_finished ON strategy_runs(finished_at);

            CREATE TABLE IF NOT EXISTS strategy_model_metrics (
                metric_id TEXT PRIMARY KEY,
                run_id TEXT,
                generation INTEGER,
                best_objective REAL,
                best_return_pct REAL,
                best_drawdown_pct REAL,
                best_win_rate REAL,
                population INTEGER,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_metrics_run ON strategy_model_metrics(run_id);

            CREATE TABLE IF NOT EXISTS strategy_candidates (
                candidate_id TEXT PRIMARY KEY,
                run_id TEXT,
                generation INTEGER,
                rank INTEGER,
                selected INTEGER,
                selection_role TEXT,
                elimination_reason TEXT,
                objective REAL,
                return_pct REAL,
                max_drawdown_pct REAL,
                sharpe_ratio REAL,
                profit_factor REAL,
                win_rate REAL,
                closed_trades INTEGER,
                params_hash TEXT,
                params_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_candidates_run_generation ON strategy_candidates(run_id, generation, rank);
            CREATE INDEX IF NOT EXISTS idx_strategy_candidates_selected ON strategy_candidates(run_id, selected);

            CREATE TABLE IF NOT EXISTS strategy_models (
                model_id TEXT PRIMARY KEY,
                run_id TEXT,
                generated_at TEXT,
                rank INTEGER,
                name TEXT,
                source TEXT,
                reusable INTEGER,
                objective REAL,
                return_pct REAL,
                max_drawdown_pct REAL,
                sharpe_ratio REAL,
                profit_factor REAL,
                win_rate REAL,
                closed_trades INTEGER,
                params_json TEXT NOT NULL DEFAULT '{}',
                backtest_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_models_run ON strategy_models(run_id, rank);
            CREATE INDEX IF NOT EXISTS idx_strategy_models_generated ON strategy_models(generated_at);

            CREATE TABLE IF NOT EXISTS strategy_model_records (
                record_id TEXT PRIMARY KEY,
                model_id TEXT,
                run_id TEXT,
                record_type TEXT,
                seq INTEGER,
                date TEXT,
                time TEXT,
                side TEXT,
                code TEXT,
                name TEXT,
                qty REAL,
                price REAL,
                pnl_pct REAL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_records_model ON strategy_model_records(model_id, record_type);
            CREATE INDEX IF NOT EXISTS idx_strategy_records_code_date ON strategy_model_records(code, date);

            CREATE TABLE IF NOT EXISTS strategy_runtime_snapshots (
                cache_key TEXT PRIMARY KEY,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                start_date TEXT,
                as_of TEXT,
                initial_cash REAL,
                record_limit INTEGER,
                source TEXT,
                generated_at TEXT,
                total_asset REAL,
                return_pct REAL,
                position_count INTEGER,
                deal_count INTEGER,
                account_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_model_date ON strategy_runtime_snapshots(model_id, as_of, start_date);
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_generated ON strategy_runtime_snapshots(generated_at);

            CREATE TABLE IF NOT EXISTS user_follow_periods (
                period_id TEXT PRIMARY KEY,
                username TEXT,
                model_id TEXT,
                simulated_cash REAL,
                started_at TEXT,
                start_date TEXT,
                ended_at TEXT,
                end_date TEXT,
                reason TEXT,
                source TEXT,
                created_at TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_user_follow_periods_user_started ON user_follow_periods(username, started_at);
            CREATE INDEX IF NOT EXISTS idx_user_follow_periods_active ON user_follow_periods(username, ended_at);

            CREATE TABLE IF NOT EXISTS user_follow_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                username TEXT,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                follow_start_date TEXT,
                as_of TEXT,
                initial_cash REAL,
                record_limit INTEGER,
                source TEXT,
                generated_at TEXT,
                total_asset REAL,
                return_pct REAL,
                position_count INTEGER,
                deal_count INTEGER,
                account_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_user_follow_snapshots_user_model ON user_follow_snapshots(username, model_id, follow_start_date, as_of);
            CREATE INDEX IF NOT EXISTS idx_user_follow_snapshots_generated ON user_follow_snapshots(generated_at);

            CREATE TABLE IF NOT EXISTS user_follow_positions (
                position_id TEXT PRIMARY KEY,
                snapshot_id TEXT,
                username TEXT,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                follow_start_date TEXT,
                as_of TEXT,
                code TEXT,
                name TEXT,
                qty REAL,
                available_qty REAL,
                entry_date TEXT,
                entry_price REAL,
                last_price REAL,
                market_value REAL,
                pnl_pct REAL,
                source TEXT,
                generated_at TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_user_follow_positions_user_date ON user_follow_positions(username, as_of);
            CREATE INDEX IF NOT EXISTS idx_user_follow_positions_code_date ON user_follow_positions(code, as_of);

            CREATE TABLE IF NOT EXISTS user_follow_trades (
                trade_id TEXT PRIMARY KEY,
                snapshot_id TEXT,
                username TEXT,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                follow_start_date TEXT,
                date TEXT,
                time TEXT,
                side TEXT,
                code TEXT,
                name TEXT,
                qty REAL,
                price REAL,
                amount REAL,
                pnl_pct REAL,
                source TEXT,
                generated_at TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_user_follow_trades_user_date ON user_follow_trades(username, date);
            CREATE INDEX IF NOT EXISTS idx_user_follow_trades_code_date ON user_follow_trades(code, date);

            CREATE TABLE IF NOT EXISTS strategy_daily_signals (
                signal_id TEXT PRIMARY KEY,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                start_date TEXT,
                date TEXT,
                execute_on TEXT,
                mode TEXT,
                code TEXT,
                name TEXT,
                action TEXT,
                buy_score REAL,
                sell_score REAL,
                reason TEXT,
                source TEXT,
                generated_at TEXT,
                initial_cash REAL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_model_date ON strategy_daily_signals(model_id, date);
            CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_code_date ON strategy_daily_signals(code, date);
            CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_generated ON strategy_daily_signals(generated_at);

            CREATE TABLE IF NOT EXISTS strategy_runtime_trades (
                trade_id TEXT PRIMARY KEY,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                start_date TEXT,
                date TEXT,
                time TEXT,
                mode TEXT,
                side TEXT,
                code TEXT,
                name TEXT,
                qty REAL,
                price REAL,
                amount REAL,
                score REAL,
                pnl_pct REAL,
                reason TEXT,
                source TEXT,
                generated_at TEXT,
                initial_cash REAL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_trades_model_date ON strategy_runtime_trades(model_id, date);
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_trades_code_date ON strategy_runtime_trades(code, date);
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_trades_generated ON strategy_runtime_trades(generated_at);

            CREATE TABLE IF NOT EXISTS strategy_runtime_positions (
                position_id TEXT PRIMARY KEY,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                start_date TEXT,
                as_of TEXT,
                mode TEXT,
                code TEXT,
                name TEXT,
                qty REAL,
                entry_date TEXT,
                entry_price REAL,
                last_price REAL,
                market_value REAL,
                pnl_pct REAL,
                source TEXT,
                generated_at TEXT,
                initial_cash REAL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_positions_model_date ON strategy_runtime_positions(model_id, as_of);
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_positions_code_date ON strategy_runtime_positions(code, as_of);
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_positions_generated ON strategy_runtime_positions(generated_at);

            CREATE TABLE IF NOT EXISTS strategy_runtime_settlements (
                settlement_id TEXT PRIMARY KEY,
                model_id TEXT,
                model_version TEXT,
                params_hash TEXT,
                start_date TEXT,
                date TEXT,
                mode TEXT,
                buy_amount REAL,
                sell_amount REAL,
                commission REAL,
                stamp_duty REAL,
                transfer_fee REAL,
                total_fee REAL,
                net_amount REAL,
                realized_pnl REAL,
                deal_count INTEGER,
                source TEXT,
                generated_at TEXT,
                initial_cash REAL,
                raw_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_settlements_model_date ON strategy_runtime_settlements(model_id, date);
            CREATE INDEX IF NOT EXISTS idx_strategy_runtime_settlements_generated ON strategy_runtime_settlements(generated_at);
            """
        )

    def _connect_db(self) -> sqlite3.Connection:
        QUANT_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(QUANT_DB_FILE)
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        return conn

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

    def _record_counts(self, model: Dict[str, Any]) -> Dict[str, int]:
        counts = dict(model.get("record_counts")) if isinstance(model.get("record_counts"), dict) else {}
        for key in MODEL_RECORD_KEYS:
            values = model.get(key)
            if isinstance(values, list):
                counts[key] = max(int(safe_float(counts.get(key), 0)), len(values))
        clean_counts: Dict[str, int] = {}
        for key, value in counts.items():
            count = int(safe_float(value, 0))
            if count > 0:
                clean_counts[str(key)] = count
        return clean_counts

    def _strip_model_records(self, model: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(model)
        record_counts = self._record_counts(item)
        for key in MODEL_RECORD_KEYS:
            item.pop(key, None)
        if record_counts:
            item["record_counts"] = record_counts
        backtest = item.get("backtest") if isinstance(item.get("backtest"), dict) else {}
        if backtest:
            item["backtest"] = dict(backtest)
        return item

    def _compact_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {"status": "idle"}
        payload = dict(result)
        if isinstance(payload.get("best"), dict):
            payload["best"] = self._strip_model_records(payload["best"])
        if isinstance(payload.get("best_model"), dict):
            payload["best_model"] = self._strip_model_records(payload["best_model"])
        if isinstance(payload.get("models"), list):
            payload["models"] = [
                self._strip_model_records(item) for item in payload["models"] if isinstance(item, dict)
            ]
        candidate_records = payload.pop("candidate_records", None)
        if isinstance(candidate_records, list):
            payload["evaluated_candidate_count"] = max(
                int(safe_float(payload.get("evaluated_candidate_count"), 0)),
                len(candidate_records),
            )
        return payload

    def _result_has_inline_records(self, result: Dict[str, Any]) -> bool:
        if not isinstance(result, dict):
            return False
        candidates: List[Dict[str, Any]] = []
        for key in ("best", "best_model"):
            value = result.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        models = result.get("models") if isinstance(result.get("models"), list) else []
        candidates.extend(item for item in models if isinstance(item, dict))
        return any(
            isinstance(item.get(key), list) and bool(item.get(key))
            for item in candidates
            for key in MODEL_RECORD_KEYS
        )

    def _write_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        compact = self._compact_result(payload)
        write_json(self.state_file, compact)
        return compact

    def _archive_oversized_state_file(self) -> Optional[Dict[str, Any]]:
        try:
            if not self.state_file.exists() or self.state_file.stat().st_size <= EVOLUTION_STATE_MAX_INLINE_BYTES:
                return None
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            archive = self.state_file.with_name(f"{self.state_file.stem}.archived-{stamp}{self.state_file.suffix}")
            self.state_file.replace(archive)
            payload = {
                "status": "idle",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "progress_message": f"历史进化状态文件过大，已归档为 {archive.name}",
                "archived_state_file": archive.name,
                "models": self._load_persisted_models(limit=16, include_records=False),
            }
            self._write_state(payload)
            return payload
        except Exception:
            return None

    def _persist_result(self, result: Dict[str, Any]) -> None:
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

    def _load_model_record_counts(self, model_ids: List[str]) -> Dict[str, Dict[str, int]]:
        clean_ids = [str(item).strip() for item in model_ids if str(item or "").strip()]
        if not clean_ids or not QUANT_DB_FILE.exists():
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

    def _load_model_records(self, model_id: str) -> Dict[str, Any]:
        model_id = str(model_id or "").strip()
        if not model_id or not QUANT_DB_FILE.exists():
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

    def _load_persisted_models(self, limit: int = 80, include_records: bool = False) -> List[Dict[str, Any]]:
        if not QUANT_DB_FILE.exists():
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
        counts_by_model = self._load_model_record_counts([str(row["model_id"] or "") for row in rows])
        for row in rows:
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
                    "id": str(row["model_id"] or item.get("id") or ""),
                    "run_id": str(row["run_id"] or item.get("run_id") or ""),
                    "generated_at": str(row["generated_at"] or item.get("generated_at") or ""),
                    "rank": int(row["rank"] or 0),
                    "name": str(row["name"] or item.get("name") or ""),
                    "source": str(row["source"] or item.get("source") or "sqlite"),
                    "reusable": bool(row["reusable"]),
                    "objective": safe_float(row["objective"], item.get("objective", 0)),
                    "return_pct": safe_float(row["return_pct"], item.get("return_pct", 0)),
                    "max_drawdown_pct": safe_float(row["max_drawdown_pct"], item.get("max_drawdown_pct", 0)),
                    "sharpe_ratio": safe_float(row["sharpe_ratio"], item.get("sharpe_ratio", 0)),
                    "profit_factor": safe_float(row["profit_factor"], item.get("profit_factor", 0)),
                    "win_rate": safe_float(row["win_rate"], item.get("win_rate", 0)),
                    "closed_trades": int(safe_float(row["closed_trades"], item.get("closed_trades", 0))),
                    "params": params if isinstance(params, dict) else {},
                    "backtest": backtest if isinstance(backtest, dict) else {},
                }
            )
            record_counts = counts_by_model.get(item["id"], {})
            if record_counts:
                item["record_counts"] = record_counts
            if include_records:
                item.update(self._load_model_records(item["id"]))
            else:
                item = self._strip_model_records(item)
            items.append(item)
        return items

    def _runtime_cache_ttl_seconds(self) -> int:
        return _env_int("QT_STRATEGY_ACCOUNT_CACHE_TTL_SECONDS", 1800, minimum=0, maximum=86400)

    def _user_follow_cache_ttl_seconds(self) -> int:
        return _env_int("QT_USER_FOLLOW_ACCOUNT_CACHE_TTL_SECONDS", 86400, minimum=0, maximum=604800)

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
        clean_model_id = str(model_id or "active").strip() or "active"
        params_hash = self._digest("strategy_params", params or {})[:24]
        key = self._digest(
            "strategy_runtime_snapshot",
            clean_model_id,
            str(model_version or ""),
            params_hash,
            round(safe_float(initial_cash, 0), 2),
            str(start_date or ""),
            str(as_of or ""),
            int(limit or 0),
        )[:32]
        return key, params_hash

    def _user_follow_snapshot_key(
        self,
        username: str,
        model_id: str,
        params: Dict[str, Any],
        initial_cash: Any,
        follow_start_date: Optional[str],
        as_of: Optional[str],
        limit: int,
        model_version: str = "",
    ) -> tuple[str, str]:
        clean_username = str(username or "anonymous").strip() or "anonymous"
        clean_model_id = str(model_id or "active").strip() or "active"
        params_hash = self._digest("strategy_params", params or {})[:24]
        key = self._digest(
            "user_follow_snapshot",
            clean_username,
            clean_model_id,
            str(model_version or ""),
            params_hash,
            round(safe_float(initial_cash, 0), 2),
            str(follow_start_date or ""),
            str(as_of or ""),
            int(limit or 0),
        )[:32]
        return key, params_hash

    def runtime_model_version(self, model: Dict[str, Any]) -> str:
        if not isinstance(model, dict):
            return ""
        record_counts = model.get("record_counts") if isinstance(model.get("record_counts"), dict) else {}
        return "|".join(
            [
                str(model.get("id") or model.get("model_id") or ""),
                str(model.get("run_id") or ""),
                str(model.get("generated_at") or ""),
                str(model.get("rank") or ""),
                self._json_text(record_counts),
            ]
        )

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
        where = ["model_id = ?"]
        params: list[Any] = [model_id]
        if model_version:
            where.append("model_version = ?")
            params.append(model_version)
        if params_hash:
            where.append("params_hash = ?")
            params.append(params_hash)
        if start_date:
            where.append(f"{date_column} >= ?")
            params.append(str(start_date))
        if as_of:
            where.append(f"{date_column} <= ?")
            params.append(str(as_of))
        return " AND ".join(where), params

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

    def _scale_runtime_trades(self, trades: List[Dict[str, Any]], base_cash: float, target_cash: float) -> List[Dict[str, Any]]:
        if base_cash <= 0 or target_cash <= 0:
            return [dict(trade) for trade in trades if isinstance(trade, dict)]
        scale = target_cash / base_cash
        if abs(scale - 1.0) < 0.0001:
            return [dict(trade) for trade in trades if isinstance(trade, dict)]
        scaled: List[Dict[str, Any]] = []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            qty = safe_float(trade.get("qty"), 0)
            price = safe_float(trade.get("price"), 0)
            if qty <= 0 or price <= 0:
                continue
            scaled_qty = math.floor(qty * scale / 100) * 100
            if scaled_qty <= 0:
                continue
            item = dict(trade)
            item["qty"] = scaled_qty
            item["amount"] = round(scaled_qty * price, 2)
            item["scaled_from_cash"] = round(base_cash, 2)
            item["scaled_to_cash"] = round(target_cash, 2)
            scaled.append(item)
        return scaled

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
                WHERE model_id = ? AND start_date = ? AND params_hash = ? AND date >= ? AND date <= ?
                """,
                (model_id, start_date, params_hash, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_trades
                WHERE model_id = ? AND start_date = ? AND params_hash = ? AND date >= ? AND date <= ?
                """,
                (model_id, start_date, params_hash, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_positions
                WHERE model_id = ? AND start_date = ? AND params_hash = ? AND as_of >= ? AND as_of <= ?
                """,
                (model_id, start_date, params_hash, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_settlements
                WHERE model_id = ? AND start_date = ? AND params_hash = ? AND date >= ? AND date <= ?
                """,
                (model_id, start_date, params_hash, start_date, end_date),
            )
            conn.execute(
                """
                DELETE FROM strategy_runtime_snapshots
                WHERE model_id = ? AND start_date = ? AND params_hash = ? AND source = ? AND as_of >= ? AND as_of <= ?
                """,
                (model_id, start_date, params_hash, snapshot_source, start_date, end_date),
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
            settlement_account = quant_engine.account_from_trades(
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
    ) -> Optional[Dict[str, Any]]:
        if not QUANT_DB_FILE.exists():
            return None
        model_id = str(model_id or "active").strip() or "active"
        as_of = str(as_of or quant_engine.latest_event_date() or "").strip()
        start_date = str(start_date or "").strip() or None
        target_cash = max(1.0, safe_float(initial_cash, 0))
        limit = max(1, min(int(limit or 500), 5000))
        params_hash = self._digest("strategy_params", params or {})[:24] if isinstance(params, dict) else ""
        try:
            conn = self._connect_db()
            try:
                selected_version = str(model_version or "").strip()
                if selected_version and not self._runtime_rows_exist(conn, model_id, selected_version, start_date, as_of, params_hash=params_hash):
                    selected_version = ""
                if not self._runtime_rows_exist(conn, model_id, selected_version, start_date, as_of, params_hash=params_hash):
                    if params_hash and self._runtime_rows_exist(conn, model_id, selected_version, start_date, as_of, params_hash=""):
                        params_hash = ""
                    else:
                        return None
                where_sql, sql_params = self._runtime_date_filter(
                    conn,
                    "strategy_runtime_trades",
                    "date",
                    model_id,
                    selected_version,
                    start_date,
                    as_of,
                    params_hash=params_hash,
                )
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
                    start_date,
                    as_of,
                    params_hash=params_hash,
                )
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
                    start_date,
                    as_of,
                    params_hash=params_hash,
                )
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
                    start_date,
                    as_of,
                    params_hash=params_hash,
                )
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
                    start_date,
                    as_of,
                    params_hash=params_hash,
                )
                snapshot_where = f"{snapshot_where} AND source LIKE ?"
                snapshot_params.append("daily_runtime%")
                snapshot_row = conn.execute(
                    f"""
                    SELECT as_of, source, total_asset, return_pct, position_count, deal_count, account_json
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
        base_cash = base_cash or target_cash
        scaled_trades = self._scale_runtime_trades(trades, base_cash, target_cash)
        account = quant_engine.account_from_trades(
            scaled_trades,
            initial_cash=target_cash,
            as_of=as_of,
            start_date=start_date,
            limit=limit,
            drop_unmatched_sells=True,
        )
        signal_total = int((signal_count["count"] if isinstance(signal_count, sqlite3.Row) else signal_count[0]) or 0)
        position_total = int((position_count["count"] if isinstance(position_count, sqlite3.Row) else position_count[0]) or 0)
        settlement_total = int((settlement_count["count"] if isinstance(settlement_count, sqlite3.Row) else settlement_count[0]) or 0)
        account["strategy_account_source"] = "runtime_tables"
        account["strategy_account_cache"] = "runtime"
        account["follow_start_date"] = start_date or ""
        account["runtime_model_id"] = model_id
        account["runtime_model_version"] = selected_version
        account["runtime_trade_count"] = len(trades)
        account["runtime_scaled_trade_count"] = len(scaled_trades)
        account["runtime_signal_count"] = signal_total
        account["runtime_position_count"] = position_total
        account["runtime_settlement_count"] = settlement_total
        account["runtime_scaled_from_cash"] = round(base_cash, 2)
        account["runtime_scaled_to_cash"] = round(target_cash, 2)
        if snapshot_row:
            account["runtime_snapshot_as_of"] = str(snapshot_row["as_of"] or "")
            account["runtime_snapshot_source"] = str(snapshot_row["source"] or "")
            account["runtime_snapshot_total_asset"] = round(safe_float(snapshot_row["total_asset"], 0), 2)
            account["runtime_snapshot_return_pct"] = round(safe_float(snapshot_row["return_pct"], 0), 3)
        return account

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
        if not QUANT_DB_FILE.exists():
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
        trade_rows: List[Dict[str, Any]] = []
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
                        WHERE username = ? AND COALESCE(ended_at, '') = '' AND period_id <> ?
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
        if not QUANT_DB_FILE.exists():
            return {"status": "missing", "current_period": {}, "account_snapshot": {}, "positions": [], "recent_trades": [], "periods": []}
        clean_username = str(username or "").strip()
        if not clean_username:
            return {"status": "invalid", "current_period": {}, "account_snapshot": {}, "positions": [], "recent_trades": [], "periods": []}
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
                    WHERE username = ? AND COALESCE(ended_at, '') = ''
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
                    where.append("ABS(initial_cash - ?) < 0.01")
                    values.append(simulated_cash)
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
            return {"status": "error", "error": str(exc), "current_period": {}, "account_snapshot": {}, "positions": [], "recent_trades": [], "periods": []}

        def row_dict(row: Any) -> Dict[str, Any]:
            if not row:
                return {}
            return {key: row[key] for key in row.keys()}

        return {
            "status": "ok",
            "current_period": row_dict(current_period),
            "account_snapshot": row_dict(snapshot),
            "positions": [row_dict(row) for row in positions],
            "recent_trades": [row_dict(row) for row in trades],
            "periods": [row_dict(row) for row in period_rows],
        }

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
        where = ["model_id = ?", "source LIKE ?"]
        values: list[Any] = [model_id, "daily_runtime%"]
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
            where = ["model_id = ?", "source LIKE ?"]
            values = [model_id, "daily_runtime%"]
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
        if not models or not QUANT_DB_FILE.exists():
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

    def _runtime_cache_is_fresh(self, generated_at: str) -> bool:
        ttl = self._runtime_cache_ttl_seconds()
        if ttl <= 0:
            return False
        try:
            generated = datetime.fromisoformat(str(generated_at or ""))
        except Exception:
            return False
        return (datetime.now() - generated).total_seconds() <= ttl

    def _user_follow_snapshot_is_fresh(self, generated_at: str) -> bool:
        ttl = self._user_follow_cache_ttl_seconds()
        if ttl <= 0:
            return False
        try:
            generated = datetime.fromisoformat(str(generated_at or ""))
        except Exception:
            return False
        return (datetime.now() - generated).total_seconds() <= ttl

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
        if not QUANT_DB_FILE.exists():
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

    def status(self) -> Dict[str, Any]:
        archived = self._archive_oversized_state_file()
        if archived:
            return archived
        payload = read_json(self.state_file, {})
        if not isinstance(payload, dict):
            return {"status": "idle"}
        compact = self._compact_result(payload)
        if self._result_has_inline_records(payload):
            try:
                write_json(self.state_file, compact)
            except Exception:
                pass
        return compact

    def _pause_requested(self) -> bool:
        payload = read_json(EVOLUTION_PAUSE_FILE, {})
        return bool(isinstance(payload, dict) and payload.get("paused"))

    def pause(self) -> Dict[str, Any]:
        paused_at = datetime.now().isoformat(timespec="seconds")
        write_json(EVOLUTION_PAUSE_FILE, {"paused": True, "paused_at": paused_at})
        payload = self.status()
        if not isinstance(payload, dict):
            payload = {}
        payload["pause_requested"] = True
        payload["progress_message"] = "已请求暂停，当前代完成后停止"
        if payload.get("status") != "running":
            payload["status"] = "paused"
            payload["progress_message"] = "进化已暂停，可恢复后重新启动"
        payload["updated_at"] = paused_at
        self._write_state(payload)
        return {"status": "ok", "paused": True, "message": payload["progress_message"]}

    def resume(self) -> Dict[str, Any]:
        try:
            EVOLUTION_PAUSE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        payload = self.status()
        if isinstance(payload, dict):
            payload["pause_requested"] = False
            payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if payload.get("status") == "paused":
                payload["status"] = "idle"
                payload["progress_message"] = "已恢复，可重新启动进化"
            self._write_state(payload)
        return {"status": "ok", "paused": False, "message": "已恢复进化控制"}

    def models(self, limit: int = 80, include_records: bool = False) -> Dict[str, Any]:
        payload = self.status()
        state_items = payload.get("models") if isinstance(payload.get("models"), list) else []
        persisted_items = self._load_persisted_models(limit=limit, include_records=include_records)
        items: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*state_items, *persisted_items]:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            if include_records and not any(isinstance(item.get(key), list) for key in MODEL_RECORD_TYPE_KEYS.values()):
                item.update(self._load_model_records(model_id))
            items.append(item if include_records else self._strip_model_records(item))
            if len(items) >= max(1, min(int(limit or 80), 500)):
                break
        active_params = quant_engine.strategy_params()
        return {
            "status": "ok",
            "active": {
                "id": "active",
                "name": "后台基准参数（非跟随策略）",
                "source": "baseline",
                "reusable": False,
                "params": active_params,
            },
            "items": items,
            "count": len(items),
            "updated_at": payload.get("finished_at") or payload.get("updated_at") or "",
        }

    def model(self, model_id: str, include_records: bool = True) -> Dict[str, Any]:
        model_id = str(model_id or "active").strip() or "active"
        if model_id == "active":
            return {
                "id": "active",
                "name": "后台基准参数（非跟随策略）",
                "source": "baseline",
                "reusable": False,
                "params": quant_engine.strategy_params(),
            }
        candidates = self.models(limit=500, include_records=False).get("items", [])
        for item in candidates if isinstance(candidates, list) else []:
            if not isinstance(item, dict) or str(item.get("id") or "") != model_id:
                continue
            payload = dict(item)
            if include_records:
                payload.update(self._load_model_records(model_id))
            return payload
        return {}

    def trace(
        self,
        run_id: Optional[str] = None,
        generation: Optional[int] = None,
        limit: int = 200,
    ) -> Dict[str, Any]:
        limit = max(1, min(int(safe_float(limit, 200)), 2000))
        if not QUANT_DB_FILE.exists():
            return {
                "status": "ok",
                "selected_run_id": "",
                "runs": [],
                "metrics": [],
                "candidates": [],
                "summary": {"evaluated": 0, "selected": 0, "eliminated": 0},
            }
        conn = self._connect_db()
        try:
            run_rows = conn.execute(
                """
                SELECT r.run_id, r.status, r.started_at, r.finished_at, r.duration_ms,
                       r.generations, r.population_size, r.start_date, r.end_date,
                       r.objective, r.return_pct, r.max_drawdown_pct, r.win_rate, r.closed_trades,
                       COUNT(c.candidate_id) AS candidate_count,
                       SUM(CASE WHEN c.selected = 1 THEN 1 ELSE 0 END) AS selected_count
                FROM strategy_runs r
                LEFT JOIN strategy_candidates c ON c.run_id = r.run_id
                GROUP BY r.run_id
                ORDER BY COALESCE(NULLIF(r.finished_at, ''), NULLIF(r.started_at, '')) DESC
                LIMIT 20
                """
            ).fetchall()
            runs = [
                {
                    "run_id": str(row["run_id"] or ""),
                    "status": str(row["status"] or ""),
                    "started_at": str(row["started_at"] or ""),
                    "finished_at": str(row["finished_at"] or ""),
                    "duration_ms": safe_float(row["duration_ms"], 0),
                    "generations": int(row["generations"] or 0),
                    "population_size": int(row["population_size"] or 0),
                    "start_date": str(row["start_date"] or ""),
                    "end_date": str(row["end_date"] or ""),
                    "objective": safe_float(row["objective"], 0),
                    "return_pct": safe_float(row["return_pct"], 0),
                    "max_drawdown_pct": safe_float(row["max_drawdown_pct"], 0),
                    "win_rate": safe_float(row["win_rate"], 0),
                    "closed_trades": int(row["closed_trades"] or 0),
                    "candidate_count": int(row["candidate_count"] or 0),
                    "selected_count": int(row["selected_count"] or 0),
                    "eliminated_count": max(0, int(row["candidate_count"] or 0) - int(row["selected_count"] or 0)),
                }
                for row in run_rows
            ]
            selected_run_id = str(run_id or "").strip()
            if not selected_run_id:
                selected = next((item for item in runs if item.get("candidate_count")), runs[0] if runs else {})
                selected_run_id = str(selected.get("run_id") or "")
            if not selected_run_id:
                return {
                    "status": "ok",
                    "selected_run_id": "",
                    "runs": runs,
                    "metrics": [],
                    "candidates": [],
                    "summary": {"evaluated": 0, "selected": 0, "eliminated": 0},
                }

            metric_rows = conn.execute(
                """
                SELECT generation, best_objective, best_return_pct, best_drawdown_pct,
                       best_win_rate, population, raw_json
                FROM strategy_model_metrics
                WHERE run_id = ?
                ORDER BY generation ASC
                """,
                (selected_run_id,),
            ).fetchall()
            metrics = []
            for row in metric_rows:
                try:
                    raw = json.loads(str(row["raw_json"] or "{}"))
                except Exception:
                    raw = {}
                metrics.append(
                    {
                        "generation": int(row["generation"] or 0),
                        "best_objective": safe_float(row["best_objective"], 0),
                        "best_return_pct": safe_float(row["best_return_pct"], 0),
                        "best_drawdown_pct": safe_float(row["best_drawdown_pct"], 0),
                        "best_win_rate": safe_float(row["best_win_rate"], 0),
                        "population": int(row["population"] or 0),
                        "evaluated_count": int(safe_float(raw.get("evaluated_count"), row["population"] or 0)) if isinstance(raw, dict) else int(row["population"] or 0),
                        "kept_count": int(safe_float(raw.get("kept_count"), 0)) if isinstance(raw, dict) else 0,
                        "eliminated_count": int(safe_float(raw.get("eliminated_count"), 0)) if isinstance(raw, dict) else 0,
                        "cutoff_objective": safe_float(raw.get("cutoff_objective"), 0) if isinstance(raw, dict) else 0,
                    }
                )

            where = ["run_id = ?"]
            params: List[Any] = [selected_run_id]
            if generation is not None:
                where.append("generation = ?")
                params.append(int(generation))
            params.append(limit)
            candidate_rows = conn.execute(
                f"""
                SELECT candidate_id, run_id, generation, rank, selected, selection_role,
                       elimination_reason, objective, return_pct, max_drawdown_pct,
                       sharpe_ratio, profit_factor, win_rate, closed_trades,
                       params_hash, params_json
                FROM strategy_candidates
                WHERE {" AND ".join(where)}
                ORDER BY generation DESC, rank ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            candidates = []
            for row in candidate_rows:
                try:
                    item_params = json.loads(str(row["params_json"] or "{}"))
                except Exception:
                    item_params = {}
                candidates.append(
                    {
                        "candidate_id": str(row["candidate_id"] or ""),
                        "run_id": str(row["run_id"] or ""),
                        "generation": int(row["generation"] or 0),
                        "rank": int(row["rank"] or 0),
                        "selected": bool(row["selected"]),
                        "selection_role": str(row["selection_role"] or ""),
                        "elimination_reason": str(row["elimination_reason"] or ""),
                        "objective": safe_float(row["objective"], 0),
                        "return_pct": safe_float(row["return_pct"], 0),
                        "max_drawdown_pct": safe_float(row["max_drawdown_pct"], 0),
                        "sharpe_ratio": safe_float(row["sharpe_ratio"], 0),
                        "profit_factor": safe_float(row["profit_factor"], 0),
                        "win_rate": safe_float(row["win_rate"], 0),
                        "closed_trades": int(row["closed_trades"] or 0),
                        "params_hash": str(row["params_hash"] or ""),
                        "params": item_params if isinstance(item_params, dict) else {},
                    }
                )

            summary_row = conn.execute(
                """
                SELECT COUNT(*) AS evaluated,
                       SUM(CASE WHEN selected = 1 THEN 1 ELSE 0 END) AS selected,
                       MAX(generation) AS latest_generation
                FROM strategy_candidates
                WHERE run_id = ?
                """,
                (selected_run_id,),
            ).fetchone()
            summary_values = dict(summary_row) if summary_row else {}
            evaluated_count = int(summary_values.get("evaluated") or 0)
            selected_count = int(summary_values.get("selected") or 0)
            latest_generation = int(summary_values.get("latest_generation") or 0)
            return {
                "status": "ok",
                "selected_run_id": selected_run_id,
                "runs": runs,
                "metrics": metrics,
                "candidates": candidates,
                "summary": {
                    "evaluated": evaluated_count,
                    "selected": selected_count,
                    "eliminated": max(0, evaluated_count - selected_count),
                    "latest_generation": latest_generation,
                    "returned": len(candidates),
                },
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": str(exc),
                "selected_run_id": str(run_id or ""),
                "runs": [],
                "metrics": [],
                "candidates": [],
                "summary": {"evaluated": 0, "selected": 0, "eliminated": 0},
            }
        finally:
            conn.close()

    def mark_applied_model(self, model: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.status()
        payload["applied_model"] = {
            "id": model.get("id"),
            "name": model.get("name"),
            "applied_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._write_state(payload)
        return payload["applied_model"]

    def apply_model(self, model_id: str) -> Dict[str, Any]:
        models = self.models(include_records=False).get("items", [])
        for model in models:
            if str(model.get("id")) != str(model_id):
                continue
            params = model.get("params") if isinstance(model.get("params"), dict) else {}
            result = quant_engine.update_strategy_params(
                params,
                source={
                    "type": "strategy_model",
                    "model_id": str(model.get("id") or ""),
                    "name": str(model.get("name") or model.get("id") or ""),
                    "description": "来自策略库模型设为后台基准参数。",
                    "objective": model.get("objective"),
                    "return_pct": model.get("return_pct"),
                    "max_drawdown_pct": model.get("max_drawdown_pct"),
                    "win_rate": model.get("win_rate"),
                },
            )
            self.mark_applied_model(model)
            return {
                "status": "ok",
                "model": model,
                "strategy_params": result.get("strategy_params"),
                "strategy_source": result.get("strategy_source"),
            }
        return {"status": "not_found", "message": "model not found"}

    def run(
        self,
        generations: int = 4,
        population_size: int = 16,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        apply_best: bool = False,
        mode: str = "intraday",
    ) -> Dict[str, Any]:
        generations = max(1, min(int(generations or 4), _env_int("QT_STRATEGY_EVOLUTION_MAX_GENERATIONS", 8, minimum=1, maximum=30)))
        population_cap = _env_int("QT_STRATEGY_EVOLUTION_MAX_POPULATION", 32, minimum=6, maximum=80)
        population_size = max(6, min(int(population_size or 16), population_cap))
        start_date = start_date or quant_engine.first_data_date()
        end_date = end_date or quant_engine.latest_event_date()
        mode = str(mode or "intraday").strip().lower()
        if mode not in {"daily", "intraday"}:
            mode = "intraday"
        started_ts = time.time()
        started_at = datetime.now().isoformat(timespec="seconds")
        run_id = self._digest(
            "strategy_run",
            started_at,
            start_date,
            end_date,
            mode,
            generations,
            population_size,
            random.random(),
        )[:24]
        if not self._lock.acquire(blocking=False):
            return {"status": "running", "message": "strategy evolution is already running"}
        try:
            self._write_state(
                {
                    "status": "running",
                    "run_id": run_id,
                    "started_at": started_at,
                    "generations": generations,
                    "population_size": population_size,
                    "start_date": start_date,
                    "end_date": end_date,
                    "mode": mode,
                    "progress_pct": 1,
                    "progress_message": "进化已开始",
                    "models": self.models().get("items", []),
                },
            )
            base = quant_engine.strategy_params()
            population = self._initial_population(base, population_size)
            history = []
            best: Optional[Dict[str, Any]] = None
            last_evaluated: List[Dict[str, Any]] = []
            candidate_records: List[Dict[str, Any]] = []
            for generation in range(1, generations + 1):
                if self._pause_requested():
                    return self._paused_result(
                        run_id=run_id,
                        started_at=started_at,
                        started_ts=started_ts,
                        generations=generations,
                        population_size=population_size,
                        start_date=start_date,
                        end_date=end_date,
                        mode=mode,
                        completed_generations=generation - 1,
                        best=best,
                        history=history,
                        last_evaluated=last_evaluated,
                        candidate_records=candidate_records,
                    )
                evaluated = [self._evaluate(candidate, start_date=start_date, end_date=end_date, mode=mode) for candidate in population]
                evaluated.sort(key=lambda item: item["objective"], reverse=True)
                last_evaluated = evaluated
                elite_count = max(2, population_size // 5)
                generation_records = self._candidate_records_for_generation(run_id, generation, evaluated, elite_count)
                candidate_records.extend(generation_records)
                if best is None or evaluated[0]["objective"] > best["objective"]:
                    best = evaluated[0]
                cutoff_index = min(max(elite_count, 1), len(evaluated)) - 1
                history.append(
                    {
                        "generation": generation,
                        "best_objective": evaluated[0]["objective"],
                        "best_return_pct": evaluated[0]["return_pct"],
                        "best_drawdown_pct": evaluated[0]["max_drawdown_pct"],
                        "best_sharpe_ratio": evaluated[0].get("sharpe_ratio", 0),
                        "best_win_rate": evaluated[0]["win_rate"],
                        "population": len(evaluated),
                        "evaluated_count": len(evaluated),
                        "kept_count": min(elite_count, len(evaluated)),
                        "eliminated_count": max(0, len(evaluated) - elite_count),
                        "cutoff_objective": evaluated[cutoff_index]["objective"] if evaluated else 0,
                    }
                )
                self._write_state(
                    {
                        "status": "running",
                        "run_id": run_id,
                        "started_at": started_at,
                        "generations": generations,
                        "population_size": population_size,
                        "start_date": start_date,
                        "end_date": end_date,
                        "mode": mode,
                        "progress_pct": round(generation / generations * 100, 2),
                        "progress_message": f"已完成第 {generation}/{generations} 代",
                        "best": best,
                        "history": history,
                        "evaluated_candidate_count": len(candidate_records),
                        "models": self.models().get("items", []),
                    },
                )
                if self._pause_requested():
                    return self._paused_result(
                        run_id=run_id,
                        started_at=started_at,
                        started_ts=started_ts,
                        generations=generations,
                        population_size=population_size,
                        start_date=start_date,
                        end_date=end_date,
                        mode=mode,
                        completed_generations=generation,
                        best=best,
                        history=history,
                        last_evaluated=last_evaluated,
                        candidate_records=candidate_records,
                    )
                population = self._next_generation(evaluated, population_size)

            finished_at = datetime.now().isoformat(timespec="seconds")
            model_source = list(last_evaluated)
            if best and not any(item.get("params") == best.get("params") for item in model_source):
                model_source.append(best)
                model_source.sort(key=lambda item: item["objective"], reverse=True)
            models = self._build_models(model_source, finished_at)

            applied = False
            if apply_best and models:
                quant_engine.update_strategy_params(
                    models[0]["params"],
                    source={
                        "type": "strategy_model",
                        "model_id": str(models[0].get("id") or ""),
                        "name": str(models[0].get("name") or models[0].get("id") or ""),
                        "description": "来自策略进化完成后自动设为后台基准参数。",
                        "objective": models[0].get("objective"),
                        "return_pct": models[0].get("return_pct"),
                        "max_drawdown_pct": models[0].get("max_drawdown_pct"),
                        "win_rate": models[0].get("win_rate"),
                    },
                )
                applied = True

            result = {
                "status": "ok",
                "run_id": run_id,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": round((time.time() - started_ts) * 1000, 2),
                "generations": generations,
                "population_size": population_size,
                "progress_pct": 100,
                "progress_message": "进化完成",
                "start_date": start_date,
                "end_date": end_date,
                "mode": mode,
                "applied": applied,
                "best": best,
                "best_model": models[0] if models else {},
                "models": models,
                "history": history,
                "candidate_records": candidate_records,
                "evaluated_candidate_count": len(candidate_records),
            }
            try:
                self._persist_result(result)
            except Exception as exc:
                result["persist_error"] = str(exc)
            return self._write_state(result)
        finally:
            self._lock.release()

    def _paused_result(
        self,
        *,
        run_id: str,
        started_at: str,
        started_ts: float,
        generations: int,
        population_size: int,
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        completed_generations: int,
        best: Optional[Dict[str, Any]],
        history: List[Dict[str, Any]],
        last_evaluated: List[Dict[str, Any]],
        candidate_records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        paused_at = datetime.now().isoformat(timespec="seconds")
        model_source = list(last_evaluated)
        if best and not any(item.get("params") == best.get("params") for item in model_source):
            model_source.append(best)
            model_source.sort(key=lambda item: item["objective"], reverse=True)
        models = self._build_models(model_source, paused_at) if model_source else self.models().get("items", [])
        result = {
            "status": "paused",
            "run_id": run_id,
            "started_at": started_at,
            "updated_at": paused_at,
            "duration_ms": round((time.time() - started_ts) * 1000, 2),
            "generations": generations,
            "population_size": population_size,
            "completed_generations": completed_generations,
            "progress_pct": round(max(0, min(100, completed_generations / max(1, generations) * 100)), 2),
            "progress_message": "进化已暂停，可恢复后重新启动",
            "pause_requested": True,
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode,
            "best": best,
            "best_model": models[0] if models else {},
            "models": models,
            "history": history,
            "candidate_records": candidate_records,
            "evaluated_candidate_count": len(candidate_records),
        }
        try:
            self._persist_result(result)
        except Exception as exc:
            result["persist_error"] = str(exc)
        return self._write_state(result)

    def _candidate_elimination_reason(self, item: Dict[str, Any], selected: bool) -> str:
        if selected:
            return "保留进入下一代精英池"
        closed_trades = safe_float(item.get("closed_trades"), 0)
        return_pct = safe_float(item.get("return_pct"), 0)
        max_drawdown_pct = safe_float(item.get("max_drawdown_pct"), 0)
        profit_factor = safe_float(item.get("profit_factor"), 0)
        sharpe_ratio = safe_float(item.get("sharpe_ratio"), 0)
        if closed_trades < 5:
            return "闭环交易不足，样本不够稳定"
        if return_pct < 0:
            return "收益为负"
        if max_drawdown_pct <= -20:
            return "最大回撤过大"
        if profit_factor and profit_factor < 1:
            return "盈亏比不足"
        if sharpe_ratio < 0:
            return "夏普为负，波动收益质量差"
        return "综合目标函数排名低于精英线"

    def _candidate_records_for_generation(
        self,
        run_id: str,
        generation: int,
        evaluated: List[Dict[str, Any]],
        elite_count: int,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for rank, item in enumerate(evaluated, start=1):
            params = quant_engine.strategy_params(item.get("params", {}))
            params_hash = self._digest("strategy_candidate_params", params)[:16]
            selected = rank <= elite_count
            record = {
                "candidate_id": self._digest("strategy_candidate", run_id, generation, rank, params_hash)[:32],
                "run_id": run_id,
                "generation": generation,
                "rank": rank,
                "selected": selected,
                "selection_role": "elite_survivor" if selected else "eliminated",
                "elimination_reason": self._candidate_elimination_reason(item, selected),
                "objective": safe_float(item.get("objective"), 0),
                "return_pct": safe_float(item.get("return_pct"), 0),
                "max_drawdown_pct": safe_float(item.get("max_drawdown_pct"), 0),
                "sharpe_ratio": safe_float(item.get("sharpe_ratio"), 0),
                "profit_factor": safe_float(item.get("profit_factor"), 0),
                "win_rate": safe_float(item.get("win_rate"), 0),
                "closed_trades": int(safe_float(item.get("closed_trades"), 0)),
                "params_hash": params_hash,
                "params": params,
            }
            records.append(record)
        return records

    def _initial_population(self, base: Dict[str, float], population_size: int) -> List[Dict[str, float]]:
        population = [quant_engine.strategy_params(base)]
        while len(population) < population_size:
            population.append(self._mutate(base, scale=0.35))
        return population

    def _next_generation(self, evaluated: List[Dict[str, Any]], population_size: int) -> List[Dict[str, float]]:
        elite_count = max(2, population_size // 5)
        elites = [item["params"] for item in evaluated[:elite_count]]
        population = [dict(item) for item in elites]
        while len(population) < population_size:
            parent_a = random.choice(elites)
            parent_b = random.choice(evaluated[: max(elite_count + 2, population_size // 2)])["params"]
            child = {}
            for key in GENES:
                child[key] = parent_a[key] if random.random() < 0.5 else parent_b[key]
            population.append(self._mutate(child, scale=0.18))
        return population[:population_size]

    def _build_models(self, evaluated: List[Dict[str, Any]], finished_at: str) -> List[Dict[str, Any]]:
        stamp = "".join(ch for ch in finished_at if ch.isdigit())[:14]
        models = []
        for rank, item in enumerate(evaluated[:16], start=1):
            params = quant_engine.strategy_params(item.get("params", {}))
            models.append(
                {
                    "id": f"evo-{stamp}-{rank:02d}",
                    "name": f"进化策略 #{rank}",
                    "source": "genetic_evolution",
                    "reusable": True,
                    "generated_at": finished_at,
                    "rank": rank,
                    "objective": item.get("objective", 0),
                    "return_pct": item.get("return_pct", 0),
                    "max_drawdown_pct": item.get("max_drawdown_pct", 0),
                    "sharpe_ratio": item.get("sharpe_ratio", 0),
                    "profit_factor": item.get("profit_factor", 0),
                    "win_rate": item.get("win_rate", 0),
                    "closed_trades": item.get("closed_trades", 0),
                    "backtest": item.get("backtest", {}),
                    "trade_records": item.get("trade_records", []),
                    "delivery_records": item.get("delivery_records", []),
                    "daily_settlements": item.get("daily_settlements", []),
                    "params": params,
                }
            )
        return models

    def _mutate(self, params: Dict[str, Any], scale: float) -> Dict[str, float]:
        mutated = dict(params)
        for key, bounds in GENES.items():
            low, high = bounds
            current = safe_float(mutated.get(key), (low + high) / 2)
            if random.random() < 0.72:
                current += random.gauss(0, (high - low) * scale)
            mutated[key] = max(low, min(high, current))
        return quant_engine.strategy_params(mutated)

    def _evaluate(self, params: Dict[str, float], start_date: Optional[str], end_date: Optional[str], mode: str = "intraday") -> Dict[str, Any]:
        with quant_engine.temporary_strategy_params(params):
            if mode == "daily":
                result = quant_engine.walk_forward(
                    start_date=start_date,
                    end_date=end_date,
                    max_positions=int(params["max_positions"]),
                    hold_days=int(params["max_hold_days"]),
                    top_n=int(params["top_n"]),
                )
            else:
                result = quant_engine.walk_forward_intraday(
                    start_date=start_date,
                    end_date=end_date,
                    max_positions=int(params["max_positions"]),
                    hold_days=int(params["max_hold_days"]),
                    top_n=int(params["top_n"]),
                    use_daily_fallback=True,
                )
        return_pct = safe_float(result.get("return_pct"), 0)
        max_drawdown_pct = safe_float(result.get("max_drawdown_pct"), 0)
        win_rate = safe_float(result.get("win_rate"), 0)
        closed_trades = safe_float(result.get("closed_trades"), 0)
        performance = result.get("performance") if isinstance(result.get("performance"), dict) else {}
        sharpe_ratio = safe_float(performance.get("sharpe_ratio"), 0)
        profit_factor = safe_float(performance.get("profit_factor"), 0)
        trade_records = result.get("trades") if isinstance(result.get("trades"), list) else []
        account = quant_engine.account_from_trades(
            trade_records,
            initial_cash=result.get("initial_cash", params.get("account_initial_cash")),
            as_of=end_date or result.get("end_date"),
            limit=0,
        )
        trade_penalty = 10.0 if closed_trades < 5 else 0.0
        objective = (
            return_pct
            - abs(max_drawdown_pct) * 0.85
            + sharpe_ratio * 3.2
            + min(max(profit_factor, 0), 4) * 1.2
            + win_rate * 0.03
            + min(closed_trades, 60) * 0.02
            - trade_penalty
        )
        return {
            "objective": round(objective, 4),
            "return_pct": round(return_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "profit_factor": round(profit_factor, 4),
            "win_rate": round(win_rate, 4),
            "closed_trades": int(closed_trades),
            "backtest": {
                "mode": result.get("mode", "daily"),
                "start_date": result.get("start_date") or start_date,
                "end_date": result.get("end_date") or end_date,
                "initial_cash": result.get("initial_cash", params.get("account_initial_cash")),
                "final_value": result.get("final_value", params.get("account_initial_cash")),
                "return_pct": round(return_pct, 4),
                "max_drawdown_pct": round(max_drawdown_pct, 4),
                "sharpe_ratio": round(sharpe_ratio, 4),
                "profit_factor": round(profit_factor, 4),
                "win_rate": round(win_rate, 4),
                "closed_trades": int(closed_trades),
                "trade_count": len(trade_records),
                "total_fees": performance.get("total_fees", 0),
            },
            "trade_records": trade_records,
            "delivery_records": account.get("delivery_records", []),
            "daily_settlements": account.get("daily_settlements", []),
            "params": params,
        }


strategy_evolution = StrategyEvolution()
