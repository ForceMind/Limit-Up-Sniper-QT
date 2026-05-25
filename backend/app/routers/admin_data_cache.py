from typing import Any, Callable, Dict

from fastapi import APIRouter, Query


AdminDatabaseTablesPayload = Callable[[], Dict[str, Any]]
AdminDatabaseTablePayload = Callable[[str, int, int], Dict[str, Any]]
AdminCacheStatusPayload = Callable[[], Dict[str, Any]]
AdminCacheClearPayload = Callable[[str], Dict[str, Any]]


def build_admin_data_cache_router(
    database_tables_payload: AdminDatabaseTablesPayload,
    database_table_payload: AdminDatabaseTablePayload,
    cache_status_payload: AdminCacheStatusPayload,
    cache_clear_payload: AdminCacheClearPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/database/tables")
    def admin_database_tables():
        return database_tables_payload()

    @router.get("/api/admin/database/table/{table_name}")
    def admin_database_table(
        table_name: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        return database_table_payload(
            table_name,
            limit,
            offset,
        )

    @router.get("/api/admin/cache/status")
    def admin_cache_status():
        return cache_status_payload()

    @router.post("/api/admin/cache/clear")
    def admin_cache_clear(scope: str = Query(default="expired")):
        return cache_clear_payload(scope)

    return router
