from typing import Any, Callable, Dict

from fastapi import APIRouter, Body, Request


AdminFrontendUsersPayload = Callable[[], Dict[str, Any]]
AdminFrontendUserCreatePayload = Callable[[Request, Dict[str, Any]], Dict[str, Any]]
AdminFrontendUserMutationPayload = Callable[[str, Dict[str, Any]], Dict[str, Any]]
AdminFrontendUserSimplePayload = Callable[[str], Dict[str, Any]]


def build_admin_frontend_users_router(
    list_users_payload: AdminFrontendUsersPayload,
    create_user_payload: AdminFrontendUserCreatePayload,
    update_user_payload: AdminFrontendUserMutationPayload,
    reset_password_payload: AdminFrontendUserMutationPayload,
    ban_user_payload: AdminFrontendUserMutationPayload,
    unban_user_payload: AdminFrontendUserSimplePayload,
    delete_user_payload: AdminFrontendUserSimplePayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/frontend_users")
    def admin_frontend_users():
        return list_users_payload()

    @router.post("/api/admin/frontend_users")
    def admin_create_frontend_user(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
        return create_user_payload(request, payload)

    @router.patch("/api/admin/frontend_users/{username}")
    def admin_update_frontend_user(username: str, payload: Dict[str, Any] = Body(default_factory=dict)):
        return update_user_payload(username, payload)

    @router.post("/api/admin/frontend_users/{username}/password")
    def admin_reset_frontend_user_password(username: str, payload: Dict[str, Any] = Body(default_factory=dict)):
        return reset_password_payload(username, payload)

    @router.post("/api/admin/frontend_users/{username}/ban")
    def admin_ban_frontend_user(username: str, payload: Dict[str, Any] = Body(default_factory=dict)):
        return ban_user_payload(username, payload)

    @router.post("/api/admin/frontend_users/{username}/unban")
    def admin_unban_frontend_user(username: str):
        return unban_user_payload(username)

    @router.delete("/api/admin/frontend_users/{username}")
    def admin_delete_frontend_user(username: str):
        return delete_user_payload(username)

    return router
