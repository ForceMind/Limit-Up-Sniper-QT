from __future__ import annotations

from typing import Any, Dict, Optional


class AdminJobRunService:
    def __init__(
        self,
        *,
        job_manager: Any,
        frontend_account_precompute_service: Any,
    ) -> None:
        self._job_manager = job_manager
        self._frontend_account_precompute_service = frontend_account_precompute_service

    def news_fetch_payload(
        self,
        hours: int,
        pages: int,
        page_size: int,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_news_fetch(
            hours=hours,
            pages=pages,
            page_size=page_size,
            refresh_events=True,
            background=background,
            process=process,
        )

    def market_sync_payload(
        self,
        date: Optional[str],
        source: str,
        max_codes: int,
        force: bool,
        include_latest: bool,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_market_sync(
            date=date,
            source=source,
            max_codes=max_codes,
            force=force,
            include_latest=include_latest,
            background=background,
            process=process,
        )

    def ai_analyze_payload(
        self,
        as_of: Optional[str],
        max_items: int,
        batch_size: int,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_ai_analysis(
            as_of=as_of,
            max_items=max_items,
            batch_size=batch_size,
            background=background,
            process=process,
        )

    def trading_run_payload(
        self,
        date: Optional[str],
        notify: bool,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_trade_cycle(
            date=date,
            notify=notify,
            background=background,
            process=process,
        )

    def strategy_daily_refresh_payload(
        self,
        date: Optional[str],
        mode: str,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_strategy_daily_refresh(
            date=date,
            mode=mode,
            background=background,
            process=process,
        )

    def strategy_replay_payload(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        batch_days: Optional[int],
        use_cursor: bool,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_strategy_replay(
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            background=background,
            batch_days=batch_days,
            use_cursor=use_cursor,
            process=process,
        )

    def frontend_payload_precompute_payload(
        self,
        as_of: Optional[str],
        usernames: Optional[str],
        limit_users: int,
        force: bool,
        background: bool,
        process: bool,
        lookback_days: int,
        top_n: int,
        limit_days: int,
        max_seconds: Optional[int],
    ) -> Dict[str, Any]:
        return self._job_manager.run_frontend_payload_precompute(
            as_of=as_of,
            usernames=usernames,
            limit_users=limit_users,
            force=force,
            background=background,
            process=process,
            lookback_days=lookback_days,
            top_n=top_n,
            limit_days=limit_days,
            max_seconds=max_seconds,
        )

    def frontend_account_precompute_payload(
        self,
        as_of: Optional[str],
        usernames: Optional[str],
        limit_users: int,
        limit: int,
        force: bool,
        background: bool,
        process: bool,
        drain_queue: Optional[bool],
    ) -> Dict[str, Any]:
        return self._frontend_account_precompute_service.run_runtime_job_payload(
            as_of=as_of,
            usernames=usernames,
            limit_users=limit_users,
            limit=limit,
            force=force,
            background=background,
            process=process,
            drain_queue=drain_queue,
        )
