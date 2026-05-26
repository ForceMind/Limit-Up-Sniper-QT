from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from fastapi.responses import FileResponse

from app.quant.admin_data_cache_service import AdminDataCacheService
from app.quant.admin_data_maintenance_service import AdminDataMaintenanceService


@dataclass(frozen=True)
class AdminDataServices:
    cache: AdminDataCacheService
    maintenance: AdminDataMaintenanceService


def build_admin_data_services(
    *,
    data_import_service: Any,
    app_version: Callable[[], str],
    data_dir: Callable[[], Path],
    cache_env_int: Callable[..., int],
    cache_get: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    memory_cache_status: Callable[[], Dict[str, Any]],
    memory_cache_clear: Callable[[], None],
    runtime_cache_status: Callable[[], Dict[str, Any]],
    runtime_cache_clear: Callable[..., Dict[str, Any]],
    database_overview: Callable[[], Dict[str, Any]],
    database_table_rows: Callable[..., Dict[str, Any]],
    max_upload_mb: Callable[[], float],
) -> AdminDataServices:
    cache_service = AdminDataCacheService(
        app_version=app_version,
        data_dir=data_dir,
        cache_env_int=cache_env_int,
        cache_get=cache_get,
        cache_set=cache_set,
        memory_cache_status=memory_cache_status,
        memory_cache_clear=memory_cache_clear,
        runtime_cache_status=runtime_cache_status,
        runtime_cache_clear=runtime_cache_clear,
        database_overview=database_overview,
        database_table_rows=database_table_rows,
    )
    return AdminDataServices(
        cache=cache_service,
        maintenance=AdminDataMaintenanceService(
            data_import_service=data_import_service,
            data_cache_service=cache_service,
            max_upload_mb=max_upload_mb,
            export_response_factory=_export_response,
        ),
    )


def _export_response(package_file: Path) -> FileResponse:
    return FileResponse(
        package_file,
        media_type="application/gzip",
        filename=package_file.name,
    )
