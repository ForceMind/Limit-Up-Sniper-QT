from __future__ import annotations

from datetime import datetime
from typing import Any, MutableMapping
from zoneinfo import ZoneInfo

from app.quant.frontend_composition import build_frontend_base_services
from app.quant.frontend_follow import request_username_from_state
from app.quant.frontend_read_composition import build_frontend_read_services
from app.quant.memory_payload_cache import MemoryPayloadCache
from app.quant.news_repository import lightweight_news_feed
from app.quant.security import (
    admin_create_frontend_user,
    admin_delete_frontend_user,
    admin_reset_frontend_user_password,
    admin_set_frontend_user_disabled,
    admin_update_frontend_user,
)


def build_frontend_base_partition(
    state: MutableMapping[str, Any],
    memory_payload_cache_service: MemoryPayloadCache,
    server_read_status_service: Any,
) -> Any:
    get = state.__getitem__
    frontend_base_services = build_frontend_base_services(
        data_dir=lambda: get("DATA_DIR"),
        app_version=lambda: get("APP_VERSION"),
        account_replay_days=lambda: get("_FRONTEND_ACCOUNT_REPLAY_DAYS"),
        latest_data_date=server_read_status_service.latest_data_date,
        first_data_date=server_read_status_service.first_data_date,
        env_flag=lambda name, default=False: get("_APP_CONFIG").env_flag(name, default),
        env_float=lambda name, default: get("_APP_CONFIG").env_float(name, default),
        cache_ttl_seconds=lambda: get("cache_env_int")(
            "QT_STRATEGY_MODELS_CACHE_TTL_SECONDS",
            60,
            minimum=0,
            maximum=3600,
        ),
        cache_get=memory_payload_cache_service.get,
        cache_set=memory_payload_cache_service.set,
        clear_memory_cache=memory_payload_cache_service.clear,
        request_username=request_username_from_state,
        frontend_user_profile=lambda username: get("frontend_user_profile")(username),
        frontend_user_summary=lambda: get("frontend_user_summary")(),
        create_frontend_user=lambda payload, request: admin_create_frontend_user(payload, request),
        update_frontend_user=lambda username, payload: admin_update_frontend_user(username, payload),
        reset_frontend_user_password=lambda username, payload: admin_reset_frontend_user_password(username, payload),
        set_frontend_user_disabled=lambda *args: admin_set_frontend_user_disabled(*args),
        delete_frontend_user=lambda username: admin_delete_frontend_user(username),
        update_frontend_user_profile=lambda username, update: get("update_frontend_user_profile")(username, update),
        resolve_profile_updates=get("_resolve_front_profile_updates"),
        job_manager=get("job_manager"),
        strategy_evolution=get("strategy_evolution"),
        quant_engine=get("quant_engine"),
    )
    frontend_account_precompute_service = frontend_base_services.account_precompute
    state.update(
        {
            "_FRONTEND_BASE_SERVICES": frontend_base_services,
            "_FRONTEND_ACCOUNT_PRECOMPUTE_SERVICE": frontend_account_precompute_service,
            "_FRONTEND_ACCOUNT_PRECOMPUTE_QUEUE_LOCK": frontend_account_precompute_service.queue_lock,
            "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_LOCK": frontend_account_precompute_service.async_lock,
            "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING": frontend_account_precompute_service.async_pending,
            "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_TASKS": frontend_account_precompute_service.async_tasks,
            "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKER_LOCK": frontend_account_precompute_service.async_worker_lock,
            "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED": frontend_account_precompute_service.async_workers_started,
            "_FRONTEND_FOLLOW_PERIOD_SERVICE": frontend_base_services.follow_period,
            "_FRONTEND_PROFILE_UPDATE_SERVICE": frontend_base_services.profile_update,
            "_FRONTEND_USER_LIFECYCLE_SERVICE": frontend_base_services.user_lifecycle,
            "_ADMIN_FRONTEND_USER_SERVICE": frontend_base_services.admin_frontend_user,
            "_FRONTEND_DATE_SERVICE": frontend_base_services.date,
            "_FRONTEND_ACCOUNT_READ_SERVICE": frontend_base_services.account_read,
            "_FRONTEND_STRATEGY_MODELS_SERVICE": frontend_base_services.strategy_models,
            "_FRONTEND_PROFILE_READ_SERVICE": frontend_base_services.profile_read,
        }
    )
    return frontend_account_precompute_service


def build_frontend_read_partition(
    state: MutableMapping[str, Any],
    memory_payload_cache_service: MemoryPayloadCache,
    runtime_status_payload_service: Any,
    frontend_account_precompute_service: Any,
) -> Any:
    get = state.__getitem__
    frontend_read_services = build_frontend_read_services(
        frontend_dir=lambda: get("FRONTEND_DIR"),
        data_dir=lambda: get("DATA_DIR"),
        app_version=lambda: get("APP_VERSION"),
        cache_env_int=lambda name, default, **kwargs: get("cache_env_int")(name, default, **kwargs),
        env_flag=lambda name, default=False: get("_APP_CONFIG").env_flag(name, default),
        safe_float=get("safe_float"),
        copy_payload=get("_cache_copy_payload"),
        cache_get=memory_payload_cache_service.get,
        cache_set=memory_payload_cache_service.set,
        load_payload_cache=lambda payload_type, parts, ttl: get("load_payload_cache")(payload_type, parts, ttl),
        save_payload_cache=lambda payload_type, parts, payload, ttl: get("save_payload_cache")(
            payload_type,
            parts,
            payload,
            ttl,
        ),
        frontend_jobs_payload=runtime_status_payload_service.frontend_jobs_payload,
        light_status_payload=runtime_status_payload_service.light_status_payload,
        lightweight_news_feed=lambda **kwargs: lightweight_news_feed(**kwargs),
        fallback_news_feed=lambda **kwargs: get("quant_engine").news_feed(**kwargs),
        stock_count=lambda: len(getattr(get("quant_engine").universe, "code_to_name", {}) or {}),
        strategy_params=lambda *args, **kwargs: get("quant_engine").strategy_params(*args, **kwargs),
        strategy_source=lambda: get("quant_engine").strategy_source(),
        latest_price=lambda code, as_of: get("quant_engine").latest_price(code, as_of=as_of),
        run_frontend_payload_precompute=lambda **kwargs: get("job_manager").run_frontend_payload_precompute(**kwargs),
        runtime_daily_payload=lambda as_of: get("strategy_daily_runtime").run_daily(as_of),
        temporary_strategy_params=lambda params: get("quant_engine").temporary_strategy_params(params),
        recommendations=lambda *args, **kwargs: get("quant_engine").recommendations(*args, **kwargs),
        daily_plan=lambda *args, **kwargs: get("quant_engine").daily_plan(*args, **kwargs),
        append_log=lambda level, message, job, stage, payload: get("job_manager")._append_log(
            level,
            message,
            job=job,
            stage=stage,
            payload=payload,
        ),
        now=lambda: datetime.now(ZoneInfo("Asia/Shanghai")),
        profile_read_service=get("_FRONTEND_PROFILE_READ_SERVICE"),
        date_service=get("_FRONTEND_DATE_SERVICE"),
        account_read_service=get("_FRONTEND_ACCOUNT_READ_SERVICE"),
        account_precompute_service=frontend_account_precompute_service,
    )
    state.update(
        {
            "_FRONTEND_READ_SERVICES": frontend_read_services,
            "_FRONTEND_NEWS_READ_SERVICE": frontend_read_services.news,
            "_FRONTEND_STATIC_RESPONSE_SERVICE": frontend_read_services.static_response,
            "_LIGHT_DASHBOARD_READ_SERVICE": frontend_read_services.light_dashboard,
            "_FRONTEND_PAYLOAD_READ_SERVICE": frontend_read_services.payload,
            "_FRONTEND_SNAPSHOT_READ_SERVICE": frontend_read_services.snapshot,
            "_FRONTEND_RUNTIME_READ_SERVICE": frontend_read_services.runtime,
            "_FRONTEND_SIGNAL_READ_SERVICE": frontend_read_services.signal,
        }
    )
    return frontend_read_services
