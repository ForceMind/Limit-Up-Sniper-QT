from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query


AdminSnapshotPayload = Callable[[Optional[str], bool], Dict[str, Any]]
AdminModelSignalsPayload = Callable[[Optional[str], int, int], Dict[str, Any]]


def build_admin_overview_router(
    snapshot_payload: AdminSnapshotPayload,
    model_signals_payload: AdminModelSignalsPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/snapshot")
    def admin_snapshot(
        as_of: Optional[str] = Query(default=None),
        light: bool = Query(default=True),
    ):
        return snapshot_payload(
            as_of,
            light,
        )

    @router.get("/api/admin/model_signals")
    def admin_model_signals(
        as_of: Optional[str] = Query(default=None),
        limit_models: int = Query(default=24, ge=1, le=80),
        limit_per_model: int = Query(default=12, ge=1, le=80),
    ):
        return model_signals_payload(
            as_of,
            limit_models,
            limit_per_model,
        )

    return router
