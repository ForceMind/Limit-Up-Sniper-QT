from typing import Any, Callable, Dict

from fastapi import APIRouter, Body, Query, Request


FrontendProfilePayload = Callable[[Request], Dict[str, Any]]
FrontendProfileUpdatePayload = Callable[[Request, Dict[str, Any], bool], Dict[str, Any]]


def build_frontend_profile_router(
    profile_payload: FrontendProfilePayload,
    update_profile_payload: FrontendProfileUpdatePayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/front/profile")
    def api_front_profile(request: Request):
        return profile_payload(request)

    @router.post("/api/front/profile")
    def api_update_front_profile(
        request: Request,
        payload: Dict[str, Any] = Body(default_factory=dict),
        include_catalog: bool = Query(default=False),
    ):
        return update_profile_payload(
            request,
            payload,
            include_catalog,
        )

    return router
