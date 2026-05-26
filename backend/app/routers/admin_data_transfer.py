from typing import Any, Awaitable, Callable, Dict

from fastapi import APIRouter, BackgroundTasks, Query, Request


SimplePayload = Callable[[], Dict[str, Any]]
ExportResponsePayload = Callable[[bool], Any]
ImportStatusPayload = Callable[[str], Dict[str, Any]]
ImportPayload = Callable[[Request, BackgroundTasks, bool], Awaitable[Dict[str, Any]]]


def build_admin_data_transfer_router(
    *,
    backup_payload: SimplePayload,
    export_response_payload: ExportResponsePayload,
    import_status_payload: ImportStatusPayload,
    import_payload: ImportPayload,
    clear_sample_state_payload: SimplePayload,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/admin/backup")
    def admin_backup():
        return backup_payload()

    @router.get("/api/admin/data/export")
    def admin_data_export(include_logs: bool = Query(default=False)):
        return export_response_payload(include_logs)

    @router.get("/api/admin/data/import/{job_id}")
    def admin_data_import_status(job_id: str):
        return import_status_payload(job_id)

    @router.post("/api/admin/data/import")
    async def admin_data_import(
        request: Request,
        background_tasks: BackgroundTasks,
        backup: bool = Query(default=True),
    ):
        return await import_payload(request, background_tasks, backup)

    @router.post("/api/admin/data/clear_sample_state")
    def admin_clear_sample_state():
        return clear_sample_state_payload()

    return router
