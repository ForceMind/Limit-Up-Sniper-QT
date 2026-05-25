from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query, Request


FrontendPublicSnapshotPayload = Callable[[Optional[str], bool, bool], Dict[str, Any]]
FrontendSnapshotPayload = Callable[[Request, Optional[str], bool, bool, bool], Dict[str, Any]]
FrontendStrategyModelsPayload = Callable[[Request], Dict[str, Any]]
FrontendTradingAccountPayload = Callable[[Request, Optional[str], int, bool, bool], Dict[str, Any]]


def build_frontend_runtime_router(
    public_snapshot_payload: FrontendPublicSnapshotPayload,
    snapshot_payload: FrontendSnapshotPayload,
    strategy_models_payload: FrontendStrategyModelsPayload,
    trading_account_payload: FrontendTradingAccountPayload,
    account_defer_default: bool = True,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/front/public_snapshot")
    def frontend_public_snapshot(
        as_of: Optional[str] = Query(default=None),
        mobile: bool = Query(default=False),
        light: bool = Query(default=True),
    ):
        return public_snapshot_payload(
            as_of,
            mobile,
            light,
        )

    @router.get("/api/front/snapshot")
    def frontend_snapshot(
        request: Request,
        as_of: Optional[str] = Query(default=None),
        mobile: bool = Query(default=False),
        light: bool = Query(default=True),
        include_catalog: bool = Query(default=False),
    ):
        return snapshot_payload(
            request,
            as_of,
            mobile,
            light,
            include_catalog,
        )

    @router.get("/api/front/strategy_models")
    def frontend_strategy_models(request: Request):
        return strategy_models_payload(request)

    @router.get("/api/front/trading_account")
    def frontend_trading_account(
        request: Request,
        as_of: Optional[str] = Query(default=None),
        limit: int = Query(default=500, ge=1, le=2000),
        force: bool = Query(default=False),
        defer: bool = Query(default=account_defer_default),
    ):
        return trading_account_payload(
            request,
            as_of,
            limit,
            force,
            defer,
        )

    return router
