from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.middleware.auth_audit import ApiAuthAuditMiddleware


def build_app_shell(
    *,
    title: str,
    version: str,
    lifespan: Any,
    frontend_static_dir: Path,
    required_scope_for_api: Callable[..., Any],
    client_ip_from_request: Callable[..., Any],
    is_ip_blocked: Callable[..., Any],
    require_request_scope: Callable[..., Any],
    verify_token: Callable[..., Any],
    record_access: Callable[..., Any],
) -> FastAPI:
    app = FastAPI(
        title=title,
        version=version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if frontend_static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(frontend_static_dir)), name="static")

    app.add_middleware(
        ApiAuthAuditMiddleware,
        required_scope_for_api=required_scope_for_api,
        client_ip_from_request=client_ip_from_request,
        is_ip_blocked=is_ip_blocked,
        require_request_scope=require_request_scope,
        verify_token=verify_token,
        record_access=record_access,
    )
    return app
