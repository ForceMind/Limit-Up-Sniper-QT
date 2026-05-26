from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping

from app.quant.access_audit import (
    access_logs,
    access_security,
    block_ip,
    client_ip_from_request,
    is_ip_blocked,
    record_access,
    unblock_ip,
)
from app.quant.application_admin_bootstrap import build_admin_partition as _build_admin_partition
from app.quant.application_core_bootstrap import (
    build_core_app_partition as _build_core_app_partition,
    build_runtime_status_partition as _build_runtime_status_partition,
    build_server_status_partition as _build_server_status_partition,
    configure_runtime_state as _configure_runtime_state,
)
from app.quant.application_frontend_bootstrap import (
    build_frontend_base_partition as _build_frontend_base_partition,
    build_frontend_read_partition as _build_frontend_read_partition,
)
from app.quant.application_operations_bootstrap import build_operations_partition as _build_operations_partition
from app.quant.application_research_bootstrap import build_quant_research_partition as _build_quant_research_partition
from app.quant.biying_sync import biying_minute_sync
from app.quant.database_inspector import database_overview, database_table_rows
from app.quant.engine import DATA_DIR, DEFAULT_AI_MODEL, quant_engine, safe_float
from app.quant.evolution import strategy_evolution
from app.quant.front_profile import (
    resolve_front_profile_updates as _resolve_front_profile_updates,
    strategy_catalog_items as _strategy_catalog_items,
)
from app.quant.jobs import job_manager
from app.quant.lhb_sync import lhb_status
from app.quant.memory_payload_cache import copy_payload as _cache_copy_payload
from app.quant.monitoring import data_coverage
from app.quant.news_fetcher import news_fetcher
from app.quant.news_repository import latest_news_time as latest_sqlite_news_time
from app.quant.notifier import trade_notifier
from app.quant.runtime_cache import clear_runtime_cache, runtime_cache_status
from app.quant.runtime_cache import env_int as cache_env_int
from app.quant.runtime_cache import load_payload_cache, save_payload_cache
from app.quant.strategy_daily_runtime import strategy_daily_runtime
from app.routers.admin_live import (
    json_fingerprint as _admin_live_json_fingerprint,
    log_key as _admin_live_log_key,
)
from app.routers.app_routes import (
    AdminRouteDependencies,
    ApplicationRouteDependencies,
    CoreRouteDependencies,
    FrontendRouteDependencies,
    FrontendStaticRouteDependencies,
    OperationsRouteDependencies,
    QuantResearchRouteDependencies,
    register_application_routes,
)
from app.quant.security import (
    auth_status,
    debug_auth_status,
    ensure_admin_entry_path,
    frontend_user_profile,
    frontend_user_summary,
    login,
    register_frontend_user,
    require_request_scope,
    required_scope_for_api,
    runtime_config_form,
    runtime_config_status,
    setup_auth,
    update_frontend_user_profile,
    update_runtime_config,
    verify_token,
)


@dataclass(frozen=True)
class ApplicationRuntime:
    app: Any
    values: dict[str, Any]

    def exports(self) -> dict[str, Any]:
        return dict(self.values)


APPLICATION_EXPORT_NAMES = (
    "access_logs",
    "access_security",
    "APP_NAME",
    "APP_VERSION",
    "app",
    "BACKUP_DIR",
    "BASE_DIR",
    "biying_minute_sync",
    "cache_env_int",
    "clear_runtime_cache",
    "data_coverage",
    "DATA_DIR",
    "DATA_IMPORT_JOBS",
    "database_overview",
    "database_table_rows",
    "DEFAULT_AI_MODEL",
    "frontend_user_profile",
    "frontend_user_summary",
    "FRONTEND_DIR",
    "job_manager",
    "latest_sqlite_news_time",
    "lhb_status",
    "load_payload_cache",
    "news_fetcher",
    "PROJECT_ROOT",
    "quant_engine",
    "runtime_cache_status",
    "save_payload_cache",
    "strategy_evolution",
    "trade_notifier",
    "update_frontend_user_profile",
    "_ADMIN_ACCESS_SERVICE",
    "_ADMIN_DATA_CACHE_SERVICE",
    "_ADMIN_DATA_MAINTENANCE_SERVICE",
    "_ADMIN_DATA_SERVICES",
    "_ADMIN_FRONTEND_USER_SERVICE",
    "_ADMIN_JOB_RUN_SERVICE",
    "_ADMIN_RUNTIME_SERVICES",
    "_ADMIN_SNAPSHOT_SERVICE",
    "_ADMIN_STRATEGY_RUNTIME_READ_SERVICE",
    "_AI_MONITORING_READ_SERVICE",
    "_APP_CONFIG",
    "_CORE_APP_SERVICES",
    "_CORE_SYSTEM_PAYLOAD_SERVICE",
    "_DATA_COLLECTION_SERVICE",
    "_DATA_COVERAGE_SERVICE",
    "_DATA_IMPORT_SERVICE",
    "_FIT_STRATEGY_SERVICE",
    "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_LOCK",
    "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING",
    "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_TASKS",
    "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKER_LOCK",
    "_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED",
    "_FRONTEND_ACCOUNT_PRECOMPUTE_QUEUE_LOCK",
    "_FRONTEND_ACCOUNT_PRECOMPUTE_SERVICE",
    "_FRONTEND_ACCOUNT_READ_SERVICE",
    "_FRONTEND_ACCOUNT_REPLAY_DAYS",
    "_FRONTEND_BASE_SERVICES",
    "_FRONTEND_DATE_SERVICE",
    "_FRONTEND_FOLLOW_PERIOD_SERVICE",
    "_FRONTEND_NEWS_READ_SERVICE",
    "_FRONTEND_PAYLOAD_READ_SERVICE",
    "_FRONTEND_PROFILE_READ_SERVICE",
    "_FRONTEND_PROFILE_UPDATE_SERVICE",
    "_FRONTEND_READ_SERVICES",
    "_FRONTEND_RUNTIME_READ_SERVICE",
    "_FRONTEND_SIGNAL_READ_SERVICE",
    "_FRONTEND_SNAPSHOT_READ_SERVICE",
    "_FRONTEND_STATIC_RESPONSE_SERVICE",
    "_FRONTEND_STRATEGY_MODELS_SERVICE",
    "_FRONTEND_USER_LIFECYCLE_SERVICE",
    "_LIGHT_DASHBOARD_READ_SERVICE",
    "_MEMORY_PAYLOAD_CACHE",
    "_MEMORY_PAYLOAD_CACHE_MAX",
    "_MEMORY_PAYLOAD_CACHE_SERVICE",
    "_OPERATIONS_SERVICES",
    "_QUANT_BACKTEST_SERVICE",
    "_QUANT_BASIC_SERVICE",
    "_QUANT_RESEARCH_SERVICES",
    "_QUANT_STRATEGY_RESEARCH_SERVICE",
    "_QUANT_TIMELINE_SERVICE",
    "_ROUTE_RUNTIME_DEFAULTS",
    "_RUNTIME_STATUS_PAYLOAD_SERVICE",
    "_SERVER_READ_STATUS_SERVICE",
    "_STRATEGY_EVOLUTION_SERVICE",
    "_STRATEGY_MODEL_BACKTEST_SERVICE",
    "_STRATEGY_MODEL_LOOKUP_SERVICE",
    "_SYSTEM_CONTROL_SERVICE",
    "_SYSTEM_STARTUP_SERVICE",
)


def _install_default_bindings(state: MutableMapping[str, Any]) -> None:
    defaults = {
        "access_logs": access_logs,
        "access_security": access_security,
        "block_ip": block_ip,
        "client_ip_from_request": client_ip_from_request,
        "is_ip_blocked": is_ip_blocked,
        "record_access": record_access,
        "unblock_ip": unblock_ip,
        "biying_minute_sync": biying_minute_sync,
        "cache_env_int": cache_env_int,
        "clear_runtime_cache": clear_runtime_cache,
        "data_coverage": data_coverage,
        "database_overview": database_overview,
        "database_table_rows": database_table_rows,
        "DATA_DIR": DATA_DIR,
        "DEFAULT_AI_MODEL": DEFAULT_AI_MODEL,
        "debug_auth_status": debug_auth_status,
        "ensure_admin_entry_path": ensure_admin_entry_path,
        "frontend_user_profile": frontend_user_profile,
        "frontend_user_summary": frontend_user_summary,
        "job_manager": job_manager,
        "latest_sqlite_news_time": latest_sqlite_news_time,
        "lhb_status": lhb_status,
        "load_payload_cache": load_payload_cache,
        "login": login,
        "news_fetcher": news_fetcher,
        "quant_engine": quant_engine,
        "register_frontend_user": register_frontend_user,
        "require_request_scope": require_request_scope,
        "required_scope_for_api": required_scope_for_api,
        "runtime_cache_status": runtime_cache_status,
        "runtime_config_form": runtime_config_form,
        "runtime_config_status": runtime_config_status,
        "safe_float": safe_float,
        "save_payload_cache": save_payload_cache,
        "setup_auth": setup_auth,
        "strategy_daily_runtime": strategy_daily_runtime,
        "strategy_evolution": strategy_evolution,
        "trade_notifier": trade_notifier,
        "update_frontend_user_profile": update_frontend_user_profile,
        "update_runtime_config": update_runtime_config,
        "verify_token": verify_token,
        "_admin_live_json_fingerprint": _admin_live_json_fingerprint,
        "_admin_live_log_key": _admin_live_log_key,
        "_cache_copy_payload": _cache_copy_payload,
        "_resolve_front_profile_updates": _resolve_front_profile_updates,
        "_strategy_catalog_items": _strategy_catalog_items,
    }
    for name, value in defaults.items():
        state.setdefault(name, value)


def _register_runtime_routes(
    state: MutableMapping[str, Any],
    *,
    app: Any,
    route_runtime_defaults: Any,
    runtime_status_payload_service: Any,
    admin_access_service: Any,
) -> None:
    get = state.__getitem__
    register_application_routes(
        app,
        ApplicationRouteDependencies(
            core=CoreRouteDependencies(
                core_system_service=get("_CORE_SYSTEM_PAYLOAD_SERVICE"),
                runtime_status_service=runtime_status_payload_service,
                auth_status_payload=auth_status,
                auth_setup_payload=setup_auth,
                config_status_payload=runtime_config_status,
                config_runtime_payload=runtime_config_form,
            ),
            frontend=FrontendRouteDependencies(
                profile_read_service=get("_FRONTEND_PROFILE_READ_SERVICE"),
                profile_update_service=get("_FRONTEND_PROFILE_UPDATE_SERVICE"),
                snapshot_read_service=get("_FRONTEND_SNAPSHOT_READ_SERVICE"),
                runtime_read_service=get("_FRONTEND_RUNTIME_READ_SERVICE"),
                signal_read_service=get("_FRONTEND_SIGNAL_READ_SERVICE"),
                route_defaults=route_runtime_defaults,
            ),
            admin=AdminRouteDependencies(
                snapshot_service=get("_ADMIN_SNAPSHOT_SERVICE"),
                strategy_runtime_service=get("_ADMIN_STRATEGY_RUNTIME_READ_SERVICE"),
                runtime_status_service=runtime_status_payload_service,
                admin_job_run_service=get("_ADMIN_JOB_RUN_SERVICE"),
                system_startup_service=get("_SYSTEM_STARTUP_SERVICE"),
                data_maintenance_service=get("_ADMIN_DATA_MAINTENANCE_SERVICE"),
                access_service=admin_access_service,
                frontend_user_service=get("_ADMIN_FRONTEND_USER_SERVICE"),
                system_control_service=get("_SYSTEM_CONTROL_SERVICE"),
                job_manager=get("job_manager"),
                route_defaults=route_runtime_defaults,
                verify_admin_token=lambda token: get("verify_token")(token, "admin"),
                biying_status_payload=lambda: get("biying_minute_sync").status(),
                live_log_key=get("_admin_live_log_key"),
                live_fingerprint=get("_admin_live_json_fingerprint"),
            ),
            quant_research=QuantResearchRouteDependencies(
                basic_service=get("_QUANT_BASIC_SERVICE"),
                strategy_research_service=get("_QUANT_STRATEGY_RESEARCH_SERVICE"),
                timeline_service=get("_QUANT_TIMELINE_SERVICE"),
                backtest_service=get("_QUANT_BACKTEST_SERVICE"),
                route_defaults=route_runtime_defaults,
            ),
            operations=OperationsRouteDependencies(
                data_collection_service=get("_DATA_COLLECTION_SERVICE"),
                data_coverage_service=get("_DATA_COVERAGE_SERVICE"),
                ai_monitoring_service=get("_AI_MONITORING_READ_SERVICE"),
                route_defaults=route_runtime_defaults,
            ),
            frontend_static=FrontendStaticRouteDependencies(
                static_response_service=get("_FRONTEND_STATIC_RESPONSE_SERVICE"),
                admin_entry_path_payload=ensure_admin_entry_path,
            ),
        ),
    )


def _export_runtime_values(state: MutableMapping[str, Any]) -> dict[str, Any]:
    get = state.__getitem__
    values = {name: get(name) for name in APPLICATION_EXPORT_NAMES}
    state.update(values)
    return values


def build_application_runtime(state: MutableMapping[str, Any], *, app_file: str) -> ApplicationRuntime:
    _install_default_bindings(state)
    memory_payload_cache_service, route_runtime_defaults = _configure_runtime_state(state, app_file=app_file)
    server_read_status_service = _build_server_status_partition(state, memory_payload_cache_service)
    frontend_account_precompute_service = _build_frontend_base_partition(
        state,
        memory_payload_cache_service,
        server_read_status_service,
    )
    runtime_status_payload_service = _build_runtime_status_partition(
        state,
        memory_payload_cache_service,
        server_read_status_service,
        frontend_account_precompute_service,
    )
    frontend_read_services = _build_frontend_read_partition(
        state,
        memory_payload_cache_service,
        runtime_status_payload_service,
        frontend_account_precompute_service,
    )
    app = _build_core_app_partition(
        state,
        memory_payload_cache_service,
        server_read_status_service,
        frontend_account_precompute_service,
    )
    _build_quant_research_partition(state)
    _build_operations_partition(state, memory_payload_cache_service)
    admin_access_service = _build_admin_partition(
        state,
        memory_payload_cache_service,
        runtime_status_payload_service,
        frontend_read_services,
        frontend_account_precompute_service,
    )
    _register_runtime_routes(
        state,
        app=app,
        route_runtime_defaults=route_runtime_defaults,
        runtime_status_payload_service=runtime_status_payload_service,
        admin_access_service=admin_access_service,
    )
    values = _export_runtime_values(state)
    return ApplicationRuntime(app=app, values=values)
