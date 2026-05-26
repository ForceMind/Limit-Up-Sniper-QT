from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from app.quant.engine_utils import safe_float


BacktestCacheLoad = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
BacktestCacheSave = Callable[[str, Dict[str, Any], Dict[str, Any], int], None]
CacheEnvInt = Callable[[str, int], int]
DeferredJobState = Callable[[Dict[str, Any], str], tuple[str, str, str]]
EnvFlag = Callable[[str, bool], bool]


def _manual_required_heavy_job(job: str, message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "manual_required",
        "job": job,
        "manual_required": True,
        "message": message,
        **payload,
    }


class QuantBacktestService:
    def __init__(
        self,
        *,
        quant_engine: Any,
        job_manager: Any,
        load_payload_cache: BacktestCacheLoad,
        save_payload_cache: BacktestCacheSave,
        cache_env_int: Callable[..., int],
        env_flag: EnvFlag,
        deferred_job_response_state: DeferredJobState,
        app_version: Callable[[], str],
    ) -> None:
        self._quant_engine = quant_engine
        self._job_manager = job_manager
        self._load_payload_cache = load_payload_cache
        self._save_payload_cache = save_payload_cache
        self._cache_env_int = cache_env_int
        self._env_flag = env_flag
        self._deferred_job_response_state = deferred_job_response_state
        self._app_version = app_version

    def cache_ttl(self) -> int:
        return self._cache_env_int("QT_BACKTEST_CACHE_TTL_SECONDS", 600, minimum=0, maximum=86400)

    def cache_parts(
        self,
        *,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: int,
        top_n: int,
        auto_fill: bool,
    ) -> Dict[str, Any]:
        params = self._quant_engine.strategy_params()
        params_hash = hashlib.sha256(
            json.dumps(params, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="ignore")
        ).hexdigest()
        return {
            "as_of": str(as_of or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "initial_cash": round(safe_float(initial_cash, 0), 2) if initial_cash is not None else "",
            "max_positions": int(max_positions) if max_positions is not None else "",
            "hold_days": max(1, min(int(hold_days or 3), 60)),
            "top_n": max(1, min(int(top_n or 5), 50)),
            "auto_fill": bool(auto_fill),
            "strategy_source": self._quant_engine.strategy_source(),
            "params_hash": params_hash,
            "version": self._app_version(),
        }

    @staticmethod
    def compact_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": payload.get("status") or "ok",
            "job": "quant_backtest",
            "as_of": payload.get("as_of") or "",
            "start_date": payload.get("start_date") or "",
            "end_date": payload.get("end_date") or "",
            "return_pct": payload.get("return_pct", 0),
            "trade_count": payload.get("timeline_trade_count", payload.get("trades", 0)),
            "closed_trades": payload.get("closed_trades", 0),
            "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }

    def compute_cached(
        self,
        *,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: Optional[float] = None,
        max_positions: Optional[int] = None,
        hold_days: int = 3,
        top_n: int = 5,
        auto_fill: bool = True,
    ) -> Dict[str, Any]:
        clean_hold_days = max(1, min(int(hold_days or 3), 60))
        clean_top_n = max(1, min(int(top_n or 5), 50))
        payload = self._quant_engine.backtest(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=clean_hold_days,
            top_n=clean_top_n,
            auto_fill=auto_fill,
        )
        if isinstance(payload, dict):
            payload["source"] = "quant_backtest"
            payload["backtest_cache"] = "refresh"
            payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            self._save_payload_cache(
                "quant_backtest",
                self.cache_parts(
                    as_of=as_of,
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=initial_cash,
                    max_positions=max_positions,
                    hold_days=clean_hold_days,
                    top_n=clean_top_n,
                    auto_fill=auto_fill,
                ),
                payload,
                self.cache_ttl(),
            )
        return payload

    def queue_precompute(
        self,
        *,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: int,
        top_n: int,
        auto_fill: bool,
        process: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "as_of": str(as_of or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "initial_cash": initial_cash,
            "max_positions": max_positions,
            "hold_days": max(1, min(int(hold_days or 3), 60)),
            "top_n": max(1, min(int(top_n or 5), 50)),
            "auto_fill": bool(auto_fill),
        }
        if process:
            return self._job_manager.run_job_process(
                "quant_backtest",
                payload=payload,
                message="quant backtest recompute queued in an isolated process",
            )

        def execute() -> Dict[str, Any]:
            result = self.compute_cached(**payload)
            return self.compact_result(result if isinstance(result, dict) else {})

        return self._job_manager.run_job_background(
            "quant_backtest",
            execute,
            payload=payload,
            message="quant backtest recompute queued in the background",
        )

    def pending(
        self,
        *,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: int,
        top_n: int,
        auto_fill: bool,
        job_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        status, cache_state, message = self._deferred_job_response_state(
            job_result,
            "quant backtest is being generated in the background; refresh later.",
        )
        return {
            "status": status,
            "source": "quant_backtest",
            "backtest_cache": cache_state,
            "message": message,
            "as_of": str(as_of or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "initial_cash": initial_cash,
            "max_positions": max_positions,
            "hold_days": max(1, min(int(hold_days or 3), 60)),
            "top_n": max(1, min(int(top_n or 5), 50)),
            "auto_fill": bool(auto_fill),
            "recent_trades": [],
            "trade_records": [],
            "account": {},
            "positions": [],
            "delivery_records": [],
            "daily_settlements": [],
            "days": [],
            "equity_curve": [],
            "job_result": job_result,
        }

    def cached_or_deferred(
        self,
        *,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: int,
        top_n: int,
        auto_fill: bool,
        force: bool = False,
        defer: bool = True,
        process: bool = True,
        manual: bool = False,
    ) -> Dict[str, Any]:
        clean_hold_days = max(1, min(int(hold_days or 3), 60))
        clean_top_n = max(1, min(int(top_n or 5), 50))
        cache_parts = self.cache_parts(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=clean_hold_days,
            top_n=clean_top_n,
            auto_fill=auto_fill,
        )
        cached = None if force else self._load_payload_cache("quant_backtest", cache_parts, self.cache_ttl())
        if cached:
            cached["backtest_cache"] = "hit"
            return cached
        if self._env_flag("QT_BACKTEST_REQUIRE_MANUAL_TRIGGER", True) and not manual:
            return _manual_required_heavy_job(
                "quant_backtest",
                "quant backtest recompute requires an explicit manual trigger; normal refresh only reads cache.",
                {
                    "source": "quant_backtest",
                    "backtest_cache": "manual_required",
                    "as_of": str(as_of or ""),
                    "start_date": str(start_date or ""),
                    "end_date": str(end_date or ""),
                    "initial_cash": initial_cash,
                    "max_positions": max_positions,
                    "hold_days": clean_hold_days,
                    "top_n": clean_top_n,
                    "auto_fill": bool(auto_fill),
                    "recent_trades": [],
                    "trade_records": [],
                    "account": {},
                    "positions": [],
                    "delivery_records": [],
                    "daily_settlements": [],
                    "days": [],
                    "equity_curve": [],
                },
            )
        if defer:
            job_result = self.queue_precompute(
                as_of=as_of,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=clean_hold_days,
                top_n=clean_top_n,
                auto_fill=auto_fill,
                process=process,
            )
            return self.pending(
                as_of=as_of,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=clean_hold_days,
                top_n=clean_top_n,
                auto_fill=auto_fill,
                job_result=job_result,
            )
        return self.compute_cached(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=clean_hold_days,
            top_n=clean_top_n,
            auto_fill=auto_fill,
        )

    def route_payload(
        self,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: int,
        top_n: int,
        auto_fill: bool,
        force: bool,
        defer: bool,
        process: bool,
        manual: bool,
    ) -> Dict[str, Any]:
        return self.cached_or_deferred(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            auto_fill=auto_fill,
            force=force,
            defer=defer,
            process=process,
            manual=manual,
        )
