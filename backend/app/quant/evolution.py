from __future__ import annotations

import hashlib
import json
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
                "name": "系统当前参数",
                "source": "runtime",
                "reusable": True,
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
                "name": "系统当前参数",
                "source": "runtime",
                "reusable": True,
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

    def apply_model(self, model_id: str) -> Dict[str, Any]:
        payload = self.status()
        models = self.models(include_records=False).get("items", [])
        for model in models:
            if str(model.get("id")) != str(model_id):
                continue
            params = model.get("params") if isinstance(model.get("params"), dict) else {}
            result = quant_engine.update_strategy_params(params)
            payload["applied_model"] = {
                "id": model.get("id"),
                "name": model.get("name"),
                "applied_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._write_state(payload)
            return {"status": "ok", "model": model, "strategy_params": result.get("strategy_params")}
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
        if not self._lock.acquire(blocking=False):
            return {"status": "running", "message": "strategy evolution is already running"}
        try:
            self._write_state(
                {
                    "status": "running",
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
            for generation in range(1, generations + 1):
                if self._pause_requested():
                    return self._paused_result(
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
                    )
                evaluated = [self._evaluate(candidate, start_date=start_date, end_date=end_date, mode=mode) for candidate in population]
                evaluated.sort(key=lambda item: item["objective"], reverse=True)
                last_evaluated = evaluated
                if best is None or evaluated[0]["objective"] > best["objective"]:
                    best = evaluated[0]
                history.append(
                    {
                        "generation": generation,
                        "best_objective": evaluated[0]["objective"],
                        "best_return_pct": evaluated[0]["return_pct"],
                        "best_drawdown_pct": evaluated[0]["max_drawdown_pct"],
                        "best_sharpe_ratio": evaluated[0].get("sharpe_ratio", 0),
                        "best_win_rate": evaluated[0]["win_rate"],
                        "population": len(evaluated),
                    }
                )
                self._write_state(
                    {
                        "status": "running",
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
                        "models": self.models().get("items", []),
                    },
                )
                if self._pause_requested():
                    return self._paused_result(
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
                quant_engine.update_strategy_params(models[0]["params"])
                applied = True

            result = {
                "status": "ok",
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
    ) -> Dict[str, Any]:
        paused_at = datetime.now().isoformat(timespec="seconds")
        model_source = list(last_evaluated)
        if best and not any(item.get("params") == best.get("params") for item in model_source):
            model_source.append(best)
            model_source.sort(key=lambda item: item["objective"], reverse=True)
        models = self._build_models(model_source, paused_at) if model_source else self.models().get("items", [])
        result = {
            "status": "paused",
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
        }
        try:
            self._persist_result(result)
        except Exception as exc:
            result["persist_error"] = str(exc)
        return self._write_state(result)

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
