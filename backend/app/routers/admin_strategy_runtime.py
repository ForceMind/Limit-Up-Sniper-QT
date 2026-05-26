from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from app.quant.strategy_runtime_admin import StrategyRuntimeModelNotFound


StrategyRuntimeMatrixPayload = Callable[[Optional[str], int, bool], Dict[str, Any]]
StrategyRuntimeAccountPayload = Callable[[Optional[str], Optional[str], Optional[float], Optional[str], int], Dict[str, Any]]


def build_admin_strategy_runtime_router(
    matrix_payload: StrategyRuntimeMatrixPayload,
    trading_account_payload: StrategyRuntimeAccountPayload,
    replay_payload: StrategyRuntimeAccountPayload,
) -> APIRouter:
    router = APIRouter()

    def runtime_payload(payload_fn: StrategyRuntimeAccountPayload, **kwargs: Any) -> Dict[str, Any]:
        try:
            return payload_fn(**kwargs)
        except StrategyRuntimeModelNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/admin/strategy_runtime/matrix")
    def admin_strategy_runtime_matrix(
        as_of: Optional[str] = Query(default=None),
        limit_models: int = Query(default=80, ge=1, le=200),
        include_signals: bool = Query(default=True),
    ):
        return matrix_payload(
            as_of=as_of,
            limit_models=limit_models,
            include_signals=include_signals,
        )

    @router.get("/api/admin/trading_account")
    def admin_trading_account(
        as_of: Optional[str] = Query(default=None),
        model_id: Optional[str] = Query(default=None),
        initial_cash: Optional[float] = Query(default=None, ge=1000),
        start_date: Optional[str] = Query(default=None),
        limit: int = Query(default=1000, ge=1, le=2000),
    ):
        return runtime_payload(
            trading_account_payload,
            as_of=as_of,
            model_id=model_id,
            initial_cash=initial_cash,
            start_date=start_date,
            limit=limit,
        )

    @router.get("/api/admin/strategy_runtime/replay")
    def admin_strategy_runtime_replay(
        as_of: Optional[str] = Query(default=None),
        model_id: Optional[str] = Query(default=None),
        initial_cash: Optional[float] = Query(default=None, ge=1000),
        start_date: Optional[str] = Query(default=None),
        limit: int = Query(default=1000, ge=1, le=2000),
    ):
        return runtime_payload(
            replay_payload,
            as_of=as_of,
            model_id=model_id,
            initial_cash=initial_cash,
            start_date=start_date,
            limit=limit,
        )

    return router
