from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query, Request


FrontendRecommendationsPayload = Callable[[Request, Optional[str], int, int, bool, bool], Dict[str, Any]]
FrontendDailyPlanPayload = Callable[[Request, Optional[str], Optional[str], int, bool, bool], Dict[str, Any]]


def build_frontend_signal_router(
    recommendations_payload: FrontendRecommendationsPayload,
    daily_plan_payload: FrontendDailyPlanPayload,
    payload_defer_default: bool = True,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/front/recommendations")
    def frontend_recommendations(
        request: Request,
        as_of: Optional[str] = Query(default=None),
        lookback_days: int = Query(default=2, ge=1, le=20),
        top_n: int = Query(default=30, ge=1, le=100),
        force: bool = Query(default=False),
        defer: bool = Query(default=payload_defer_default),
    ):
        return recommendations_payload(
            request,
            as_of,
            lookback_days,
            top_n,
            force,
            defer,
        )

    @router.get("/api/front/daily_plan")
    def frontend_daily_plan(
        request: Request,
        as_of: Optional[str] = Query(default=None),
        start_date: Optional[str] = Query(default=None),
        limit_days: int = Query(default=120, ge=1, le=500),
        force: bool = Query(default=False),
        defer: bool = Query(default=payload_defer_default),
    ):
        return daily_plan_payload(
            request,
            as_of,
            start_date,
            limit_days,
            force,
            defer,
        )

    return router
