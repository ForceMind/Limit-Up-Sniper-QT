import inspect
from typing import Any, Awaitable, Callable, Dict, Optional, Union

from fastapi import APIRouter, Query


JobStatusPayload = Callable[[bool], Dict[str, Any]]
JobLogsPayload = Callable[[int, Optional[str], Optional[str]], Dict[str, Any]]
JobSimplePayload = Callable[[], Union[Dict[str, Any], Awaitable[Dict[str, Any]]]]
JobNamePayload = Callable[[str], Dict[str, Any]]


async def _maybe_await(result: Union[Dict[str, Any], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
    if inspect.isawaitable(result):
        return await result
    return result


def build_admin_jobs_router(
    status_payload: JobStatusPayload,
    logs_payload: JobLogsPayload,
    scheduler_start_payload: JobSimplePayload,
    scheduler_stop_payload: JobSimplePayload,
    pause_payload: JobNamePayload,
    resume_payload: JobNamePayload,
    stop_payload: JobNamePayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs/status")
    def jobs_status(light: bool = Query(default=True)):
        return status_payload(light)

    @router.get("/api/jobs/logs")
    def jobs_logs(
        limit: int = Query(default=200, ge=1, le=1000),
        level: Optional[str] = Query(default=None),
        job: Optional[str] = Query(default=None),
    ):
        return logs_payload(limit, level, job)

    @router.post("/api/jobs/scheduler/start")
    async def jobs_scheduler_start():
        return await _maybe_await(scheduler_start_payload())

    @router.post("/api/jobs/scheduler/stop")
    async def jobs_scheduler_stop():
        return await _maybe_await(scheduler_stop_payload())

    @router.post("/api/jobs/{job_name}/pause")
    def jobs_pause(job_name: str):
        return pause_payload(job_name)

    @router.post("/api/jobs/{job_name}/resume")
    def jobs_resume(job_name: str):
        return resume_payload(job_name)

    @router.post("/api/jobs/{job_name}/stop")
    def jobs_stop(job_name: str):
        return stop_payload(job_name)

    @router.get("/api/logs/runtime")
    def runtime_logs(
        limit: int = Query(default=200, ge=1, le=1000),
        level: Optional[str] = Query(default=None),
        job: Optional[str] = Query(default=None),
    ):
        return logs_payload(limit, level, job)

    return router
