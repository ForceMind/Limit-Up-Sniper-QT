from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app.quant.engine_utils import safe_float
from app.quant.strategy_model_lookup_service import StrategyModelLookupNotFound


CacheLoad = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
CacheSave = Callable[[str, Dict[str, Any], Dict[str, Any], int], None]
DeferredJobState = Callable[[Dict[str, Any], str], tuple[str, str, str]]
EnvFlag = Callable[[str, bool], bool]
FindStrategyModel = Callable[[str, bool], Dict[str, Any]]
RuntimeModelVersion = Callable[[Dict[str, Any]], str]


def _manual_required_heavy_job(job: str, message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "manual_required",
        "job": job,
        "manual_required": True,
        "message": message,
        **payload,
    }


class QuantTimelineService:
    def __init__(
        self,
        *,
        quant_engine: Any,
        job_manager: Any,
        find_strategy_model: FindStrategyModel,
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
        self._find_strategy_model = find_strategy_model
        self._load_payload_cache = load_payload_cache
        self._save_payload_cache = save_payload_cache
        self._cache_env_int = cache_env_int
        self._env_flag = env_flag
        self._deferred_job_response_state = deferred_job_response_state
        self._runtime_model_version = runtime_model_version
        self._app_version = app_version

    def find_strategy_model(self, model_id: str, include_records: bool = True) -> Dict[str, Any]:
        try:
            return self._find_strategy_model(model_id, include_records)
        except StrategyModelLookupNotFound as exc:
            raise HTTPException(status_code=404, detail="strategy model not found") from exc

    def payload(
        self,
        *,
        model_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: Optional[int],
        top_n: Optional[int],
        intraday: bool,
        use_daily_fallback: bool = True,
        auto_fill: bool = True,
    ) -> Dict[str, Any]:
        clean_model_id = str(model_id or "").strip()
        if not clean_model_id:
            if intraday:
                return self._quant_engine.walk_forward_intraday(
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=initial_cash,
                    max_positions=max_positions,
                    hold_days=hold_days,
                    top_n=top_n,
                    use_daily_fallback=use_daily_fallback,
                    auto_fill=auto_fill,
                )
            return self._quant_engine.walk_forward(
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=hold_days,
                top_n=top_n,
                auto_fill=auto_fill,
            )

        model = self.find_strategy_model(clean_model_id, False)
        params = self._quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
        effective_initial_cash = initial_cash if initial_cash is not None else safe_float(params.get("account_initial_cash"), 100000)
        effective_max_positions = max_positions if max_positions is not None else int(safe_float(params.get("max_positions"), 5))
        effective_hold_days = hold_days if hold_days is not None else int(safe_float(params.get("max_hold_days"), 3))
        effective_top_n = top_n if top_n is not None else int(safe_float(params.get("top_n"), 5))
        with self._quant_engine.temporary_strategy_params(params):
            if intraday:
                payload = self._quant_engine.walk_forward_intraday(
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=effective_initial_cash,
                    max_positions=effective_max_positions,
                    hold_days=effective_hold_days,
                    top_n=effective_top_n,
                    use_daily_fallback=use_daily_fallback,
                    auto_fill=auto_fill,
                )
            else:
                payload = self._quant_engine.walk_forward(
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=effective_initial_cash,
                    max_positions=effective_max_positions,
                    hold_days=effective_hold_days,
                    top_n=effective_top_n,
                    auto_fill=auto_fill,
                )
        if isinstance(payload, dict):
            payload["strategy_model_id"] = str(model.get("id") or clean_model_id)
            payload["strategy_name"] = str(model.get("name") or clean_model_id)
            payload["strategy_params"] = params
            payload["strategy_scope"] = "strategy_model"
        return payload

    def cache_ttl(self) -> int:
        return self._cache_env_int("QT_TIMELINE_CACHE_TTL_SECONDS", 600, minimum=0, maximum=86400)

    def cache_parts(
        self,
        *,
        model_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: Optional[int],
        top_n: Optional[int],
        intraday: bool,
        use_daily_fallback: bool = True,
        auto_fill: bool = True,
    ) -> Dict[str, Any]:
        clean_model_id = str(model_id or "").strip()
        identity: Dict[str, Any] = {}
        if clean_model_id:
            model = self.find_strategy_model(clean_model_id, False)
            params = self._quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
            identity["model_version"] = self._runtime_model_version(model)
            identity["strategy_name"] = str(model.get("name") or clean_model_id)
        else:
            params = self._quant_engine.strategy_params()
            identity["strategy_source"] = self._quant_engine.strategy_source()
        params_hash = hashlib.sha256(
            json.dumps(params, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="ignore")
        ).hexdigest()
        return {
            "model_id": clean_model_id,
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "initial_cash": None if initial_cash is None else round(safe_float(initial_cash), 2),
            "max_positions": None if max_positions is None else int(max_positions),
            "hold_days": None if hold_days is None else int(hold_days),
            "top_n": None if top_n is None else int(top_n),
            "intraday": bool(intraday),
            "use_daily_fallback": bool(use_daily_fallback),
            "auto_fill": bool(auto_fill),
            "params_hash": params_hash,
            "version": self._app_version(),
            **identity,
        }

    @staticmethod
    def compact_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
        days = payload.get("days") if isinstance(payload.get("days"), list) else []
        return {
            "status": payload.get("status") or "ok",
            "job": "quant_timeline",
            "mode": payload.get("mode") or "",
            "start_date": payload.get("start_date") or "",
            "end_date": payload.get("end_date") or "",
            "strategy_model_id": payload.get("strategy_model_id") or "",
            "strategy_name": payload.get("strategy_name") or "",
            "return_pct": payload.get("return_pct", 0),
            "trade_count": len(trades),
            "closed_trades": payload.get("closed_trades", 0),
            "day_count": len(days),
            "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }

    def compute_cached(
        self,
        *,
        model_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: Optional[int],
        top_n: Optional[int],
        intraday: bool,
        use_daily_fallback: bool = True,
        auto_fill: bool = True,
    ) -> Dict[str, Any]:
        payload = self.payload(
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            intraday=intraday,
            use_daily_fallback=use_daily_fallback,
            auto_fill=auto_fill,
        )
        if isinstance(payload, dict):
            payload["timeline_cache"] = "refresh"
            payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            self._save_payload_cache(
                "quant_timeline",
                self.cache_parts(
                    model_id=model_id,
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=initial_cash,
                    max_positions=max_positions,
                    hold_days=hold_days,
                    top_n=top_n,
                    intraday=intraday,
                    use_daily_fallback=use_daily_fallback,
                    auto_fill=auto_fill,
                ),
                payload,
                self.cache_ttl(),
            )
            return payload
        return payload

    def queue_precompute(
        self,
        *,
        model_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: Optional[int],
        top_n: Optional[int],
        intraday: bool,
        use_daily_fallback: bool = True,
        auto_fill: bool = True,
        process: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "model_id": str(model_id or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "initial_cash": initial_cash,
            "max_positions": max_positions,
            "hold_days": hold_days,
            "top_n": top_n,
            "intraday": bool(intraday),
            "mode": "intraday" if intraday else "daily",
            "use_daily_fallback": bool(use_daily_fallback),
            "auto_fill": bool(auto_fill),
        }
        if process:
            return self._job_manager.run_job_process(
                "quant_timeline",
                payload=payload,
                message="quant timeline recompute queued in an isolated process",
            )

        def execute() -> Dict[str, Any]:
            result = self.compute_cached(
                model_id=model_id,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=hold_days,
                top_n=top_n,
                intraday=intraday,
                use_daily_fallback=use_daily_fallback,
                auto_fill=auto_fill,
            )
            return self.compact_result(result if isinstance(result, dict) else {})

        return self._job_manager.run_job_background(
            "quant_timeline",
            execute,
            payload=payload,
            message="quant timeline recompute queued in the background",
        )

    def pending(
        self,
        *,
        model_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: Optional[int],
        top_n: Optional[int],
        intraday: bool,
        use_daily_fallback: bool,
        auto_fill: bool,
        job_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        status, cache_state, message = self._deferred_job_response_state(
            job_result,
            "quant timeline is being generated in the background; refresh later.",
        )
        return {
            "status": status,
            "source": "quant_timeline",
            "timeline_cache": cache_state,
            "message": message,
            "mode": "intraday" if intraday else "daily",
            "model_id": str(model_id or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "initial_cash": initial_cash,
            "max_positions": max_positions,
            "hold_days": hold_days,
            "top_n": top_n,
            "intraday": bool(intraday),
            "use_daily_fallback": bool(use_daily_fallback),
            "auto_fill": bool(auto_fill),
            "trades": [],
            "equity_curve": [],
            "days": [],
            "job_result": job_result,
        }

    def cached_or_deferred(
        self,
        *,
        model_id: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: Optional[int],
        top_n: Optional[int],
        intraday: bool,
        use_daily_fallback: bool = True,
        auto_fill: bool = True,
        force: bool = False,
        defer: bool = True,
        process: bool = True,
        manual: bool = False,
    ) -> Dict[str, Any]:
        cache_parts = self.cache_parts(
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            intraday=intraday,
            use_daily_fallback=use_daily_fallback,
            auto_fill=auto_fill,
        )
        cached = None if force else self._load_payload_cache("quant_timeline", cache_parts, self.cache_ttl())
        if cached:
            cached["timeline_cache"] = "hit"
            return cached
        if self._env_flag("QT_TIMELINE_REQUIRE_MANUAL_TRIGGER", True) and not manual:
            return _manual_required_heavy_job(
                "quant_timeline",
                "quant timeline recompute requires an explicit manual trigger; normal refresh only reads cache.",
                {
                    "source": "quant_timeline",
                    "timeline_cache": "manual_required",
                    "mode": "intraday" if intraday else "daily",
                    "model_id": str(model_id or ""),
                    "start_date": str(start_date or ""),
                    "end_date": str(end_date or ""),
                    "initial_cash": initial_cash,
                    "max_positions": max_positions,
                    "hold_days": hold_days,
                    "top_n": top_n,
                    "intraday": bool(intraday),
                    "use_daily_fallback": bool(use_daily_fallback),
                    "auto_fill": bool(auto_fill),
                    "trades": [],
                    "equity_curve": [],
                    "days": [],
                },
            )
        if defer:
            job_result = self.queue_precompute(
                model_id=model_id,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=hold_days,
                top_n=top_n,
                intraday=intraday,
                use_daily_fallback=use_daily_fallback,
                auto_fill=auto_fill,
                process=process,
            )
            return self.pending(
                model_id=model_id,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=hold_days,
                top_n=top_n,
                intraday=intraday,
                use_daily_fallback=use_daily_fallback,
                auto_fill=auto_fill,
                job_result=job_result,
            )
        return self.compute_cached(
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            intraday=intraday,
            use_daily_fallback=use_daily_fallback,
            auto_fill=auto_fill,
        )

    def route_payload(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        model_id: Optional[str],
        initial_cash: Optional[float],
        max_positions: Optional[int],
        hold_days: Optional[int],
        top_n: Optional[int],
        intraday: bool,
        use_daily_fallback: bool,
        auto_fill: bool,
        force: bool,
        defer: bool,
        process: bool,
        manual: bool,
    ) -> Dict[str, Any]:
        return self.cached_or_deferred(
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            intraday=intraday,
            use_daily_fallback=use_daily_fallback,
            auto_fill=auto_fill,
            force=force,
            defer=defer,
            process=process,
            manual=manual,
        )
