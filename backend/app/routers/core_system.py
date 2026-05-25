from typing import Any, Callable, Dict

from fastapi import APIRouter, Body, Request


SimplePayload = Callable[[], Dict[str, Any]]
RequestPayload = Callable[[Request], Dict[str, Any]]
BodyPayload = Callable[[Dict[str, Any]], Dict[str, Any]]
RequestBodyPayload = Callable[[Request, Dict[str, Any]], Dict[str, Any]]


def build_core_system_router(
    version_payload: SimplePayload,
    auth_status_payload: SimplePayload,
    debug_status_payload: RequestPayload,
    debug_routes_payload: SimplePayload,
    auth_setup_payload: BodyPayload,
    auth_login_payload: RequestBodyPayload,
    auth_register_payload: RequestBodyPayload,
    config_status_payload: SimplePayload,
    config_runtime_payload: SimplePayload,
    config_update_payload: BodyPayload,
    status_payload: SimplePayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/version")
    def api_version():
        return version_payload()

    @router.get("/api/auth/status")
    def api_auth_status():
        return auth_status_payload()

    @router.get("/api/debug/status")
    def api_debug_status(request: Request):
        return debug_status_payload(request)

    @router.get("/api/debug/routes")
    def api_debug_routes():
        return debug_routes_payload()

    @router.post("/api/auth/setup")
    def api_auth_setup(payload: Dict[str, Any] = Body(default_factory=dict)):
        return auth_setup_payload(payload)

    @router.post("/api/auth/login")
    def api_auth_login(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
        return auth_login_payload(request, payload)

    @router.post("/api/auth/register")
    def api_auth_register(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
        return auth_register_payload(request, payload)

    @router.get("/api/config/status")
    def api_config_status():
        return config_status_payload()

    @router.get("/api/config/runtime")
    def api_config_runtime():
        return config_runtime_payload()

    @router.post("/api/config/runtime")
    def api_update_config_runtime(payload: Dict[str, Any] = Body(default_factory=dict)):
        return config_update_payload(payload)

    @router.get("/api/status")
    def api_status():
        return status_payload()

    return router
