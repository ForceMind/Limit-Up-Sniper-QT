from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from app.quant.admin_job_run_service import AdminJobRunService
from app.quant.admin_snapshot_service import AdminSnapshotService
from app.quant.capital_strategy import DEFAULT_FRONTEND_STRATEGY_ID, apply_capital_constraints
from app.quant.strategy_runtime_admin import AdminStrategyRuntimeReadService
from app.quant.system_control_service import SystemControlService
from app.quant.system_startup_service import SystemStartupService


@dataclass(frozen=True)
class AdminRuntimeServices:
    job_run: AdminJobRunService
    strategy_runtime: AdminStrategyRuntimeReadService
    snapshot: AdminSnapshotService
    system_startup: SystemStartupService
    system_control: SystemControlService


def build_admin_runtime_services(
    *,
    app_version: Callable[[], str],
    project_root: Callable[[], Path],
    cache_env_int: Callable[..., int],
    env_flag: Callable[[str, bool], bool],
    resolve_as_of: Callable[..., str],
    cache_get: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    runtime_status_service: Any,
    frontend_read_services: Any,
    frontend_strategy_models_service: Any,
    frontend_account_precompute_service: Any,
    admin_frontend_user_service: Any,
    data_coverage_service: Any,
    job_manager: Any,
    quant_engine: Any,
    strategy_evolution: Any,
    trade_notifier: Any,
    frontend_user_summary: Callable[[], Dict[str, Any]],
    access_logs: Callable[..., Dict[str, Any]],
    biying_status: Callable[[], Dict[str, Any]],
    lhb_status: Callable[[], Dict[str, Any]],
    ai_usage_summary: Callable[[], Dict[str, Any]],
    ai_failures: Callable[..., Dict[str, Any]],
    ai_records_feed: Callable[..., Dict[str, Any]],
    append_log: Callable[[str, str, str, str, Dict[str, Any]], None],
) -> AdminRuntimeServices:
    job_run = AdminJobRunService(
        job_manager=job_manager,
        frontend_account_precompute_service=frontend_account_precompute_service,
    )
    strategy_runtime = AdminStrategyRuntimeReadService(
        resolve_as_of=resolve_as_of,
        strategy_models_payload=lambda **kwargs: frontend_strategy_models_service.payload(**kwargs),
        model_signal_feed=lambda **kwargs: strategy_evolution.model_signal_feed(**kwargs),
        quant_engine=quant_engine,
        strategy_evolution_service=strategy_evolution,
        default_strategy_id=DEFAULT_FRONTEND_STRATEGY_ID,
        apply_capital_constraints=apply_capital_constraints,
    )
    snapshot = AdminSnapshotService(
        resolve_as_of=resolve_as_of,
        app_version=app_version,
        cache_env_int=cache_env_int,
        env_flag=env_flag,
        cache_get=cache_get,
        cache_set=cache_set,
        jobs_status_payload=runtime_status_service.jobs_status_payload,
        status_payload=runtime_status_service.status_payload,
        light_status_payload=runtime_status_service.light_status_payload,
        safe_news_feed=lambda **kwargs: frontend_read_services.news.safe_news_feed(**kwargs),
        full_news_feed=lambda **kwargs: quant_engine.news_feed(**kwargs),
        strategy_models_payload=lambda **kwargs: frontend_strategy_models_service.payload(**kwargs),
        admin_model_signal_feed=strategy_runtime.signal_feed_payload,
        strategy_runtime_overview_payload=lambda *args, **kwargs: strategy_runtime.overview_payload(*args, **kwargs),
        light_dashboard_payload=lambda *args, **kwargs: frontend_read_services.light_dashboard.light_dashboard_payload(
            *args,
            **kwargs,
        ),
        dashboard_payload=lambda **kwargs: quant_engine.dashboard(**kwargs),
        frontend_user_summary=frontend_user_summary,
        admin_frontend_user_summary=admin_frontend_user_service.list_users_payload,
        trading_account_payload=lambda **kwargs: strategy_runtime.trading_account_payload(**kwargs),
        data_coverage_payload=lambda **kwargs: data_coverage_service.payload(**kwargs),
        market_sentiment=frontend_read_services.market_sentiment,
        biying_status=biying_status,
        lhb_status=lhb_status,
        notification_status=lambda: trade_notifier.status(),
        evolution_status=lambda: strategy_evolution.status(),
        ai_usage_summary=ai_usage_summary,
        ai_failures=ai_failures,
        ai_records_feed=ai_records_feed,
        access_logs=access_logs,
        strategy_params=lambda: quant_engine.strategy_params(),
        strategy_source=lambda: quant_engine.strategy_source(),
        append_log=lambda level, message, job, stage: append_log(level, message, job, stage, {}),
    )
    system_startup = SystemStartupService(
        quant_engine=quant_engine,
        job_manager=job_manager,
    )
    system_control = SystemControlService(
        project_root=project_root,
        env_flag=env_flag,
        notifier=trade_notifier,
        append_log=append_log,
    )
    return AdminRuntimeServices(
        job_run=job_run,
        strategy_runtime=strategy_runtime,
        snapshot=snapshot,
        system_startup=system_startup,
        system_control=system_control,
    )
