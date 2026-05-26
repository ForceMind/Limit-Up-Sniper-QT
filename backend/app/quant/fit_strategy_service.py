from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo


DeferredJobState = Callable[[Dict[str, Any], str], tuple[str, str, str]]
EnvFlag = Callable[[str, bool], bool]


def _manual_required_fit_strategy(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "manual_required",
        "source": "fit_strategy",
        "job": "fit_strategy",
        "manual_required": True,
        "message": "fit strategy optimization requires an explicit manual trigger; normal refresh only reads saved state.",
        **payload,
    }


class FitStrategyService:
    def __init__(
        self,
        *,
        quant_engine: Any,
        job_manager: Any,
        deferred_job_response_state: DeferredJobState,
        env_flag: EnvFlag,
    ) -> None:
        self._quant_engine = quant_engine
        self._job_manager = job_manager
        self._deferred_job_response_state = deferred_job_response_state
        self._env_flag = env_flag

    @staticmethod
    def compact_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        best = payload.get("best") if isinstance(payload.get("best"), dict) else {}
        return {
            "status": payload.get("status") or "ok",
            "job": "fit_strategy",
            "as_of": payload.get("as_of") or "",
            "start_date": payload.get("start_date") or "",
            "applied": bool(payload.get("applied")),
            "best_name": best.get("name") or "",
            "objective": best.get("objective", 0),
            "return_pct": best.get("return_pct", 0),
            "candidate_count": len(payload.get("candidates") if isinstance(payload.get("candidates"), list) else []),
            "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }

    def compute(
        self,
        *,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        apply_best: bool = True,
    ) -> Dict[str, Any]:
        payload = self._quant_engine.fit_strategy(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            apply_best=apply_best,
        )
        if isinstance(payload, dict):
            payload["source"] = "fit_strategy"
            payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        return payload

    def queue(
        self,
        *,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        apply_best: bool,
        process: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "as_of": str(as_of or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "apply_best": bool(apply_best),
        }
        if process:
            return self._job_manager.run_job_process(
                "fit_strategy",
                payload=payload,
                message="fit strategy queued in an isolated process",
            )

        def execute() -> Dict[str, Any]:
            result = self.compute(**payload)
            return self.compact_result(result if isinstance(result, dict) else {})

        return self._job_manager.run_job_background(
            "fit_strategy",
            execute,
            payload=payload,
            message="fit strategy queued in the background",
        )

    def pending(
        self,
        *,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        apply_best: bool,
        job_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        status, _cache_state, message = self._deferred_job_response_state(
            job_result,
            "fit strategy is being generated in the background; check job status and refresh after completion.",
        )
        return {
            "status": status,
            "source": "fit_strategy",
            "message": message,
            "as_of": str(as_of or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "apply_best": bool(apply_best),
            "applied": False,
            "best": {},
            "candidates": [],
            "strategy_params": self._quant_engine.strategy_params(),
            "job_result": job_result,
        }

    def deferred_or_sync(
        self,
        *,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        apply_best: bool,
        defer: bool = True,
        process: bool = True,
        manual: bool = False,
    ) -> Dict[str, Any]:
        manual_payload = {
            "as_of": str(as_of or ""),
            "start_date": str(start_date or ""),
            "end_date": str(end_date or ""),
            "apply_best": bool(apply_best),
            "applied": False,
            "best": {},
            "candidates": [],
            "strategy_params": self._quant_engine.strategy_params(),
        }
        if self._env_flag("QT_FIT_STRATEGY_REQUIRE_MANUAL_TRIGGER", True) and not manual:
            return _manual_required_fit_strategy(manual_payload)
        if defer:
            job_result = self.queue(
                as_of=as_of,
                start_date=start_date,
                end_date=end_date,
                apply_best=apply_best,
                process=process,
            )
            return self.pending(
                as_of=as_of,
                start_date=start_date,
                end_date=end_date,
                apply_best=apply_best,
                job_result=job_result,
            )
        return self.compute(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            apply_best=apply_best,
        )
