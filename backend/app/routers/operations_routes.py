from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.routers.ai_monitoring import build_ai_monitoring_router
from app.routers.data_collection import build_data_collection_router


def register_operations_routes(
    app: FastAPI,
    *,
    data_collection_service: Any,
    data_coverage_service: Any,
    ai_monitoring_service: Any,
    route_defaults: Any,
) -> None:
    app.include_router(
        build_data_collection_router(
            biying_status_payload=data_collection_service.biying_status_payload,
            data_coverage_payload=data_coverage_service.route_payload,
            kline_fill_payload=data_collection_service.kline_fill_payload,
            lhb_status_payload=data_collection_service.lhb_status_payload,
            lhb_sync_payload=data_collection_service.lhb_sync_payload,
            intraday_sync_payload=data_collection_service.intraday_sync_payload,
            **route_defaults.data_collection_kwargs(),
        )
    )
    app.include_router(
        build_ai_monitoring_router(
            usage_payload=ai_monitoring_service.usage_payload,
            records_payload=ai_monitoring_service.records_payload,
            failures_payload=ai_monitoring_service.failures_payload,
        )
    )
