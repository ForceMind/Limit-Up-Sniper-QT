from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo


CN_TZ = ZoneInfo("Asia/Shanghai")


def timestamp_to_cn_iso(value: float) -> str:
    return datetime.fromtimestamp(float(value), CN_TZ).isoformat(timespec="seconds")


def build_scheduler_status(
    *,
    last_tick_at: str,
    market_open: bool,
    trading_day: bool,
    next_runs: Mapping[str, float],
    intervals: Mapping[str, int],
    data_backfill: Mapping[str, Any],
    strategy_daily_refresh: Mapping[str, Any],
    strategy_replay: Mapping[str, Any],
    strategy_evolution: Mapping[str, Any],
    frontend_payload_precompute: Mapping[str, Any],
    research_tasks_manual_only: bool,
) -> dict[str, Any]:
    return {
        "enabled": True,
        "status": "running",
        "last_tick_at": last_tick_at,
        "market_open": bool(market_open),
        "trading_day": bool(trading_day),
        "next_news_fetch_at": timestamp_to_cn_iso(next_runs["news_fetch"]),
        "news_interval_seconds": intervals["news"],
        "next_ai_analysis_at": timestamp_to_cn_iso(next_runs["ai_analysis"]),
        "ai_interval_seconds": intervals["ai"],
        "next_market_sync_at": timestamp_to_cn_iso(next_runs["market_sync"]),
        "market_interval_seconds": intervals["market"],
        "next_kline_fill_at": timestamp_to_cn_iso(next_runs["kline_fill"]),
        "kline_fill_interval_seconds": intervals["kline_fill"],
        "next_lhb_sync_at": timestamp_to_cn_iso(next_runs["lhb_sync"]),
        "lhb_sync_interval_seconds": intervals["lhb_sync"],
        "data_backfill_start_date": data_backfill["start_date"],
        "data_backfill_end_date": data_backfill["end_date"],
        "data_backfill_max_codes": data_backfill["max_codes"],
        "next_strategy_daily_refresh_at": timestamp_to_cn_iso(next_runs["strategy_daily_refresh"]),
        "strategy_daily_refresh_interval_seconds": intervals["strategy_daily_refresh"],
        "strategy_daily_refresh_initial_delay_seconds": strategy_daily_refresh["initial_delay_seconds"],
        "strategy_daily_refresh_enabled": bool(strategy_daily_refresh["enabled"]),
        "strategy_daily_refresh_process_enabled": bool(strategy_daily_refresh["process_enabled"]),
        "strategy_daily_refresh_waits_for_heavy_jobs": bool(strategy_daily_refresh.get("waits_for_heavy_jobs", True)),
        "strategy_daily_refresh_mode": strategy_daily_refresh["mode"],
        "next_trade_cycle_at": timestamp_to_cn_iso(next_runs["trade_cycle"]),
        "trade_interval_seconds": intervals["trade"],
        "next_strategy_replay_at": timestamp_to_cn_iso(next_runs["strategy_replay"]),
        "strategy_replay_interval_seconds": intervals["strategy_replay"],
        "strategy_replay_start_date": strategy_replay["start_date"],
        "strategy_replay_batch_days": strategy_replay["batch_days"],
        "strategy_replay_enabled": bool(strategy_replay["enabled"]),
        "strategy_replay_process_enabled": bool(strategy_replay["process_enabled"]),
        "next_strategy_evolution_at": timestamp_to_cn_iso(next_runs["strategy_evolution"]),
        "strategy_evolution_interval_seconds": intervals["strategy_evolution"],
        "strategy_evolution_start_date": strategy_evolution["start_date"],
        "strategy_evolution_generations": strategy_evolution["generations"],
        "strategy_evolution_population_size": strategy_evolution["population_size"],
        "strategy_evolution_enabled": bool(strategy_evolution["enabled"]),
        "strategy_evolution_process_enabled": bool(strategy_evolution["process_enabled"]),
        "research_tasks_manual_only": bool(research_tasks_manual_only),
        "next_frontend_payload_precompute_at": timestamp_to_cn_iso(next_runs["frontend_payload_precompute"]),
        "frontend_payload_precompute_interval_seconds": intervals["frontend_payload_precompute"],
        "frontend_payload_precompute_initial_delay_seconds": frontend_payload_precompute["initial_delay_seconds"],
        "frontend_payload_precompute_limit_users": frontend_payload_precompute["limit_users"],
        "frontend_payload_precompute_enabled": bool(frontend_payload_precompute["enabled"]),
    }
