from __future__ import annotations

import os
import tarfile
import threading
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict
from zoneinfo import ZoneInfo

from app.quant.data_transfer import (
    DataPackageError,
    clear_sample_quant_state as _clear_sample_quant_state,
    create_safe_data_package as _create_safe_data_package,
    import_data_package,
    validate_data_package,
)
from app.quant.engine_utils import safe_float


DATA_IMPORT_JOBS: Dict[str, Dict[str, Any]] = {}
DATA_IMPORT_JOBS_LOCK = threading.Lock()


def refresh_quant_caches(
    *,
    quant_engine: Any,
    clear_frontend_account_cache: Callable[[], None],
    clear_memory_cache: Callable[[], None],
) -> None:
    for attr in ("_events_cache", "_kline_cache", "_future_return_cache", "_correlation_cache"):
        value = getattr(quant_engine, attr, None)
        if isinstance(value, dict):
            value.clear()
        elif isinstance(value, list):
            value.clear()
    if hasattr(quant_engine, "_cache_source_key"):
        setattr(quant_engine, "_cache_source_key", "")
    if hasattr(quant_engine, "_events_cache_key"):
        setattr(quant_engine, "_events_cache_key", "")
    try:
        quant_engine.clear_market_cache()
    except Exception:
        pass
    clear_frontend_account_cache()
    clear_memory_cache()


class DataImportUploadError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class DataImportService:
    def __init__(
        self,
        *,
        data_dir: Callable[[], Path],
        backup_dir: Callable[[], Path],
        refresh_caches: Callable[[], None],
        append_log: Callable[[str, str, str, str, Dict[str, Any]], None],
        create_safe_data_package: Callable[..., Dict[str, Any]] = _create_safe_data_package,
        clear_sample_quant_state: Callable[[Path], Dict[str, Any]] = _clear_sample_quant_state,
    ) -> None:
        self._data_dir = data_dir
        self._backup_dir = backup_dir
        self._refresh_caches = refresh_caches
        self._append_log = append_log
        self._create_safe_data_package = create_safe_data_package
        self._clear_sample_quant_state = clear_sample_quant_state

    def create_data_backup(self) -> Dict[str, Any]:
        data_dir = self._data_dir()
        backup_dir = self._backup_dir()
        if not data_dir.exists():
            return {"status": "error", "message": f"data dir not found: {data_dir}"}
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"backend_data_{stamp}.tar.gz"
        with tarfile.open(backup_file, "w:gz") as archive:
            archive.add(data_dir, arcname="data")
        return {
            "status": "ok",
            "backup_file": str(backup_file),
            "size_bytes": backup_file.stat().st_size,
            "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }

    def backup_payload(self) -> Dict[str, Any]:
        result = self.create_data_backup()
        self._append_log("info", "admin requested data backup", "admin_backup", "finish", result)
        return result

    def export_package(self, *, include_logs: bool = False) -> Dict[str, Any]:
        result = self._create_safe_data_package(self._backup_dir(), self._data_dir(), include_logs=include_logs)
        self._append_log("info", "admin generated data transfer package", "admin_data_export", "finish", result)
        return result

    def clear_sample_state_payload(self) -> Dict[str, Any]:
        result = self._clear_sample_quant_state(self._data_dir())
        if result.get("cleared"):
            self._refresh_caches()
        self._append_log("warning", "admin checked and cleared sample state", "admin_data_clear_sample", "finish", result)
        return result

    def job_snapshot(self, job_id: str) -> Dict[str, Any]:
        with DATA_IMPORT_JOBS_LOCK:
            job = dict(DATA_IMPORT_JOBS.get(job_id) or {})
            logs = job.get("logs") if isinstance(job.get("logs"), list) else []
            job["logs"] = list(logs)[-80:]
            return job

    def update_job(self, job_id: str, **updates: Any) -> Dict[str, Any]:
        with DATA_IMPORT_JOBS_LOCK:
            job = DATA_IMPORT_JOBS.setdefault(
                job_id,
                {
                    "job_id": job_id,
                    "status": "queued",
                    "stage": "queued",
                    "progress": 0,
                    "message": "等待开始合并",
                    "logs": [],
                    "created_at": self._now_shanghai_iso(),
                },
            )
            log_message = str(updates.pop("log_message", "") or "").strip()
            job.update({key: value for key, value in updates.items() if value is not None})
            job["updated_at"] = self._now_shanghai_iso()
            if log_message:
                logs = job.setdefault("logs", [])
                if isinstance(logs, list):
                    logs.append({"ts": job["updated_at"], "message": log_message, "stage": job.get("stage")})
                    del logs[:-80]
            return dict(job)

    def progress(self, job_id: str, payload: Dict[str, Any]) -> None:
        total_files = int(safe_float(payload.get("total_files"), 0))
        imported_files = int(safe_float(payload.get("imported_files"), 0))
        sqlite_table_count = int(safe_float(payload.get("sqlite_table_count"), 0))
        sqlite_table_index = int(safe_float(payload.get("sqlite_table_index"), 0))
        progress = 35
        if total_files > 0:
            progress = 35 + int(min(58, imported_files / total_files * 58))
        if sqlite_table_count > 0:
            progress = max(progress, 35 + int(min(58, sqlite_table_index / sqlite_table_count * 58)))
        message = str(payload.get("message") or "正在合并数据")
        self.update_job(
            job_id,
            status="running",
            stage=str(payload.get("stage") or "importing"),
            progress=min(93, max(35, progress)),
            message=message,
            imported_files=imported_files or None,
            total_files=total_files or None,
            current_file=payload.get("current_file"),
            sqlite_table=payload.get("sqlite_table"),
            sqlite_table_index=sqlite_table_index or None,
            sqlite_table_count=sqlite_table_count or None,
            added_records=payload.get("added_records"),
            log_message=message,
        )

    def run_data_import_job(self, job_id: str, upload_file: Path, received: int, backup: bool) -> None:
        backup_result: Dict[str, Any] = {}
        try:
            self.update_job(
                job_id,
                status="running",
                stage="backup",
                progress=20,
                message="正在备份服务器现有数据",
                log_message="正在备份服务器现有数据",
            )
            if backup:
                backup_result = self.create_data_backup()
                if backup_result.get("status") != "ok":
                    raise RuntimeError(f"导入前备份失败：{backup_result.get('message') or 'unknown'}")
            else:
                self.update_job(job_id, backup_skipped=True)
            self.update_job(
                job_id,
                stage="importing",
                progress=32,
                message="备份完成，开始合并上传数据",
                backup=backup_result,
                log_message="备份完成，开始合并上传数据",
            )
            result = import_data_package(
                upload_file,
                self._data_dir(),
                progress=lambda payload: self.progress(job_id, payload),
            )
            self.update_job(
                job_id,
                stage="refresh",
                progress=96,
                message="数据已合并，正在刷新量化缓存",
                result=result,
                backup=backup_result,
                received_bytes=received,
                log_message="数据已合并，正在刷新量化缓存",
            )
            self._refresh_caches()
            result["backup"] = backup_result
            result["received_bytes"] = received
            self.update_job(
                job_id,
                status="done",
                stage="done",
                progress=100,
                message="上传数据已合并完成",
                result=result,
                backup=backup_result,
                received_bytes=received,
                finished_at=self._now_shanghai_iso(),
                log_message="上传数据已合并完成",
            )
            self._append_log("warning", "后台已导入数据迁移包", "admin_data_import", "finish", result)
        except Exception as exc:
            payload = {"error": str(exc), "job_id": job_id}
            self.update_job(
                job_id,
                status="failed",
                stage="failed",
                progress=100,
                message=f"数据合并失败：{exc}",
                error=str(exc),
                finished_at=self._now_shanghai_iso(),
                log_message=f"数据合并失败：{exc}",
            )
            self._append_log("error", "后台数据导入失败", "admin_data_import", "failed", payload)
        finally:
            try:
                upload_file.unlink(missing_ok=True)
            except Exception:
                pass

    async def accept_upload(
        self,
        request: Any,
        background_tasks: Any,
        *,
        backup: bool = True,
        max_upload_mb: float = 1024.0,
        job_id_factory: Callable[[], str] | None = None,
    ) -> Dict[str, Any]:
        backup_dir = self._backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        max_bytes = int(max(1.0, safe_float(max_upload_mb, 1024.0)) * 1024 * 1024)
        upload_fd, upload_name = tempfile.mkstemp(prefix="qt_data_upload_", suffix=".tar.gz", dir=str(backup_dir))
        os.close(upload_fd)
        upload_file: Path | None = Path(upload_name)
        received = 0
        try:
            with upload_file.open("wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > max_bytes:
                        raise DataImportUploadError(413, "data package exceeds the server upload limit")
                    handle.write(chunk)
            if received <= 0:
                raise DataImportUploadError(400, "uploaded file is empty")
            validation = validate_data_package(upload_file)
            job_id = job_id_factory() if job_id_factory else uuid.uuid4().hex[:16]
            self.update_job(
                job_id,
                status="queued",
                stage="queued",
                progress=15,
                message="data package uploaded; waiting for background merge",
                received_bytes=received,
                validation=validation,
                upload_file=str(upload_file),
                log_message="data package uploaded; waiting for background merge",
            )
            background_tasks.add_task(self.run_data_import_job, job_id, upload_file, received, backup)
            upload_file = None
            return {
                "status": "accepted",
                "job_id": job_id,
                "message": "data package uploaded; merge is running in the background",
                "received_bytes": received,
                "validation": validation,
            }
        except DataPackageError as exc:
            self._append_log(
                "error",
                "admin data import rejected",
                "admin_data_import",
                "rejected",
                {"error": str(exc)},
            )
            raise
        finally:
            if upload_file is not None:
                try:
                    upload_file.unlink(missing_ok=True)
                except Exception:
                    pass

    @staticmethod
    def _now_shanghai_iso() -> str:
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
