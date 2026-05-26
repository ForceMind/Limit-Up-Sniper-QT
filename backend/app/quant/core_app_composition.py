from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from fastapi import FastAPI

from app.app_shell import build_app_shell
from app.quant.app_lifecycle import build_app_lifespan
from app.quant.core_system_service import CoreSystemPayloadService
from app.quant.data_import_service import DATA_IMPORT_JOBS, DataImportService, refresh_quant_caches


@dataclass(frozen=True)
class CoreAppServices:
    app: FastAPI
    core_system: CoreSystemPayloadService
    data_import: DataImportService
    data_import_jobs: Dict[str, Dict[str, Any]]


def build_core_app_services(
    *,
    app_name: Callable[[], str],
    app_version: Callable[[], str],
    frontend_dir: Callable[[], Path],
    data_dir: Callable[[], Path],
    backup_dir: Callable[[], Path],
    env_flag: Callable[[str, bool], bool],
    job_manager: Any,
    quant_engine: Any,
    server_read_status_service: Any,
    frontend_user_lifecycle_service: Any,
    frontend_follow_period_service: Any,
    frontend_account_precompute_service: Any,
    frontend_account_read_service: Any,
    clear_memory_cache: Callable[[], None],
    required_scope_for_api: Callable[..., Any],
    client_ip_from_request: Callable[..., Any],
    is_ip_blocked: Callable[..., Any],
    require_request_scope: Callable[..., Any],
    verify_token: Callable[..., Any],
    record_access: Callable[..., Any],
    debug_auth_status: Callable[[], Dict[str, Any]],
    login: Callable[[Dict[str, Any], Any], Dict[str, Any]],
    register_frontend_user: Callable[[Dict[str, Any], Any], Dict[str, Any]],
    frontend_user_profile: Callable[[str], Dict[str, Any]],
    update_runtime_config: Callable[[Dict[str, Any]], Dict[str, Any]],
    append_log: Callable[[str, str, str, str, Dict[str, Any]], None],
) -> CoreAppServices:
    data_import = DataImportService(
        data_dir=data_dir,
        backup_dir=backup_dir,
        refresh_caches=lambda: refresh_quant_caches(
            quant_engine=quant_engine,
            clear_frontend_account_cache=frontend_account_read_service.clear_memory_cache,
            clear_memory_cache=clear_memory_cache,
        ),
        append_log=append_log,
    )
    app = build_app_shell(
        title="Limit Up Sniper Quant System",
        version=app_version(),
        lifespan=build_app_lifespan(
            env_flag=env_flag,
            job_manager=job_manager,
        ),
        frontend_static_dir=frontend_dir() / "static",
        required_scope_for_api=required_scope_for_api,
        client_ip_from_request=client_ip_from_request,
        is_ip_blocked=is_ip_blocked,
        require_request_scope=require_request_scope,
        verify_token=verify_token,
        record_access=record_access,
    )
    core_system = CoreSystemPayloadService(
        app_name=app_name,
        app_version=app_version,
        git_ref=server_read_status_service.git_ref,
        openapi_schema=lambda: app.openapi(),
        debug_auth_status=debug_auth_status,
        login=login,
        register_frontend_user=register_frontend_user,
        frontend_register_lifecycle=lambda result: frontend_user_lifecycle_service.after_front_register(
            result,
            load_profile=frontend_user_profile,
            record_follow_period=frontend_follow_period_service.record_user_follow_period,
            queue_account_precompute=frontend_account_precompute_service.queue_runtime_user,
        ),
        update_runtime_config=update_runtime_config,
        append_log=lambda level, message, job, stage: append_log(level, message, job, stage, {}),
    )
    return CoreAppServices(
        app=app,
        core_system=core_system,
        data_import=data_import,
        data_import_jobs=DATA_IMPORT_JOBS,
    )
