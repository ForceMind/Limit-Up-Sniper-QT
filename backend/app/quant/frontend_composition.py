from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from app.quant.frontend_account import FrontendAccountReadService
from app.quant.frontend_account_precompute_service import FrontendAccountPrecomputeService
from app.quant.frontend_date_service import FrontendDateService
from app.quant.frontend_follow import FrontendProfileReadService
from app.quant.frontend_follow_period_service import FrontendFollowPeriodService
from app.quant.frontend_profile_update_service import FrontendProfileUpdateService
from app.quant.frontend_strategy_models import FrontendStrategyModelsService
from app.quant.frontend_user_lifecycle_service import AdminFrontendUserService, FrontendUserLifecycleService
from app.quant.runtime_policy import target_strategy_count


@dataclass(frozen=True)
class FrontendBaseServices:
    account_precompute: FrontendAccountPrecomputeService
    follow_period: FrontendFollowPeriodService
    profile_update: FrontendProfileUpdateService
    user_lifecycle: FrontendUserLifecycleService
    admin_frontend_user: AdminFrontendUserService
    date: FrontendDateService
    account_read: FrontendAccountReadService
    strategy_models: FrontendStrategyModelsService
    profile_read: FrontendProfileReadService


def build_frontend_base_services(
    *,
    data_dir: Callable[[], Path],
    app_version: Callable[[], str],
    account_replay_days: Callable[[], int],
    latest_data_date: Callable[[], str],
    first_data_date: Callable[[], str],
    env_flag: Callable[[str, bool], bool],
    env_float: Callable[[str, float], float],
    cache_ttl_seconds: Callable[[], int],
    cache_get: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    clear_memory_cache: Callable[[], None],
    request_username: Callable[[Any], str],
    frontend_user_profile: Callable[[str], Dict[str, Any]],
    frontend_user_summary: Callable[[], Dict[str, Any]],
    create_frontend_user: Callable[[Dict[str, Any], Any], Dict[str, Any]],
    update_frontend_user: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    reset_frontend_user_password: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    set_frontend_user_disabled: Callable[..., Dict[str, Any]],
    delete_frontend_user: Callable[[str], Dict[str, Any]],
    update_frontend_user_profile: Callable[[str, Dict[str, Any]], Any],
    resolve_profile_updates: Callable[..., tuple[Dict[str, Any], Dict[str, Any]]],
    job_manager: Any,
    strategy_evolution: Any,
    quant_engine: Any,
) -> FrontendBaseServices:
    account_precompute = FrontendAccountPrecomputeService(
        data_dir=data_dir,
        env_flag=env_flag,
        env_float=env_float,
        job_manager=job_manager,
    )
    follow_period = FrontendFollowPeriodService(
        env_flag=env_flag,
        append_log=lambda level, message, job, stage, payload: job_manager._append_log(
            level,
            message,
            job=job,
            stage=stage,
            payload=payload,
        ),
        record_follow_period=lambda *args, **kwargs: strategy_evolution.record_user_follow_period(*args, **kwargs),
    )
    profile_update = FrontendProfileUpdateService()
    user_lifecycle = FrontendUserLifecycleService()
    date = FrontendDateService(
        latest_data_date=latest_data_date,
        first_data_date=first_data_date,
        account_replay_days=account_replay_days,
    )
    account_read = FrontendAccountReadService(
        replay_days=account_replay_days,
        resolve_as_of=date.account_as_of,
        follow_start_date=date.follow_start_date,
        record_follow_period=lambda *args, **kwargs: follow_period.record_user_follow_period(*args, **kwargs),
        load_user_follow_account=lambda *args, **kwargs: strategy_evolution.load_user_follow_account(*args, **kwargs),
        load_runtime_account=lambda *args, **kwargs: strategy_evolution.load_runtime_account(*args, **kwargs),
        load_account_cache=lambda *args, **kwargs: strategy_evolution.load_account_cache(*args, **kwargs),
        save_account_cache=lambda *args, **kwargs: strategy_evolution.save_account_cache(*args, **kwargs),
        save_user_follow_account=lambda *args, **kwargs: strategy_evolution.save_user_follow_account(*args, **kwargs),
        model_loader=lambda model_id: strategy_evolution.model(
            str(model_id or "active").strip() or "active",
            include_records=True,
        )
        or {},
        account_from_trades=lambda *args, **kwargs: quant_engine.account_from_trades(*args, **kwargs),
        temporary_strategy_params=lambda params: quant_engine.temporary_strategy_params(params),
        walk_forward=lambda *args, **kwargs: quant_engine.walk_forward(*args, **kwargs),
        trading_account=lambda *args, **kwargs: quant_engine.trading_account(*args, **kwargs),
        allow_model_records_fallback=lambda: env_flag("QT_FRONT_ACCOUNT_MODEL_RECORDS_FALLBACK", False),
    )
    strategy_models = FrontendStrategyModelsService(
        app_version=app_version,
        cache_ttl_seconds=cache_ttl_seconds,
        cache_get=cache_get,
        cache_set=cache_set,
        strategy_params=quant_engine.strategy_params,
        strategy_source=quant_engine.strategy_source,
        catalog_payload=strategy_evolution.models,
        runtime_model_summaries=strategy_evolution.runtime_model_summaries,
        target_strategy_count=target_strategy_count,
    )
    profile_read = FrontendProfileReadService(
        request_username=request_username,
        frontend_user_profile=frontend_user_profile,
        strategy_models_payload=lambda *args, **kwargs: strategy_models.payload(*args, **kwargs),
        model_lookup=lambda *args, **kwargs: strategy_evolution.model(*args, **kwargs),
        active_strategy_model=strategy_models.active_model,
        strategy_params=lambda params=None: quant_engine.strategy_params(params),
        update_user_profile=update_frontend_user_profile,
    )
    profile_update.configure_route(
        request_username=request_username,
        load_profile=frontend_user_profile,
        resolve_updates=resolve_profile_updates,
        strategy_models_payload=lambda *args, **kwargs: strategy_models.payload(*args, **kwargs),
        model_lookup=lambda *args, **kwargs: strategy_evolution.model(*args, **kwargs),
        save_profile=update_frontend_user_profile,
        profile_context=profile_read.profile_context,
        follow_period_reason=lambda previous, current: follow_period.follow_period_reason(previous, current),
        queue_follow_period=lambda *args, **kwargs: follow_period.queue_user_follow_period_record(*args, **kwargs),
        queue_account_precompute=lambda *args, **kwargs: account_precompute.queue_runtime_user(*args, **kwargs),
    )
    account_precompute.configure_runtime(
        resolve_as_of=date.account_as_of,
        frontend_user_summary=frontend_user_summary,
        profile_context=profile_read.profile_context_for_username,
        strategy_account=account_read.strategy_account,
    )
    admin_frontend_user = AdminFrontendUserService(
        lifecycle=user_lifecycle,
        list_users=frontend_user_summary,
        create_user=create_frontend_user,
        update_user=update_frontend_user,
        reset_password=reset_frontend_user_password,
        set_disabled=set_frontend_user_disabled,
        delete_user=delete_frontend_user,
        load_profile=frontend_user_profile,
        clear_account_cache=account_read.clear_memory_cache,
        clear_memory_cache=clear_memory_cache,
        record_follow_period=lambda *args, **kwargs: follow_period.record_user_follow_period(*args, **kwargs),
        follow_period_reason=lambda previous, current: follow_period.follow_period_reason(previous, current),
        user_follow_diagnostics=lambda username, profile: strategy_evolution.user_follow_diagnostics(username, profile),
        queue_account_precompute=account_precompute.queue_runtime_user,
    )
    return FrontendBaseServices(
        account_precompute=account_precompute,
        follow_period=follow_period,
        profile_update=profile_update,
        user_lifecycle=user_lifecycle,
        admin_frontend_user=admin_frontend_user,
        date=date,
        account_read=account_read,
        strategy_models=strategy_models,
        profile_read=profile_read,
    )
