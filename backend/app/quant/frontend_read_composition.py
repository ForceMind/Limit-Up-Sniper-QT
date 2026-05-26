from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.quant.frontend_account import (
    frontend_followed_model_version,
    frontend_trading_account_payload,
)
from app.quant.frontend_news_read import FrontendNewsReadService, market_sentiment
from app.quant.frontend_payload_read_service import FrontendPayloadReadService
from app.quant.frontend_runtime_read_service import FrontendRuntimeReadService
from app.quant.frontend_signal_read_service import FrontendSignalReadService
from app.quant.frontend_snapshot import FrontendSnapshotReadService
from app.quant.frontend_static_response import FrontendStaticResponseService
from app.quant.frontend_strategy_daily import frontend_strategy_daily_payload
from app.quant.light_dashboard_read import LightDashboardReadService


@dataclass(frozen=True)
class FrontendReadServices:
    news: FrontendNewsReadService
    static_response: FrontendStaticResponseService
    light_dashboard: LightDashboardReadService
    payload: FrontendPayloadReadService
    snapshot: FrontendSnapshotReadService
    runtime: FrontendRuntimeReadService
    signal: FrontendSignalReadService
    market_sentiment: Callable[[Dict[str, Any]], Dict[str, Any]]


def build_frontend_read_services(
    *,
    frontend_dir: Callable[[], Path],
    data_dir: Callable[[], Path],
    app_version: Callable[[], str],
    cache_env_int: Callable[..., int],
    env_flag: Callable[[str, bool], bool],
    safe_float: Callable[[Any, float], float],
    copy_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    cache_get: Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]],
    cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    load_payload_cache: Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]],
    save_payload_cache: Callable[[str, Dict[str, Any], Dict[str, Any], int], Any],
    frontend_jobs_payload: Callable[[], Dict[str, Any]],
    light_status_payload: Callable[..., Dict[str, Any]],
    lightweight_news_feed: Callable[..., Dict[str, Any]],
    fallback_news_feed: Callable[..., Dict[str, Any]],
    stock_count: Callable[[], int],
    strategy_params: Callable[..., Dict[str, Any]],
    strategy_source: Callable[[], Dict[str, Any]],
    latest_price: Callable[[str, Optional[str]], Dict[str, Any]],
    run_frontend_payload_precompute: Callable[..., Dict[str, Any]],
    runtime_daily_payload: Callable[[Optional[str]], Dict[str, Any]],
    temporary_strategy_params: Callable[[Dict[str, Any]], Any],
    recommendations: Callable[..., Dict[str, Any]],
    daily_plan: Callable[..., Dict[str, Any]],
    append_log: Callable[[str, str, str, str, Dict[str, Any]], None],
    now: Callable[[], datetime],
    profile_read_service: Any,
    date_service: Any,
    account_read_service: Any,
    account_precompute_service: Any,
) -> FrontendReadServices:
    def cached_runtime_daily_payload(as_of: Optional[str]) -> Dict[str, Any]:
        clean_as_of = str(as_of or "").strip()[:10]
        ttl = cache_env_int(
            "QT_FRONT_STRATEGY_DAILY_CACHE_TTL_SECONDS",
            10,
            minimum=0,
            maximum=3600,
        )
        cache_parts = {"as_of": clean_as_of, "version": app_version()}
        if ttl > 0:
            cached = cache_get("front_strategy_daily_runtime", cache_parts, ttl)
            if cached is not None:
                return copy_payload(cached)
        payload = runtime_daily_payload(clean_as_of)
        payload = payload if isinstance(payload, dict) else {}
        if ttl > 0:
            return cache_set("front_strategy_daily_runtime", cache_parts, payload)
        return payload

    news = FrontendNewsReadService(
        lightweight_news_feed=lightweight_news_feed,
        fallback_news_feed=fallback_news_feed,
        append_log=append_log,
    )
    static_response = FrontendStaticResponseService(
        frontend_dir=frontend_dir,
    )
    light_dashboard = LightDashboardReadService(
        data_dir=data_dir,
        safe_float=safe_float,
        stock_count=stock_count,
        strategy_params=lambda: strategy_params(),
        strategy_source=strategy_source,
        now=now,
    )
    payload = FrontendPayloadReadService(
        safe_float=safe_float,
        followed_model_version=frontend_followed_model_version,
        cache_env_int=cache_env_int,
        env_flag=env_flag,
        replay_start_date=date_service.replay_start_date,
        latest_price=latest_price,
        load_payload_cache=load_payload_cache,
        save_payload_cache=save_payload_cache,
        run_frontend_payload_precompute=run_frontend_payload_precompute,
        append_log=append_log,
    )
    snapshot = FrontendSnapshotReadService(
        resolve_as_of=date_service.account_as_of,
        app_version=app_version,
        cache_env_int=cache_env_int,
        env_flag=env_flag,
        cache_get=cache_get,
        cache_set=cache_set,
        frontend_jobs_payload=frontend_jobs_payload,
        light_status_payload=light_status_payload,
        frontend_profile_context=profile_read_service.profile_context,
        frontend_payload_cache_parts=payload.cache_parts,
        frontend_light_news_feed=news.frontend_light_news_feed,
        safe_news_feed=news.safe_news_feed,
        market_sentiment=market_sentiment,
        strategy_daily_payload=lambda current_context, current_as_of, current_news_limit, news_stable: frontend_strategy_daily_payload(
            context=current_context,
            as_of=current_as_of,
            news_limit=current_news_limit,
            resolve_as_of=date_service.account_as_of,
            runtime_daily_payload=cached_runtime_daily_payload,
            news_payload=lambda **_kwargs: news_stable.get("news") if isinstance(news_stable.get("news"), dict) else {},
        ),
        trading_account_payload=lambda current_context, current_as_of, current_light: frontend_trading_account_payload(
            account_read_service.strategy_account(
                current_context,
                current_as_of,
                limit=80 if current_light else 500,
                record_period=not current_light,
                persist_derived=not current_light,
                hydrate_runtime_trades=not current_light,
            ),
            current_context,
        ),
        recommendations_plan_payload=payload.cached_recommendations_and_plan,
        attach_account_precompute=account_precompute_service.attach_runtime_precompute,
        copy_payload=copy_payload,
        append_log=lambda level, message, job, stage: append_log(level, message, job, stage, {}),
    )
    runtime = FrontendRuntimeReadService(
        profile_context=profile_read_service.profile_context,
        env_flag=env_flag,
        strategy_account=account_read_service.strategy_account,
        trading_account_payload=frontend_trading_account_payload,
        attach_account_precompute=account_precompute_service.attach_runtime_precompute,
        strategy_daily_payload=lambda **kwargs: frontend_strategy_daily_payload(**kwargs),
        resolve_as_of=date_service.account_as_of,
        runtime_daily_payload=cached_runtime_daily_payload,
        news_payload=lambda **kwargs: news.frontend_light_news_feed(**kwargs),
    )
    signal = FrontendSignalReadService(
        profile_context=profile_read_service.profile_context,
        resolve_as_of=date_service.account_as_of,
        replay_start_date=date_service.replay_start_date,
        payload_read_service=payload,
        temporary_strategy_params=temporary_strategy_params,
        recommendations=recommendations,
        daily_plan=daily_plan,
    )
    return FrontendReadServices(
        news=news,
        static_response=static_response,
        light_dashboard=light_dashboard,
        payload=payload,
        snapshot=snapshot,
        runtime=runtime,
        signal=signal,
        market_sentiment=market_sentiment,
    )
