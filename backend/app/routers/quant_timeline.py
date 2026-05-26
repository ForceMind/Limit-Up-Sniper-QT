from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query


TimelinePayload = Callable[
    [
        Optional[str],
        Optional[str],
        Optional[str],
        Optional[float],
        Optional[int],
        Optional[int],
        Optional[int],
        bool,
        bool,
        bool,
        bool,
        bool,
        bool,
        bool,
    ],
    Dict[str, Any],
]


def build_quant_timeline_router(
    *,
    timeline_payload: TimelinePayload,
    defer_default: bool,
    process_default: bool,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/quant/timeline")
    def quant_timeline(
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        model_id: Optional[str] = Query(default=None),
        initial_cash: Optional[float] = Query(default=None, gt=0),
        max_positions: Optional[int] = Query(default=None, ge=1, le=20),
        hold_days: Optional[int] = Query(default=None, ge=1, le=20),
        top_n: Optional[int] = Query(default=None, ge=1, le=20),
        auto_fill: bool = Query(default=True),
        force: bool = Query(default=False),
        defer: bool = Query(default=defer_default),
        process: bool = Query(default=process_default),
        manual: bool = Query(default=False),
    ):
        return timeline_payload(
            start_date,
            end_date,
            model_id,
            initial_cash,
            max_positions,
            hold_days,
            top_n,
            False,
            True,
            auto_fill,
            force,
            defer,
            process,
            manual,
        )

    @router.get("/api/quant/intraday_timeline")
    def quant_intraday_timeline(
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        model_id: Optional[str] = Query(default=None),
        initial_cash: Optional[float] = Query(default=None, gt=0),
        max_positions: Optional[int] = Query(default=None, ge=1, le=20),
        hold_days: Optional[int] = Query(default=None, ge=1, le=20),
        top_n: Optional[int] = Query(default=None, ge=1, le=20),
        use_daily_fallback: bool = Query(default=True),
        auto_fill: bool = Query(default=True),
        force: bool = Query(default=False),
        defer: bool = Query(default=defer_default),
        process: bool = Query(default=process_default),
        manual: bool = Query(default=False),
    ):
        return timeline_payload(
            start_date,
            end_date,
            model_id,
            initial_cash,
            max_positions,
            hold_days,
            top_n,
            True,
            use_daily_fallback,
            auto_fill,
            force,
            defer,
            process,
            manual,
        )

    return router
