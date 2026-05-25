from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Query


DashboardPayload = Callable[[Optional[str], bool], Dict[str, Any]]
RecommendationsPayload = Callable[[Optional[str], int, int], Dict[str, Any]]
DailyPlanPayload = Callable[[Optional[str], Optional[str], int], Dict[str, Any]]
StrategyParamsPayload = Callable[[], Dict[str, Any]]
StrategyParamsUpdatePayload = Callable[[Dict[str, Any]], Dict[str, Any]]
EventsPayload = Callable[[Optional[str], int], Dict[str, Any]]
NewsPayload = Callable[[Optional[str], int, bool, Optional[str], Optional[str], Optional[str]], Dict[str, Any]]
CorrelationPayload = Callable[[Optional[str], int], Dict[str, Any]]
PortfolioPayload = Callable[[Optional[str]], Dict[str, Any]]
TradingAccountPayload = Callable[[Optional[str], int], Dict[str, Any]]
RunPayload = Callable[[Optional[str], bool], Dict[str, Any]]
NewsHistoryPayload = Callable[[int], Dict[str, Any]]


def build_quant_basic_router(
    *,
    dashboard_payload: DashboardPayload,
    recommendations_payload: RecommendationsPayload,
    daily_plan_payload: DailyPlanPayload,
    strategy_params_payload: StrategyParamsPayload,
    strategy_params_update_payload: StrategyParamsUpdatePayload,
    strategy_params_reset_payload: StrategyParamsPayload,
    events_payload: EventsPayload,
    news_payload: NewsPayload,
    correlation_payload: CorrelationPayload,
    portfolio_payload: PortfolioPayload,
    trading_account_payload: TradingAccountPayload,
    run_payload: RunPayload,
    news_history_payload: NewsHistoryPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/quant/dashboard")
    def quant_dashboard(as_of: Optional[str] = Query(default=None), light: bool = Query(default=False)):
        return dashboard_payload(as_of, light)

    @router.get("/api/quant/recommendations")
    def quant_recommendations(
        as_of: Optional[str] = Query(default=None),
        lookback_days: int = Query(default=2, ge=1, le=20),
        top_n: int = Query(default=30, ge=1, le=100),
    ):
        return recommendations_payload(as_of, lookback_days, top_n)

    @router.get("/api/quant/daily_plan")
    def quant_daily_plan(
        as_of: Optional[str] = Query(default=None),
        start_date: Optional[str] = Query(default=None),
        limit_days: int = Query(default=80, ge=1, le=500),
    ):
        return daily_plan_payload(as_of, start_date, limit_days)

    @router.get("/api/quant/strategy_params")
    def quant_strategy_params():
        return strategy_params_payload()

    @router.post("/api/quant/strategy_params")
    def quant_update_strategy_params(payload: Dict[str, Any] = Body(default_factory=dict)):
        return strategy_params_update_payload(payload)

    @router.post("/api/quant/strategy_params/reset")
    def quant_reset_strategy_params():
        return strategy_params_reset_payload()

    @router.get("/api/quant/events")
    def quant_events(as_of: Optional[str] = Query(default=None), limit: int = Query(default=200, ge=1, le=1000)):
        return events_payload(as_of, limit)

    @router.get("/api/quant/news")
    def quant_news(
        as_of: Optional[str] = Query(default=None),
        limit: int = Query(default=120, ge=1, le=1000),
        fallback_latest: bool = Query(default=True),
        source: Optional[str] = Query(default=None),
        keyword: Optional[str] = Query(default=None),
        code: Optional[str] = Query(default=None),
    ):
        return news_payload(as_of, limit, fallback_latest, source, keyword, code)

    @router.get("/api/quant/correlation")
    def quant_correlation(as_of: Optional[str] = Query(default=None), hold_days: int = Query(default=3, ge=1, le=20)):
        return correlation_payload(as_of, hold_days)

    @router.get("/api/quant/portfolio")
    def quant_portfolio(as_of: Optional[str] = Query(default=None)):
        return portfolio_payload(as_of)

    @router.get("/api/quant/trading_account")
    def quant_trading_account(
        as_of: Optional[str] = Query(default=None),
        limit: int = Query(default=500, ge=1, le=2000),
    ):
        return trading_account_payload(as_of, limit)

    @router.post("/api/quant/run")
    def quant_run(as_of: Optional[str] = Query(default=None), calibrate: bool = Query(default=True)):
        return run_payload(as_of, calibrate)

    @router.get("/api/news_history")
    def news_history(limit: int = Query(default=200, ge=1, le=2000)):
        return news_history_payload(limit)

    return router
