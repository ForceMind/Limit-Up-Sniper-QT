from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class DataCollectionService:
    def __init__(
        self,
        *,
        biying_status: Callable[[], Dict[str, Any]],
        lhb_status: Callable[[], Dict[str, Any]],
        job_manager: Any,
    ) -> None:
        self._biying_status = biying_status
        self._lhb_status = lhb_status
        self._job_manager = job_manager

    def biying_status_payload(self) -> Dict[str, Any]:
        return self._biying_status()

    def kline_fill_payload(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        max_codes: int,
        force: bool,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_kline_fill(
            start_date=start_date,
            end_date=end_date,
            max_codes=max_codes,
            force=force,
            background=background,
            process=process,
        )

    def lhb_status_payload(self) -> Dict[str, Any]:
        return self._lhb_status()

    def lhb_sync_payload(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        max_stock_days: int,
        force: bool,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_lhb_sync(
            start_date=start_date,
            end_date=end_date,
            max_stock_days=max_stock_days,
            force=force,
            refresh_events=True,
            background=background,
            process=process,
        )

    def intraday_sync_payload(
        self,
        date: Optional[str],
        source: str,
        max_codes: int,
        codes: Optional[str],
        force: bool,
        include_latest: bool,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._job_manager.run_market_sync(
            date=date,
            source=source,
            max_codes=max_codes,
            codes=codes,
            force=force,
            include_latest=include_latest,
            background=background,
            process=process,
        )
