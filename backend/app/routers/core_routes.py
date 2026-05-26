from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from app.routers.core_system import build_core_system_router


def register_core_routes(
    app: FastAPI,
    *,
    core_system_service: Any,
    runtime_status_service: Any,
    auth_status_payload: Callable[[], Any],
    auth_setup_payload: Callable[..., Any],
    config_status_payload: Callable[[], Any],
    config_runtime_payload: Callable[[], Any],
) -> None:
    app.include_router(
        build_core_system_router(
            version_payload=core_system_service.version_payload,
            auth_status_payload=auth_status_payload,
            debug_status_payload=core_system_service.debug_status_payload,
            debug_routes_payload=core_system_service.debug_routes_payload,
            auth_setup_payload=auth_setup_payload,
            auth_login_payload=core_system_service.auth_login_payload,
            auth_register_payload=core_system_service.auth_register_payload,
            config_status_payload=config_status_payload,
            config_runtime_payload=config_runtime_payload,
            config_update_payload=core_system_service.config_update_payload,
            status_payload=runtime_status_service.status_payload,
        )
    )
