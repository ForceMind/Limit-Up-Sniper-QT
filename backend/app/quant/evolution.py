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

from app.quant.engine import quant_engine
from app.quant.engine_utils import read_json, safe_float, write_json
from app.quant.quant_paths import DATA_DIR, QUANT_DB_FILE
from app.quant.strategy_evolution_core import (
    build_evolution_models,
    candidate_elimination_reason,
    candidate_records_for_generation,
    evolution_evaluation_payload,
    initial_population,
    mutate_strategy_params,
    next_generation_population,
)
from app.quant.strategy_evolution_schema import (
    STRATEGY_EVOLUTION_SCHEMA_VERSION,
    ensure_strategy_evolution_schema,
)
from app.quant.strategy_evolution_repository import (
    MODEL_RECORD_KEYS,
    MODEL_RECORD_TYPE_KEYS,
    StrategyEvolutionRepository,
)
from app.quant.strategy_runtime_repository import StrategyRuntimeRepository
from app.quant.strategy_runtime_account import (
    cache_is_fresh,
    daily_runtime_source_filter,
    runtime_cache_key,
    runtime_date_filter,
    runtime_snapshot_payload,
    scale_runtime_trades,
    user_follow_snapshot_key,
)
from app.quant.strategy_follow_repository import StrategyFollowRepository


EVOLUTION_STATE_FILE = DATA_DIR / "strategy_evolution_state.json"
EVOLUTION_PAUSE_FILE = DATA_DIR / "strategy_evolution_pause.json"
EVOLUTION_STATE_MAX_INLINE_BYTES = 8 * 1024 * 1024
DAILY_RUNTIME_SOURCE_PREFIX = "daily_runtime"
DAILY_RUNTIME_SOURCE_UPPER_BOUND = "daily_runtimf"
_SCHEMA_READY_LOCK = threading.Lock()
_SCHEMA_READY_KEYS: set[tuple[str, int]] = set()


def _env_int(name: str, default: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
    try:
        value = int(float(os.getenv(name, "") or default))
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


class StrategyEvolution:
    def __init__(self) -> None:
        self.state_file = EVOLUTION_STATE_FILE
        self._lock = threading.Lock()
        self._repository = StrategyEvolutionRepository(
            db_exists=lambda: QUANT_DB_FILE.exists(),
            connect_db=self._connect_db,
            json_text=self._json_text,
            digest=self._digest,
            compact_result=self._compact_result,
            strip_model_records=self._strip_model_records,
        )
        self._follow_repository = StrategyFollowRepository(
            db_exists=lambda: QUANT_DB_FILE.exists(),
            connect_db=self._connect_db,
            json_text=self._json_text,
            digest=self._digest,
            user_follow_snapshot_key=self._user_follow_snapshot_key,
            user_follow_snapshot_is_fresh=self._user_follow_snapshot_is_fresh,
        )
        self._runtime_repository = StrategyRuntimeRepository(
            db_exists=lambda: QUANT_DB_FILE.exists(),
            connect_db=self._connect_db,
            json_text=self._json_text,
            digest=self._digest,
            runtime_model_version=self.runtime_model_version,
            runtime_date_filter=self._runtime_date_filter,
            daily_runtime_source_filter=self._daily_runtime_source_filter,
            runtime_snapshot_payload=self._runtime_snapshot_payload,
            scale_runtime_trades=self._scale_runtime_trades,
            runtime_cache_key=self._runtime_cache_key,
            runtime_cache_is_fresh=self._runtime_cache_is_fresh,
            quant_engine=quant_engine,
        )

    def _json_text(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def _digest(self, *parts: Any) -> str:
        text = "|".join(self._json_text(part) for part in parts)
        return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        ensure_strategy_evolution_schema(conn)

    def _connect_db(self) -> sqlite3.Connection:
        QUANT_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        existed = QUANT_DB_FILE.exists()
        conn = sqlite3.connect(QUANT_DB_FILE)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            schema_key = (str(QUANT_DB_FILE.resolve()), STRATEGY_EVOLUTION_SCHEMA_VERSION)
        except Exception:
            schema_key = (str(QUANT_DB_FILE), STRATEGY_EVOLUTION_SCHEMA_VERSION)
        if not existed or schema_key not in _SCHEMA_READY_KEYS:
            with _SCHEMA_READY_LOCK:
                if not existed or schema_key not in _SCHEMA_READY_KEYS:
                    self._ensure_schema(conn)
                    _SCHEMA_READY_KEYS.add(schema_key)
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
        self._repository.persist_result(result)

    def _load_model_record_counts(self, model_ids: List[str]) -> Dict[str, Dict[str, int]]:
        return self._repository.load_model_record_counts(model_ids)

    def _load_model_records(self, model_id: str) -> Dict[str, Any]:
        return self._repository.load_model_records(model_id)

    def _load_persisted_models(self, limit: int = 80, include_records: bool = False) -> List[Dict[str, Any]]:
        return self._repository.load_persisted_models(limit=limit, include_records=include_records)

    def _persisted_model_from_row(
        self,
        row: sqlite3.Row,
        include_records: bool = False,
        record_counts: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        return self._repository.persisted_model_from_row(
            row,
            include_records=include_records,
            record_counts=record_counts,
        )

    def _load_persisted_model(self, model_id: str, include_records: bool = False) -> Dict[str, Any]:
        return self._repository.load_persisted_model(model_id, include_records=include_records)

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
        return runtime_cache_key(
            model_id=model_id,
            params=params,
            initial_cash=initial_cash,
            start_date=start_date,
            as_of=as_of,
            limit=limit,
            model_version=model_version,
            digest=self._digest,
        )

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
        return user_follow_snapshot_key(
            username=username,
            model_id=model_id,
            params=params,
            initial_cash=initial_cash,
            follow_start_date=follow_start_date,
            as_of=as_of,
            limit=limit,
            model_version=model_version,
            digest=self._digest,
        )

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
        return runtime_date_filter(
            table=table,
            date_column=date_column,
            model_id=model_id,
            model_version=model_version,
            start_date=start_date,
            as_of=as_of,
            params_hash=params_hash,
        )

    def _daily_runtime_source_filter(self) -> tuple[str, list[str]]:
        return daily_runtime_source_filter(DAILY_RUNTIME_SOURCE_PREFIX, DAILY_RUNTIME_SOURCE_UPPER_BOUND)

    def _runtime_rows_exist(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        model_version: str,
        start_date: Optional[str],
        as_of: Optional[str],
        params_hash: str = "",
    ) -> bool:
        return self._runtime_repository._runtime_rows_exist(
            conn,
            model_id,
            model_version,
            start_date,
            as_of,
            params_hash=params_hash,
        )

    def _latest_runtime_scope(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        model_version: str,
        start_date: Optional[str],
        as_of: Optional[str],
        params_hash: str = "",
    ) -> Optional[Dict[str, str]]:
        return self._runtime_repository._latest_runtime_scope(
            conn,
            model_id,
            model_version,
            start_date,
            as_of,
            params_hash=params_hash,
        )

    def _select_runtime_scope(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        model_version: str,
        start_date: Optional[str],
        as_of: Optional[str],
        params_hash: str = "",
    ) -> Optional[Dict[str, str]]:
        return self._runtime_repository._select_runtime_scope(
            conn,
            model_id,
            model_version,
            start_date,
            as_of,
            params_hash=params_hash,
        )

    def _scale_runtime_trades(self, trades: List[Dict[str, Any]], base_cash: float, target_cash: float) -> List[Dict[str, Any]]:
        return scale_runtime_trades(trades, base_cash, target_cash)

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
        return runtime_snapshot_payload(
            snapshot_row=snapshot_row,
            model_id=model_id,
            selected_version=selected_version,
            selected_start_date=selected_start_date,
            requested_start_date=requested_start_date,
            as_of=as_of,
            target_cash=target_cash,
            generated_at=generated_at,
            scope=scope,
        )

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
        return self._runtime_repository.save_daily_runtime(
            model=model,
            params=params,
            timeline=timeline,
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            source=source,
        )

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
        return self._runtime_repository.load_runtime_account(
            model_id=model_id,
            initial_cash=initial_cash,
            start_date=start_date,
            as_of=as_of,
            limit=limit,
            model_version=model_version,
            params=params,
            hydrate_trades=hydrate_trades,
        )

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
        return self._follow_repository.load_user_follow_account(
            username=username,
            model_id=model_id,
            initial_cash=initial_cash,
            follow_start_date=follow_start_date,
            as_of=as_of,
            limit=limit,
            model_version=model_version,
            params=params,
        )

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
        self._follow_repository.save_user_follow_account(
            username=username,
            model_id=model_id,
            params=params,
            initial_cash=initial_cash,
            follow_start_date=follow_start_date,
            as_of=as_of,
            limit=limit,
            account=account,
            model_version=model_version,
            source=source,
        )

    def record_user_follow_period(
        self,
        username: str,
        profile: Dict[str, Any],
        reason: str = "",
        source: str = "",
        previous_profile: Optional[Dict[str, Any]] = None,
        created_at: str = "",
    ) -> Dict[str, Any]:
        return self._follow_repository.record_user_follow_period(
            username=username,
            profile=profile,
            reason=reason,
            source=source,
            previous_profile=previous_profile,
            created_at=created_at,
        )

    def user_follow_diagnostics(
        self,
        username: str,
        profile: Optional[Dict[str, Any]] = None,
        position_limit: int = 8,
        trade_limit: int = 8,
        period_limit: int = 6,
    ) -> Dict[str, Any]:
        return self._follow_repository.user_follow_diagnostics(
            username=username,
            profile=profile,
            position_limit=position_limit,
            trade_limit=trade_limit,
            period_limit=period_limit,
        )

    def _runtime_summary_for_model(
        self,
        conn: sqlite3.Connection,
        model_id: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._runtime_repository._runtime_summary_for_model(conn, model_id, params=params)

    def runtime_model_summaries(self, models: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return self._runtime_repository.runtime_model_summaries(models)

    def model_signal_feed(
        self,
        as_of: Optional[str] = None,
        limit_models: int = 20,
        limit_per_model: int = 12,
        fallback_latest: bool = True,
    ) -> Dict[str, Any]:
        return self._runtime_repository.model_signal_feed(
            as_of=as_of,
            limit_models=limit_models,
            limit_per_model=limit_per_model,
            fallback_latest=fallback_latest,
        )

    def _runtime_cache_is_fresh(self, generated_at: str) -> bool:
        return cache_is_fresh(generated_at, ttl_seconds=self._runtime_cache_ttl_seconds())

    def _user_follow_snapshot_is_fresh(self, generated_at: str) -> bool:
        return cache_is_fresh(generated_at, ttl_seconds=self._user_follow_cache_ttl_seconds())

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
        return self._runtime_repository.load_account_cache(
            model_id=model_id,
            params=params,
            initial_cash=initial_cash,
            start_date=start_date,
            as_of=as_of,
            limit=limit,
            model_version=model_version,
        )

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
        self._runtime_repository.save_account_cache(
            model_id=model_id,
            params=params,
            initial_cash=initial_cash,
            start_date=start_date,
            as_of=as_of,
            limit=limit,
            account=account,
            model_version=model_version,
            source=source,
        )

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
                "name": "系统默认基础参数（非跟随策略）",
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
                "name": "系统默认基础参数（非跟随策略）",
                "source": "baseline",
                "reusable": False,
                "params": quant_engine.strategy_params(),
            }
        persisted = self._load_persisted_model(model_id, include_records=include_records)
        if persisted:
            return persisted
        candidates = self.status().get("models", [])
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
                    "description": "来自策略库模型复制为系统默认基础参数。",
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
                        "description": "来自策略进化完成后自动复制为系统默认基础参数。",
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
        return candidate_elimination_reason(item, selected)

    def _candidate_records_for_generation(
        self,
        run_id: str,
        generation: int,
        evaluated: List[Dict[str, Any]],
        elite_count: int,
    ) -> List[Dict[str, Any]]:
        return candidate_records_for_generation(
            run_id=run_id,
            generation=generation,
            evaluated=evaluated,
            elite_count=elite_count,
            normalize_params=quant_engine.strategy_params,
            digest=self._digest,
        )

    def _initial_population(self, base: Dict[str, float], population_size: int) -> List[Dict[str, float]]:
        return initial_population(base, population_size=population_size, normalize_params=quant_engine.strategy_params)

    def _next_generation(self, evaluated: List[Dict[str, Any]], population_size: int) -> List[Dict[str, float]]:
        return next_generation_population(
            evaluated,
            population_size=population_size,
            normalize_params=quant_engine.strategy_params,
        )

    def _build_models(self, evaluated: List[Dict[str, Any]], finished_at: str) -> List[Dict[str, Any]]:
        return build_evolution_models(
            evaluated,
            finished_at=finished_at,
            normalize_params=quant_engine.strategy_params,
        )

    def _mutate(self, params: Dict[str, Any], scale: float) -> Dict[str, float]:
        return mutate_strategy_params(params, scale=scale, normalize_params=quant_engine.strategy_params)

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
        trade_records = result.get("trades") if isinstance(result.get("trades"), list) else []
        account = quant_engine.account_from_trades(
            trade_records,
            initial_cash=result.get("initial_cash", params.get("account_initial_cash")),
            as_of=end_date or result.get("end_date"),
            limit=0,
        )
        return evolution_evaluation_payload(
            result=result,
            params=params,
            account=account,
            start_date=start_date,
            end_date=end_date,
        )


strategy_evolution = StrategyEvolution()
