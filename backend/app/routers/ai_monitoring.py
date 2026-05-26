from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query


AiUsagePayload = Callable[[], Dict[str, Any]]
AiRecordsPayload = Callable[[int, Optional[str], Optional[str]], Dict[str, Any]]
AiFailuresPayload = Callable[[int], Dict[str, Any]]


def build_ai_monitoring_router(
    *,
    usage_payload: AiUsagePayload,
    records_payload: AiRecordsPayload,
    failures_payload: AiFailuresPayload,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/ai/usage")
    def quant_ai_usage():
        return usage_payload()

    @router.get("/api/ai/records")
    def quant_ai_records(
        limit: int = Query(default=100, ge=1, le=500),
        code: Optional[str] = Query(default=None),
        source: Optional[str] = Query(default=None),
    ):
        return records_payload(limit, code, source)

    @router.get("/api/ai/failures")
    def quant_ai_failures(limit: int = Query(default=100, ge=1, le=500)):
        return failures_payload(limit)

    return router
