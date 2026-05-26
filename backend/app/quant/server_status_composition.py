from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict

from app.quant.server_read_status import RuntimeStatusPayloadService, ServerReadStatusService


def build_server_read_status_service(
    *,
    app_version: Callable[[], str],
    data_dir: Callable[[], Path],
    project_root: Callable[[], Path],
    cache_env_int: Callable[..., int],
    cache_get: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    env_flag: Callable[[str, bool], bool],
    latest_sqlite_news_time: Callable[[], str],
    latest_history_news_time: Callable[[], str],
    engine_first_data_date: Callable[[], str],
    engine_latest_event_date: Callable[[], str],
    now: Callable[[], datetime],
) -> ServerReadStatusService:
    return ServerReadStatusService(
        app_version=app_version,
        data_dir=data_dir,
        project_root=project_root,
        cache_env_int=lambda name, default: cache_env_int(name, default, minimum=0, maximum=300),
        cache_get=cache_get,
        cache_set=cache_set,
        env_flag=env_flag,
        latest_sqlite_news_time=latest_sqlite_news_time,
        latest_history_news_time=latest_history_news_time,
        engine_first_data_date=engine_first_data_date,
        engine_latest_event_date=engine_latest_event_date,
        now=now,
    )


def build_runtime_status_payload_service(
    *,
    app_name: Callable[[], str],
    app_version: Callable[[], str],
    data_dir: Callable[[], Path],
    default_ai_model: Callable[[], str],
    server_read_status_service: ServerReadStatusService,
    engine_latest_event_date: Callable[[], str],
    frontend_status: Callable[[], Dict[str, Any]],
    jobs_status: Callable[[bool], Dict[str, Any]],
    frontend_account_precompute_service: Any,
    cache_env_int: Callable[..., int],
    cache_get: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    now: Callable[[], datetime],
) -> RuntimeStatusPayloadService:
    return RuntimeStatusPayloadService(
        app_name=app_name,
        app_version=app_version,
        data_dir=data_dir,
        default_ai_model=default_ai_model,
        latest_news_time=server_read_status_service.latest_news_time,
        data_date_bounds=server_read_status_service.data_date_bounds,
        engine_latest_event_date=engine_latest_event_date,
        frontend_status=frontend_status,
        jobs_status=jobs_status,
        frontend_account_precompute_queue_status=frontend_account_precompute_service.queue_status,
        frontend_account_precompute_async_status=frontend_account_precompute_service.async_status,
        frontend_jobs_cache_env_int=lambda name, default: cache_env_int(name, default, minimum=0, maximum=60),
        cache_get=cache_get,
        cache_set=cache_set,
        now=now,
    )
