from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


RequiredScopeForApi = Callable[[str, str], Optional[str]]
ClientIpFromRequest = Callable[[Request], str]
IsIpBlocked = Callable[[Any], bool]
RequireRequestScope = Callable[[Request, str], Dict[str, Any]]
VerifyToken = Callable[[str, str], Dict[str, Any]]
RecordAccess = Callable[[Request, int, float, Optional[Dict[str, Any]]], None]


class ApiAuthAuditMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        required_scope_for_api: RequiredScopeForApi,
        client_ip_from_request: ClientIpFromRequest,
        is_ip_blocked: IsIpBlocked,
        require_request_scope: RequireRequestScope,
        verify_token: VerifyToken,
        record_access: RecordAccess,
        blocked_message: str = "当前 IP 已被访问审计封禁",
    ) -> None:
        super().__init__(app)
        self._required_scope_for_api = required_scope_for_api
        self._client_ip_from_request = client_ip_from_request
        self._is_ip_blocked = is_ip_blocked
        self._require_request_scope = require_request_scope
        self._verify_token = verify_token
        self._record_access = record_access
        self._blocked_message = blocked_message

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        auth_payload: Optional[Dict[str, Any]] = None
        status_code = 500
        required_scope = self._required_scope_for_api(request.url.path, request.method)
        try:
            if self._is_ip_blocked(self._client_ip_from_request(request)):
                status_code = 403
                return JSONResponse({"detail": self._blocked_message}, status_code=403)
            if required_scope:
                try:
                    auth_payload = self._require_request_scope(request, required_scope)
                except HTTPException as exc:
                    status_code = exc.status_code
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            else:
                authorization = request.headers.get("authorization") or request.headers.get("Authorization") or ""
                token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
                token = token or str(request.headers.get("x-qt-token") or "").strip()
                if token:
                    try:
                        auth_payload = self._verify_token(token, "frontend")
                    except HTTPException:
                        auth_payload = None
            request.state.auth_payload = auth_payload
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            self._record_access(request, status_code, (time.perf_counter() - started) * 1000, auth_payload)
