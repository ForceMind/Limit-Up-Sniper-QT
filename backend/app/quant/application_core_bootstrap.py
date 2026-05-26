from __future__ import annotations

from datetime import datetime
from typing import Any, MutableMapping
from zoneinfo import ZoneInfo

from app.quant.app_config import AppRuntimeConfig
from app.quant.core_app_composition import build_core_app_services
from app.quant.memory_payload_cache import MemoryPayloadCache
from app.quant.route_runtime_defaults import RouteRuntimeDefaults
from app.quant.server_status_composition import build_runtime_status_payload_service, build_server_read_status_service


def configure_runtime_state(state: MutableMapping[str, Any], *, app_file: str) -> tuple[MemoryPayloadCache, Any]:
    app_config = AppRuntimeConfig.from_app_file(app_file, app_name="涨停狙击手")
    state.update(
        {
            "_APP_CONFIG": app_config,
            "BASE_DIR": app_config.base_dir,
            "FRONTEND_DIR": app_config.frontend_dir,
            "PROJECT_ROOT": app_config.project_root,
            "BACKUP_DIR": app_config.backup_dir,
            "APP_VERSION": app_config.app_version(),
            "APP_NAME": "涨停狙击手",
        }
    )

    memory_payload_cache_service = MemoryPayloadCache(max_rows=256)
    state.update(
        {
            "_MEMORY_PAYLOAD_CACHE_SERVICE": memory_payload_cache_service,
            "_MEMORY_PAYLOAD_CACHE": memory_payload_cache_service.rows,
            "_MEMORY_PAYLOAD_CACHE_MAX": memory_payload_cache_service.max_rows,
            "_FRONTEND_ACCOUNT_REPLAY_DAYS": app_config.frontend_account_replay_days(),
        }
    )

    route_runtime_defaults = RouteRuntimeDefaults(env_flag=app_config.env_flag)
    state["_ROUTE_RUNTIME_DEFAULTS"] = route_runtime_defaults
    return memory_payload_cache_service, route_runtime_defaults


def build_server_status_partition(
    state: MutableMapping[str, Any],
    memory_payload_cache_service: MemoryPayloadCache,
) -> Any:
    get = state.__getitem__
    server_read_status_service = build_server_read_status_service(
        app_version=lambda: get("APP_VERSION"),
        data_dir=lambda: get("DATA_DIR"),
        project_root=lambda: get("PROJECT_ROOT"),
        cache_env_int=lambda name, default, **kwargs: get("cache_env_int")(name, default, **kwargs),
        cache_get=memory_payload_cache_service.get,
        cache_set=memory_payload_cache_service.set,
        env_flag=lambda name, default=False: get("_APP_CONFIG").env_flag(name, default),
        latest_sqlite_news_time=lambda: get("latest_sqlite_news_time")(),
        latest_history_news_time=lambda: get("news_fetcher").latest_history_time(),
        engine_first_data_date=lambda: get("quant_engine").first_data_date(),
        engine_latest_event_date=lambda: get("quant_engine").latest_event_date(),
        now=lambda: datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    state["_SERVER_READ_STATUS_SERVICE"] = server_read_status_service
    return server_read_status_service


def build_runtime_status_partition(
    state: MutableMapping[str, Any],
    memory_payload_cache_service: MemoryPayloadCache,
    server_read_status_service: Any,
    frontend_account_precompute_service: Any,
) -> Any:
    get = state.__getitem__
    runtime_status_payload_service = build_runtime_status_payload_service(
        app_name=lambda: get("APP_NAME"),
        app_version=lambda: get("APP_VERSION"),
        data_dir=lambda: get("DATA_DIR"),
        default_ai_model=lambda: get("DEFAULT_AI_MODEL"),
        server_read_status_service=server_read_status_service,
        engine_latest_event_date=lambda: get("quant_engine").latest_event_date(),
        frontend_status=lambda: get("job_manager").frontend_status(),
        jobs_status=lambda light: get("job_manager").status(light=light),
        frontend_account_precompute_service=frontend_account_precompute_service,
        cache_env_int=lambda name, default, **kwargs: get("cache_env_int")(name, default, **kwargs),
        cache_get=memory_payload_cache_service.get,
        cache_set=memory_payload_cache_service.set,
        now=lambda: datetime.now(ZoneInfo("Asia/Shanghai")),
    )
    state["_RUNTIME_STATUS_PAYLOAD_SERVICE"] = runtime_status_payload_service
    return runtime_status_payload_service


def build_core_app_partition(
    state: MutableMapping[str, Any],
    memory_payload_cache_service: MemoryPayloadCache,
    server_read_status_service: Any,
    frontend_account_precompute_service: Any,
) -> Any:
    get = state.__getitem__
    core_app_services = build_core_app_services(
        app_name=lambda: get("APP_NAME"),
        app_version=lambda: get("APP_VERSION"),
        frontend_dir=lambda: get("FRONTEND_DIR"),
        data_dir=lambda: get("DATA_DIR"),
        backup_dir=lambda: get("BACKUP_DIR"),
        env_flag=lambda name, default=False: get("_APP_CONFIG").env_flag(name, default),
        job_manager=get("job_manager"),
        quant_engine=get("quant_engine"),
        server_read_status_service=server_read_status_service,
        frontend_user_lifecycle_service=get("_FRONTEND_USER_LIFECYCLE_SERVICE"),
        frontend_follow_period_service=get("_FRONTEND_FOLLOW_PERIOD_SERVICE"),
        frontend_account_precompute_service=frontend_account_precompute_service,
        frontend_account_read_service=get("_FRONTEND_ACCOUNT_READ_SERVICE"),
        clear_memory_cache=memory_payload_cache_service.clear,
        required_scope_for_api=lambda path, method: get("required_scope_for_api")(path, method),
        client_ip_from_request=lambda request: get("client_ip_from_request")(request),
        is_ip_blocked=lambda ip: get("is_ip_blocked")(ip),
        require_request_scope=lambda request, scope: get("require_request_scope")(request, scope),
        verify_token=lambda token, scope: get("verify_token")(token, scope),
        record_access=lambda request, status_code, duration_ms, auth_payload: get("record_access")(
            request,
            status_code,
            duration_ms,
            auth_payload,
        ),
        debug_auth_status=lambda: get("debug_auth_status")(),
        login=lambda payload, request: get("login")(payload, request),
        register_frontend_user=lambda payload, request: get("register_frontend_user")(payload, request),
        frontend_user_profile=lambda username: get("frontend_user_profile")(username),
        update_runtime_config=lambda payload: get("update_runtime_config")(payload),
        append_log=lambda level, message, job, stage, payload: get("job_manager")._append_log(
            level,
            message,
            job=job,
            stage=stage,
            payload=payload,
        ),
    )
    app = core_app_services.app
    state.update(
        {
            "_CORE_APP_SERVICES": core_app_services,
            "app": app,
            "DATA_IMPORT_JOBS": core_app_services.data_import_jobs,
            "_DATA_IMPORT_SERVICE": core_app_services.data_import,
            "_CORE_SYSTEM_PAYLOAD_SERVICE": core_app_services.core_system,
        }
    )
    return app
