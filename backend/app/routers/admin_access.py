from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from app.quant.admin_access_service import AdminAccessPayloadError


AdminAccessLogsPayload = Callable[[int, int, Optional[str], Optional[str], Optional[str], Optional[int]], Dict[str, Any]]
AdminAccessSecurityPayload = Callable[[int], Dict[str, Any]]
AdminAccessSecurityMutationPayload = Callable[[Dict[str, Any]], Dict[str, Any]]


def build_admin_access_router(
    access_logs_payload: AdminAccessLogsPayload,
    access_security_payload: AdminAccessSecurityPayload,
    block_payload: AdminAccessSecurityMutationPayload,
    unblock_payload: AdminAccessSecurityMutationPayload,
    block_all_payload: AdminAccessSecurityMutationPayload,
) -> APIRouter:
    router = APIRouter()

    def mutation_payload(payload_fn: AdminAccessSecurityMutationPayload, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return payload_fn(payload)
        except AdminAccessPayloadError as exc:
            raise HTTPException(status_code=400, detail=exc.detail) from exc

    @router.get("/api/admin/access_logs")
    def admin_access_logs(
        limit: int = Query(default=220, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        username: Optional[str] = Query(default=None),
        ip: Optional[str] = Query(default=None),
        path: Optional[str] = Query(default=None),
        status_code: Optional[int] = Query(default=None, ge=100, le=599),
    ):
        return access_logs_payload(
            limit,
            offset,
            username,
            ip,
            path,
            status_code,
        )

    @router.get("/api/admin/access_security")
    def admin_access_security(limit: int = Query(default=120, ge=1, le=500)):
        return access_security_payload(limit)

    @router.post("/api/admin/access_security/block")
    def admin_access_security_block(payload: Dict[str, Any] = Body(default_factory=dict)):
        return mutation_payload(block_payload, payload)

    @router.post("/api/admin/access_security/unblock")
    def admin_access_security_unblock(payload: Dict[str, Any] = Body(default_factory=dict)):
        return mutation_payload(unblock_payload, payload)

    @router.post("/api/admin/access_security/block_all")
    def admin_access_security_block_all(payload: Dict[str, Any] = Body(default_factory=dict)):
        return mutation_payload(block_all_payload, payload)

    return router
