from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo


CacheLoad = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
CacheSave = Callable[[str, Dict[str, Any], Dict[str, Any], int], None]
DeferredJobState = Callable[[Dict[str, Any], str], tuple[str, str, str]]
EnvFlag = Callable[[str, bool], bool]
RuntimeModelVersion = Callable[[Dict[str, Any]], str]


def _manual_required_heavy_job(job: str, message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "manual_required",
        "job": job,
        "manual_required": True,
        "message": message,
        **payload,
    }


class StrategyModelBacktestService:
    def __init__(
        self,
        *,
        quant_engine: Any,
        job_manager: Any,
        load_payload_cache: CacheLoad,
        save_payload_cache: CacheSave,
        cache_env_int: Callable[..., int],
        env_flag: EnvFlag,
        deferred_job_response_state: DeferredJobState,
        runtime_model_version: RuntimeModelVersion,
        app_version: Callable[[], str],
    ) -> None:
        self._quant_engine = quant_engine
        self._job_manager = job_manager
        self._load_payload_cache = load_payload_cache
        self._save_payload_cache = save_payload_cache
        self._cache_env_int = cache_env_int
        self._env_flag = env_flag
        self._deferred_job_response_state = deferred_job_response_state
        self._runtime_model_version = runtime_model_version
        self._app_version = app_version

    def cache_ttl(self) -> int:
        return self._cache_env_int("QT_MODEL_BACKTEST_CACHE_TTL_SECONDS", 600, minimum=0, maximum=86400)

    def backtest_payload(
        self,
        model: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        limit: int,
    ) -> Dict[str, Any]:
        params = self._quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
        start_date = str(start_date or self._quant_engine.first_data_date() or "").strip() or None
        end_date = str(end_date or self._quant_engine.latest_event_date() or "").strip() or None
        mode = str(mode or "intraday").strip().lower()
        with self._quant_engine.temporary_strategy_params(params):
            if mode in {"intraday", "intraday_5m", "minute"}:
                timeline = self._quant_engine.walk_forward_intraday(
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=params.get("account_initial_cash"),
                    max_positions=int(params.get("max_positions", 5)),
                    hold_days=int(params.get("max_hold_days", 3)),
                    top_n=int(params.get("top_n", 5)),
                    auto_fill=False,
                )
            else:
                timeline = self._quant_engine.walk_forward(
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=params.get("account_initial_cash"),
                    max_positions=int(params.get("max_positions", 5)),
                    hold_days=int(params.get("max_hold_days", 3)),
                    top_n=int(params.get("top_n", 5)),
                    auto_fill=False,
                )
            trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
            account = self._quant_engine.account_from_trades(
                trades,
                initial_cash=timeline.get("initial_cash", params.get("account_initial_cash")),
                as_of=end_date or timeline.get("end_date"),
                limit=limit,
            )
        return {
            "status": "ok",
            "model": model,
            "model_id": model.get("id"),
            "model_name": model.get("name"),
            "mode": timeline.get("mode", mode),
            "start_date": timeline.get("start_date") or start_date,
            "end_date": timeline.get("end_date") or end_date,
            "summary": {
                "initial_cash": timeline.get("initial_cash"),
                "final_value": timeline.get("final_value"),
                "return_pct": timeline.get("return_pct", 0),
                "max_drawdown_pct": timeline.get("max_drawdown_pct", 0),
                "annualized_return_pct": timeline.get("annualized_return_pct", 0),
                "sharpe_ratio": timeline.get("sharpe_ratio", 0),
                "profit_factor": timeline.get("profit_factor", 0),
                "win_rate": timeline.get("win_rate", 0),
                "closed_trades": timeline.get("closed_trades", 0),
                "trade_count": len(trades),
                "total_fees": timeline.get("total_fees", 0),
            },
            "account": account.get("account", {}),
            "positions": account.get("positions", []),
            "trade_records": trades if limit <= 0 else trades[-limit:],
            "delivery_records": account.get("delivery_records", []),
            "daily_settlements": account.get("daily_settlements", []),
            "equity_curve": timeline.get("equity_curve", []),
            "days": timeline.get("days", []),
            "strategy_params": params,
        }

    def cache_parts(
        self,
        model: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        limit: int,
    ) -> Dict[str, Any]:
        params = model.get("params") if isinstance(model.get("params"), dict) else {}
        params_hash = hashlib.sha256(
            json.dumps(params, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="ignore")
        ).hexdigest()
        return {
            "model_id": str(model.get("id") or ""),
            "model_version": self._runtime_model_version(model),
            "params_hash": params_hash,
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "mode": str(mode or "intraday").strip().lower(),
            "limit": max(0, min(int(limit or 0), 5000)),
            "version": self._app_version(),
        }

    @staticmethod
    def compact_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        return {
            "status": payload.get("status") or "ok",
            "job": "model_backtest",
            "model_id": payload.get("model_id") or "",
            "model_name": payload.get("model_name") or "",
            "mode": payload.get("mode") or "",
            "start_date": payload.get("start_date") or "",
            "end_date": payload.get("end_date") or "",
            "return_pct": summary.get("return_pct", 0),
            "trade_count": summary.get("trade_count", 0),
            "closed_trades": summary.get("closed_trades", 0),
            "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }

    def compute_cached(
        self,
        model: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        limit: int,
    ) -> Dict[str, Any]:
        clean_limit = max(0, min(int(limit or 0), 5000))
        payload = self.backtest_payload(
            model=model,
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            limit=clean_limit,
        )
        if isinstance(payload, dict):
            payload["source"] = "model_backtest_recompute"
            payload["model_backtest_cache"] = "refresh"
            payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            self._save_payload_cache(
                "model_backtest",
                self.cache_parts(model, start_date, end_date, mode, clean_limit),
                payload,
                self.cache_ttl(),
            )
            return payload
        return payload

    def queue_recompute(
        self,
        model: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        limit: int,
        process: bool = True,
    ) -> Dict[str, Any]:
        clean_limit = max(0, min(int(limit or 0), 5000))
        payload = {
            "model_id": str(model.get("id") or ""),
            "model_name": str(model.get("name") or model.get("id") or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "mode": str(mode or "intraday").strip().lower(),
            "limit": clean_limit,
        }
        if process:
            return self._job_manager.run_job_process(
                "model_backtest",
                payload=payload,
                message="model backtest recompute queued in an isolated process",
            )

        def execute() -> Dict[str, Any]:
            result = self.compute_cached(model, start_date, end_date, mode, clean_limit)
            return self.compact_result(result if isinstance(result, dict) else {})

        return self._job_manager.run_job_background(
            "model_backtest",
            execute,
            payload=payload,
            message="model backtest recompute queued in the background",
        )

    def pending(
        self,
        model: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        limit: int,
        job_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        status, cache_state, message = self._deferred_job_response_state(
            job_result,
            "model backtest recompute is being generated in the background; refresh later.",
        )
        return {
            "status": status,
            "source": "model_backtest_recompute",
            "model_backtest_cache": cache_state,
            "message": message,
            "model": model,
            "model_id": model.get("id"),
            "model_name": model.get("name"),
            "mode": str(mode or "intraday").strip().lower(),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "summary": {},
            "account": {},
            "positions": [],
            "trade_records": [],
            "delivery_records": [],
            "daily_settlements": [],
            "equity_curve": [],
            "days": [],
            "strategy_params": self._quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {}),
            "limit": max(0, min(int(limit or 0), 5000)),
            "job_result": job_result,
        }

    def recompute_payload(
        self,
        model: Dict[str, Any],
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        limit: int,
        force: bool = False,
        defer: bool = True,
        manual: bool = False,
        process: bool = True,
    ) -> Dict[str, Any]:
        clean_limit = max(0, min(int(limit or 0), 5000))
        cache_parts = self.cache_parts(model, start_date, end_date, mode, clean_limit)
        cached = None if force else self._load_payload_cache("model_backtest", cache_parts, self.cache_ttl())
        if cached:
            cached["model_backtest_cache"] = "hit"
            return cached
        if self._env_flag("QT_MODEL_BACKTEST_REQUIRE_MANUAL_TRIGGER", True) and not manual:
            return _manual_required_heavy_job(
                "model_backtest",
                "model backtest recompute requires an explicit manual trigger; normal refresh only reads saved records or cache.",
                {
                    "source": "model_backtest_recompute",
                    "model_backtest_cache": "manual_required",
                    "model": model,
                    "model_id": model.get("id"),
                    "model_name": model.get("name"),
                    "mode": str(mode or "intraday").strip().lower(),
                    "start_date": str(start_date or ""),
                    "end_date": str(end_date or ""),
                    "summary": {},
                    "account": {},
                    "positions": [],
                    "trade_records": [],
                    "delivery_records": [],
                    "daily_settlements": [],
                    "equity_curve": [],
                    "days": [],
                    "strategy_params": self._quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {}),
                    "limit": clean_limit,
                },
            )
        if defer:
            job_result = self.queue_recompute(model, start_date, end_date, mode, clean_limit, process=process)
            return self.pending(model, start_date, end_date, mode, clean_limit, job_result)
        return self.compute_cached(model, start_date, end_date, mode, clean_limit)

    def stored_payload(self, model: Dict[str, Any], limit: int = 0) -> Dict[str, Any]:
        backtest = model.get("backtest") if isinstance(model.get("backtest"), dict) else {}
        trades = model.get("trade_records") if isinstance(model.get("trade_records"), list) else []
        deliveries = model.get("delivery_records") if isinstance(model.get("delivery_records"), list) else []
        settlements = model.get("daily_settlements") if isinstance(model.get("daily_settlements"), list) else []
        params = self._quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
        if not backtest and not trades and not deliveries and not settlements:
            return {
                "status": "missing",
                "source": "strategy_model_records",
                "model": model,
                "model_id": model.get("id"),
                "model_name": model.get("name"),
                "message": "this model has no saved backtest records; manually recompute when needed.",
                "summary": {},
                "trade_records": [],
                "delivery_records": [],
                "daily_settlements": [],
                "equity_curve": [],
                "days": [],
                "strategy_params": params,
            }
        initial_cash = backtest.get("initial_cash") or params.get("account_initial_cash")
        as_of = backtest.get("end_date") or model.get("generated_at")
        account = self._quant_engine.account_from_trades(trades, initial_cash=initial_cash, as_of=as_of, limit=limit) if trades else {}
        return {
            "status": "ok",
            "source": "strategy_model_records",
            "model": model,
            "model_id": model.get("id"),
            "model_name": model.get("name"),
            "mode": backtest.get("mode") or "",
            "start_date": backtest.get("start_date") or "",
            "end_date": backtest.get("end_date") or "",
            "summary": {
                "initial_cash": initial_cash,
                "final_value": backtest.get("final_value"),
                "return_pct": backtest.get("return_pct", model.get("return_pct", 0)),
                "max_drawdown_pct": backtest.get("max_drawdown_pct", model.get("max_drawdown_pct", 0)),
                "annualized_return_pct": backtest.get("annualized_return_pct", 0),
                "sharpe_ratio": backtest.get("sharpe_ratio", 0),
                "profit_factor": backtest.get("profit_factor", 0),
                "win_rate": backtest.get("win_rate", model.get("win_rate", 0)),
                "closed_trades": backtest.get("closed_trades", model.get("closed_trades", 0)),
                "trade_count": backtest.get("trade_count", len(trades)),
                "total_fees": backtest.get("total_fees", 0),
            },
            "account": account.get("account", {}),
            "positions": account.get("positions", []),
            "trade_records": trades if limit <= 0 else trades[-limit:],
            "delivery_records": deliveries or account.get("delivery_records", []),
            "daily_settlements": settlements or account.get("daily_settlements", []),
            "equity_curve": model.get("equity_curve") if isinstance(model.get("equity_curve"), list) else [],
            "days": model.get("days") if isinstance(model.get("days"), list) else [],
            "strategy_params": params,
        }
