from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from app.routers.frontend_profile import build_frontend_profile_router
from app.routers.frontend_runtime import build_frontend_runtime_router
from app.routers.frontend_signal import build_frontend_signal_router
from app.routers.frontend_static import build_frontend_static_router


def register_frontend_routes(
    app: FastAPI,
    *,
    profile_read_service: Any,
    profile_update_service: Any,
    snapshot_read_service: Any,
    runtime_read_service: Any,
    signal_read_service: Any,
    route_defaults: Any,
) -> None:
    app.include_router(
        build_frontend_profile_router(
            profile_payload=profile_read_service.profile_payload,
            update_profile_payload=profile_update_service.update_profile_payload,
        )
    )
    app.include_router(
        build_frontend_runtime_router(
            public_snapshot_payload=snapshot_read_service.public_snapshot_payload,
            snapshot_payload=snapshot_read_service.snapshot_payload,
            strategy_models_payload=profile_read_service.strategy_models_route_payload,
            trading_account_payload=runtime_read_service.trading_account_payload,
            strategy_daily_payload=runtime_read_service.strategy_daily_payload,
            **route_defaults.frontend_runtime_kwargs(),
        )
    )
    app.include_router(
        build_frontend_signal_router(
            recommendations_payload=signal_read_service.recommendations_payload,
            daily_plan_payload=signal_read_service.daily_plan_payload,
            **route_defaults.frontend_signal_kwargs(),
        )
    )


def register_frontend_static_routes(
    app: FastAPI,
    *,
    static_response_service: Any,
    admin_entry_path_payload: Callable[[], str],
) -> None:
    app.include_router(
        build_frontend_static_router(
            index_response_payload=static_response_service.index_response,
            admin_index_response_payload=static_response_service.admin_index_response,
            admin_entry_path_payload=admin_entry_path_payload,
        )
    )
