from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query


NewsFetchPayload = Callable[[int, int, int, bool, bool], Dict[str, Any]]
MarketSyncPayload = Callable[[Optional[str], str, int, bool, bool, bool, bool], Dict[str, Any]]
AiAnalyzePayload = Callable[[Optional[str], int, int, bool, bool], Dict[str, Any]]
TradingRunPayload = Callable[[Optional[str], bool, bool, bool], Dict[str, Any]]
StrategyDailyRefreshPayload = Callable[[Optional[str], str, bool, bool], Dict[str, Any]]
StrategyReplayPayload = Callable[[Optional[str], Optional[str], str, Optional[int], bool, bool, bool], Dict[str, Any]]
FrontendPayloadPrecomputePayload = Callable[
    [Optional[str], Optional[str], int, bool, bool, bool, int, int, int, Optional[int]],
    Dict[str, Any],
]
FrontendAccountPrecomputePayload = Callable[
    [Optional[str], Optional[str], int, int, bool, bool, bool, Optional[bool]],
    Dict[str, Any],
]
SystemStartupPayload = Callable[
    [Optional[str], Optional[str], Optional[str], int, int, int, int, bool, bool, bool, bool],
    Dict[str, Any],
]


def build_admin_job_runs_router(
    news_fetch_payload: NewsFetchPayload,
    market_sync_payload: MarketSyncPayload,
    ai_analyze_payload: AiAnalyzePayload,
    trading_run_payload: TradingRunPayload,
    strategy_daily_refresh_payload: StrategyDailyRefreshPayload,
    strategy_replay_payload: StrategyReplayPayload,
    frontend_payload_precompute_payload: FrontendPayloadPrecomputePayload,
    frontend_account_precompute_payload: FrontendAccountPrecomputePayload,
    system_startup_payload: SystemStartupPayload,
    *,
    news_fetch_process_default: bool = True,
    market_sync_process_default: bool = True,
    ai_analysis_process_default: bool = True,
    trade_cycle_process_default: bool = True,
    strategy_daily_refresh_process_default: bool = True,
    heavy_job_process_default: bool = True,
    frontend_payload_process_default: bool = True,
    frontend_account_process_default: bool = True,
    system_startup_process_default: bool = True,
    system_startup_run_strategy_replay_default: bool = False,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/jobs/news/fetch")
    def jobs_news_fetch(
        hours: int = Query(default=12, ge=1, le=168),
        pages: int = Query(default=5, ge=1, le=30),
        page_size: int = Query(default=20, ge=10, le=100),
        background: bool = Query(default=True),
        process: bool = Query(default=news_fetch_process_default),
    ):
        return news_fetch_payload(hours, pages, page_size, background, process)

    @router.post("/api/jobs/market/sync")
    def jobs_market_sync(
        date: Optional[str] = Query(default=None),
        source: str = Query(default="auto"),
        max_codes: int = Query(default=80, ge=1, le=500),
        force: bool = Query(default=False),
        include_latest: bool = Query(default=True),
        background: bool = Query(default=True),
        process: bool = Query(default=market_sync_process_default),
    ):
        return market_sync_payload(date, source, max_codes, force, include_latest, background, process)

    @router.post("/api/jobs/ai/analyze")
    def jobs_ai_analyze(
        as_of: Optional[str] = Query(default=None),
        max_items: int = Query(default=8, ge=1, le=50),
        batch_size: int = Query(default=4, ge=1, le=10),
        background: bool = Query(default=True),
        process: bool = Query(default=ai_analysis_process_default),
    ):
        return ai_analyze_payload(as_of, max_items, batch_size, background, process)

    @router.post("/api/jobs/trading/run")
    def jobs_trading_run(
        date: Optional[str] = Query(default=None),
        notify: bool = Query(default=True),
        background: bool = Query(default=True),
        process: bool = Query(default=trade_cycle_process_default),
    ):
        return trading_run_payload(date, notify, background, process)

    @router.post("/api/jobs/strategy/daily_refresh")
    def jobs_strategy_daily_refresh(
        date: Optional[str] = Query(default=None),
        mode: str = Query(default="daily"),
        background: bool = Query(default=True),
        process: bool = Query(default=strategy_daily_refresh_process_default),
    ):
        return strategy_daily_refresh_payload(date, mode, background, process)

    @router.post("/api/jobs/strategy/replay")
    def jobs_strategy_replay(
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        mode: str = Query(default="intraday"),
        batch_days: Optional[int] = Query(default=None, ge=1, le=366),
        use_cursor: bool = Query(default=False),
        background: bool = Query(default=True),
        process: bool = Query(default=heavy_job_process_default),
    ):
        return strategy_replay_payload(start_date, end_date, mode, batch_days, use_cursor, background, process)

    @router.post("/api/jobs/frontend/precompute")
    def jobs_frontend_payload_precompute(
        as_of: Optional[str] = Query(default=None),
        usernames: Optional[str] = Query(default=None),
        limit_users: int = Query(default=8, ge=1, le=500),
        force: bool = Query(default=False),
        background: bool = Query(default=True),
        process: bool = Query(default=frontend_payload_process_default),
        lookback_days: int = Query(default=2, ge=1, le=20),
        top_n: int = Query(default=30, ge=1, le=100),
        limit_days: int = Query(default=30, ge=1, le=500),
        max_seconds: Optional[int] = Query(default=None, ge=0, le=86400),
    ):
        return frontend_payload_precompute_payload(
            as_of,
            usernames,
            limit_users,
            force,
            background,
            process,
            lookback_days,
            top_n,
            limit_days,
            max_seconds,
        )

    @router.post("/api/jobs/frontend/account_precompute")
    def jobs_frontend_account_precompute(
        as_of: Optional[str] = Query(default=None),
        usernames: Optional[str] = Query(default=None),
        limit_users: int = Query(default=50, ge=1, le=500),
        limit: int = Query(default=160, ge=1, le=2000),
        force: bool = Query(default=False),
        background: bool = Query(default=True),
        process: bool = Query(default=frontend_account_process_default),
        drain_queue: Optional[bool] = Query(default=None),
    ):
        return frontend_account_precompute_payload(
            as_of,
            usernames,
            limit_users,
            limit,
            force,
            background,
            process,
            drain_queue,
        )

    @router.post("/api/jobs/daily/run")
    def jobs_daily_run(
        date: Optional[str] = Query(default=None),
        notify: bool = Query(default=True),
        background: bool = Query(default=True),
        process: bool = Query(default=trade_cycle_process_default),
    ):
        return trading_run_payload(date, notify, background, process)

    @router.post("/api/admin/system/startup")
    def admin_system_startup(
        date: Optional[str] = Query(default=None),
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        news_hours: int = Query(default=24, ge=1, le=168),
        news_pages: int = Query(default=8, ge=1, le=30),
        ai_items: int = Query(default=20, ge=1, le=80),
        market_codes: int = Query(default=200, ge=1, le=1000),
        notify: bool = Query(default=True),
        background: bool = Query(default=True),
        process: bool = Query(default=system_startup_process_default),
        run_strategy_replay: bool = Query(default=system_startup_run_strategy_replay_default),
    ):
        return system_startup_payload(
            date,
            start_date,
            end_date,
            news_hours,
            news_pages,
            ai_items,
            market_codes,
            notify,
            background,
            process,
            run_strategy_replay,
        )

    return router
