from __future__ import annotations

from typing import Any, MutableMapping

from app.quant.admin_access_service import AdminAccessService
from app.quant.admin_data_composition import build_admin_data_services
from app.quant.admin_runtime_composition import build_admin_runtime_services
from app.quant.memory_payload_cache import MemoryPayloadCache
from app.quant.monitoring import ai_failures, ai_records_feed, ai_usage_summary


def build_admin_partition(
    state: MutableMapping[str, Any],
    memory_payload_cache_service: MemoryPayloadCache,
    runtime_status_payload_service: Any,
    frontend_read_services: Any,
    frontend_account_precompute_service: Any,
) -> Any:
    get = state.__getitem__
    admin_runtime_services = build_admin_runtime_services(
        app_version=lambda: get("APP_VERSION"),
        project_root=lambda: get("PROJECT_ROOT"),
        cache_env_int=lambda name, default, **kwargs: get("cache_env_int")(name, default, **kwargs),
        env_flag=lambda name, default=False: get("_APP_CONFIG").env_flag(name, default),
        resolve_as_of=get("_FRONTEND_DATE_SERVICE").account_as_of,
        cache_get=memory_payload_cache_service.get,
        cache_set=memory_payload_cache_service.set,
        runtime_status_service=runtime_status_payload_service,
        frontend_read_services=frontend_read_services,
        frontend_strategy_models_service=get("_FRONTEND_STRATEGY_MODELS_SERVICE"),
        frontend_account_precompute_service=frontend_account_precompute_service,
        admin_frontend_user_service=get("_ADMIN_FRONTEND_USER_SERVICE"),
        data_coverage_service=get("_DATA_COVERAGE_SERVICE"),
        job_manager=get("job_manager"),
        quant_engine=get("quant_engine"),
        strategy_evolution=get("strategy_evolution"),
        trade_notifier=get("trade_notifier"),
        frontend_user_summary=lambda: get("frontend_user_summary")(),
        access_logs=lambda **kwargs: get("access_logs")(**kwargs),
        biying_status=lambda: get("biying_minute_sync").status(),
        lhb_status=lambda: get("lhb_status")(),
        ai_usage_summary=lambda: ai_usage_summary(),
        ai_failures=lambda **kwargs: ai_failures(**kwargs),
        ai_records_feed=lambda **kwargs: ai_records_feed(**kwargs),
        append_log=lambda level, message, job, stage, payload: get("job_manager")._append_log(
            level,
            message,
            job=job,
            stage=stage,
            payload=payload,
        ),
    )
    state.update(
        {
            "_ADMIN_RUNTIME_SERVICES": admin_runtime_services,
            "_ADMIN_JOB_RUN_SERVICE": admin_runtime_services.job_run,
            "_ADMIN_STRATEGY_RUNTIME_READ_SERVICE": admin_runtime_services.strategy_runtime,
            "_ADMIN_SNAPSHOT_SERVICE": admin_runtime_services.snapshot,
            "_SYSTEM_STARTUP_SERVICE": admin_runtime_services.system_startup,
            "_SYSTEM_CONTROL_SERVICE": admin_runtime_services.system_control,
        }
    )

    admin_data_services = build_admin_data_services(
        data_import_service=get("_DATA_IMPORT_SERVICE"),
        app_version=lambda: get("APP_VERSION"),
        data_dir=lambda: get("DATA_DIR"),
        cache_env_int=lambda name, default, **kwargs: get("cache_env_int")(name, default, **kwargs),
        cache_get=memory_payload_cache_service.get,
        cache_set=memory_payload_cache_service.set,
        memory_cache_status=lambda: memory_payload_cache_service.status(),
        memory_cache_clear=memory_payload_cache_service.clear,
        runtime_cache_status=lambda: get("runtime_cache_status")(),
        runtime_cache_clear=lambda **kwargs: get("clear_runtime_cache")(**kwargs),
        database_overview=lambda: get("database_overview")(),
        database_table_rows=lambda *args, **kwargs: get("database_table_rows")(*args, **kwargs),
        max_upload_mb=lambda: get("_APP_CONFIG").env_float("QT_DATA_UPLOAD_MAX_MB", 1024.0),
    )
    state.update(
        {
            "_ADMIN_DATA_SERVICES": admin_data_services,
            "_ADMIN_DATA_CACHE_SERVICE": admin_data_services.cache,
            "_ADMIN_DATA_MAINTENANCE_SERVICE": admin_data_services.maintenance,
        }
    )

    admin_access_service = AdminAccessService(
        access_logs=lambda **kwargs: get("access_logs")(**kwargs),
        access_security=lambda **kwargs: get("access_security")(**kwargs),
        block_ip=lambda *args, **kwargs: get("block_ip")(*args, **kwargs),
        unblock_ip=lambda ip: get("unblock_ip")(ip),
    )
    state["_ADMIN_ACCESS_SERVICE"] = admin_access_service
    return admin_access_service
