from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from fastapi import HTTPException

from app.quant.data_import_service import DataImportUploadError
from app.quant.data_transfer import DataPackageError


class AdminDataMaintenanceService:
    def __init__(
        self,
        *,
        data_import_service: Any,
        data_cache_service: Any,
        max_upload_mb: Callable[[], float],
        export_response_factory: Callable[[Path], Any],
    ) -> None:
        self._data_import_service = data_import_service
        self._data_cache_service = data_cache_service
        self._max_upload_mb = max_upload_mb
        self._export_response_factory = export_response_factory

    def backup_payload(self) -> Dict[str, Any]:
        return self._data_import_service.backup_payload()

    def export_response_payload(self, include_logs: bool = False) -> Any:
        result = self._data_import_service.export_package(include_logs=include_logs)
        return self._export_response_factory(Path(result["package_file"]))

    def import_status_payload(self, job_id: str) -> Dict[str, Any]:
        job = self._data_import_service.job_snapshot(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="data import job not found")
        return {"status": "ok", "job": job}

    async def import_payload(self, request: Any, background_tasks: Any, backup: bool = True) -> Dict[str, Any]:
        try:
            return await self._data_import_service.accept_upload(
                request,
                background_tasks,
                backup=backup,
                max_upload_mb=self._max_upload_mb(),
            )
        except DataImportUploadError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except DataPackageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def clear_sample_state_payload(self) -> Dict[str, Any]:
        return self._data_import_service.clear_sample_state_payload()

    def database_tables_payload(self) -> Dict[str, Any]:
        return self._data_cache_service.database_tables_payload()

    def database_table_payload(self, table_name: str, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        try:
            return self._data_cache_service.database_table_payload(table_name, limit=limit, offset=offset)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def cache_status_payload(self) -> Dict[str, Any]:
        return self._data_cache_service.cache_status_payload()

    def cache_clear_payload(self, scope: str = "expired") -> Dict[str, Any]:
        return self._data_cache_service.cache_clear_payload(scope)
