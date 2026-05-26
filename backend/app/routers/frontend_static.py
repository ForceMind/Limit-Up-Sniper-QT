from typing import Any, Callable

from fastapi import APIRouter, HTTPException


ResponsePayload = Callable[[], Any]
AdminEntryPayload = Callable[[], str]


def build_frontend_static_router(
    *,
    index_response_payload: ResponsePayload,
    admin_index_response_payload: ResponsePayload,
    admin_entry_path_payload: AdminEntryPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/", include_in_schema=False)
    def index():
        return index_response_payload()

    @router.get("/index.html", include_in_schema=False)
    def index_html():
        return index_response_payload()

    @router.get("/{full_path:path}", include_in_schema=False)
    def configured_static_entry(full_path: str):
        request_path = "/" + str(full_path or "").strip("/")
        if request_path in {"/api", "/static"} or request_path.startswith(("/api/", "/static/")):
            raise HTTPException(status_code=404, detail="Not Found")
        admin_entry = admin_entry_path_payload().rstrip("/")
        if request_path in {admin_entry, f"{admin_entry}/index.html"}:
            return admin_index_response_payload()
        raise HTTPException(status_code=404, detail="Not Found")

    return router
