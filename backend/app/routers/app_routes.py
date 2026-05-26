from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI

from app.routers.admin_routes import register_admin_routes
from app.routers.core_routes import register_core_routes
from app.routers.frontend_routes import register_frontend_routes, register_frontend_static_routes
from app.routers.operations_routes import register_operations_routes
from app.routers.quant_research_routes import register_quant_research_routes


class _RouteDependencies:
    def kwargs(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class CoreRouteDependencies(_RouteDependencies):
    core_system_service: Any
    runtime_status_service: Any
    auth_status_payload: Callable[[], Any]
    auth_setup_payload: Callable[..., Any]
    config_status_payload: Callable[[], Any]
    config_runtime_payload: Callable[[], Any]


@dataclass(frozen=True)
class FrontendRouteDependencies(_RouteDependencies):
    profile_read_service: Any
    profile_update_service: Any
    snapshot_read_service: Any
    runtime_read_service: Any
    signal_read_service: Any
    route_defaults: Any


@dataclass(frozen=True)
class AdminRouteDependencies(_RouteDependencies):
    snapshot_service: Any
    strategy_runtime_service: Any
    runtime_status_service: Any
    admin_job_run_service: Any
    system_startup_service: Any
    data_maintenance_service: Any
    access_service: Any
    frontend_user_service: Any
    system_control_service: Any
    job_manager: Any
    route_defaults: Any
    verify_admin_token: Callable[[str], Any]
    biying_status_payload: Callable[[], Any]
    live_log_key: Callable[..., Any]
    live_fingerprint: Callable[..., Any]


@dataclass(frozen=True)
class QuantResearchRouteDependencies(_RouteDependencies):
    basic_service: Any
    strategy_research_service: Any
    timeline_service: Any
    backtest_service: Any
    route_defaults: Any


@dataclass(frozen=True)
class OperationsRouteDependencies(_RouteDependencies):
    data_collection_service: Any
    data_coverage_service: Any
    ai_monitoring_service: Any
    route_defaults: Any


@dataclass(frozen=True)
class FrontendStaticRouteDependencies(_RouteDependencies):
    static_response_service: Any
    admin_entry_path_payload: Callable[[], str]


@dataclass(frozen=True)
class ApplicationRouteDependencies:
    core: CoreRouteDependencies
    frontend: FrontendRouteDependencies
    admin: AdminRouteDependencies
    quant_research: QuantResearchRouteDependencies
    operations: OperationsRouteDependencies
    frontend_static: FrontendStaticRouteDependencies


def register_application_routes(app: FastAPI, dependencies: ApplicationRouteDependencies) -> None:
    register_core_routes(app, **dependencies.core.kwargs())
    register_frontend_routes(app, **dependencies.frontend.kwargs())
    register_admin_routes(app, **dependencies.admin.kwargs())
    register_quant_research_routes(app, **dependencies.quant_research.kwargs())
    register_operations_routes(app, **dependencies.operations.kwargs())

    # Static frontend/admin entry routes must stay last so API paths keep their 404 behavior.
    register_frontend_static_routes(app, **dependencies.frontend_static.kwargs())
