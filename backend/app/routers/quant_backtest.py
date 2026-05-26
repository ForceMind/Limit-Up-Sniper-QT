from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query


BacktestPayload = Callable[
    [
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[float],
        Optional[int],
        int,
        int,
        bool,
        bool,
        bool,
        bool,
        bool,
    ],
    Dict[str, Any],
]


def build_quant_backtest_router(
    *,
    backtest_payload: BacktestPayload,
    defer_default: bool,
    process_default: bool,
) -> APIRouter:
    router = APIRouter()

    def _run_quant_backtest(
        as_of: Optional[str] = Query(default=None),
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        initial_cash: Optional[float] = Query(default=None, gt=0),
        max_positions: Optional[int] = Query(default=None, ge=1, le=20),
        hold_days: int = Query(default=3, ge=1, le=20),
        top_n: int = Query(default=5, ge=1, le=20),
        auto_fill: bool = Query(default=True),
        force: bool = Query(default=False),
        defer: bool = Query(default=defer_default),
        process: bool = Query(default=process_default),
        manual: bool = Query(default=False),
    ):
        return backtest_payload(
            as_of,
            start_date,
            end_date,
            initial_cash,
            max_positions,
            hold_days,
            top_n,
            auto_fill,
            force,
            defer,
            process,
            manual,
        )

    router.add_api_route("/api/quant/backtest", _run_quant_backtest, methods=["GET"])
    router.add_api_route("/api/quant/backtest", _run_quant_backtest, methods=["POST"])
    return router
