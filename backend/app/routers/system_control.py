from typing import Any, Callable, Dict

from fastapi import APIRouter, BackgroundTasks


RestartPayload = Callable[[BackgroundTasks], Dict[str, Any]]
SimplePayload = Callable[[], Dict[str, Any]]


def build_system_control_router(
    *,
    restart_payload: RestartPayload,
    notification_status_payload: SimplePayload,
    notification_test_payload: SimplePayload,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/admin/restart")
    def admin_restart(background_tasks: BackgroundTasks):
        return restart_payload(background_tasks)

    @router.get("/api/notifications/status")
    def notifications_status():
        return notification_status_payload()

    @router.post("/api/notifications/test")
    def notifications_test():
        return notification_test_payload()

    return router
