from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Query


SimplePayload = Callable[[], Dict[str, Any]]
DataCoveragePayload = Callable[[Optional[str], int, bool, bool, bool], Dict[str, Any]]
KlineFillPayload = Callable[[Optional[str], Optional[str], int, bool, bool, bool], Dict[str, Any]]
LhbSyncPayload = Callable[[Optional[str], Optional[str], int, bool, bool, bool], Dict[str, Any]]
IntradaySyncPayload = Callable[[Optional[str], str, int, Optional[str], bool, bool, bool, bool], Dict[str, Any]]


def build_data_collection_router(
    *,
    biying_status_payload: SimplePayload,
    data_coverage_payload: DataCoveragePayload,
    kline_fill_payload: KlineFillPayload,
    lhb_status_payload: SimplePayload,
    lhb_sync_payload: LhbSyncPayload,
    intraday_sync_payload: IntradaySyncPayload,
    coverage_defer_default: bool,
    coverage_process_default: bool,
    kline_process_default: bool,
    lhb_process_default: bool,
    market_process_default: bool,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/data/biying/status")
    def biying_status():
        return biying_status_payload()

    @router.get("/api/data/coverage")
    def quant_data_coverage(
        as_of: Optional[str] = Query(default=None),
        top_n: int = Query(default=80, ge=1, le=300),
        force: bool = Query(default=False),
        defer: bool = Query(default=coverage_defer_default),
        process: bool = Query(default=coverage_process_default),
    ):
        return data_coverage_payload(as_of, top_n, force, defer, process)

    @router.post("/api/data/kline/fill")
    def data_kline_fill(
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        max_codes: int = Query(default=300, ge=1, le=5000),
        force: bool = Query(default=False),
        background: bool = Query(default=True),
        process: bool = Query(default=kline_process_default),
    ):
        return kline_fill_payload(start_date, end_date, max_codes, force, background, process)

    @router.get("/api/data/lhb/status")
    def data_lhb_status():
        return lhb_status_payload()

    @router.post("/api/data/lhb/sync")
    def data_lhb_sync(
        start_date: Optional[str] = Query(default=None),
        end_date: Optional[str] = Query(default=None),
        max_stock_days: int = Query(default=300, ge=1, le=2000),
        force: bool = Query(default=False),
        background: bool = Query(default=True),
        process: bool = Query(default=lhb_process_default),
    ):
        return lhb_sync_payload(start_date, end_date, max_stock_days, force, background, process)

    @router.post("/api/data/biying/sync_intraday")
    def biying_sync_intraday(
        date: Optional[str] = Query(default=None),
        source: str = Query(default="events"),
        max_codes: int = Query(default=200, ge=1, le=5000),
        codes: Optional[str] = Query(default=None),
        force: bool = Query(default=False),
        include_latest: bool = Query(default=True),
        background: bool = Query(default=True),
        process: bool = Query(default=market_process_default),
    ):
        return intraday_sync_payload(
            date,
            source,
            max_codes,
            codes,
            force,
            include_latest,
            background,
            process,
        )

    return router
