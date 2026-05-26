from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.quant.event_models import NewsEvent


DailySync = Callable[..., Dict[str, Any]]


def sync_daily_kline_for_events(
    events: Iterable[NewsEvent],
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    hold_days: int = 3,
    max_codes: int = 300,
    force: bool = False,
    sync_daily_for_codes: DailySync,
) -> Dict[str, Any]:
    event_list = list(events)
    if not event_list:
        return {
            "status": "no_events",
            "event_start_date": start_date,
            "event_end_date": end_date,
            "fetch_end_date": end_date,
            "fetched": 0,
            "added_rows": 0,
            "updated_rows": 0,
        }
    start = start_date or min(event.date for event in event_list)
    end = end_date or max(event.date for event in event_list)
    try:
        end_dt = datetime.strptime(end[:10], "%Y-%m-%d") + timedelta(days=max(3, int(hold_days or 3) * 3))
        fetch_end = end_dt.strftime("%Y-%m-%d")
    except Exception:
        fetch_end = end

    best_by_code: Dict[str, NewsEvent] = {}
    for event in event_list:
        old = best_by_code.get(event.code)
        if old is None or (event.date, event.impact_score, event.timestamp) > (old.date, old.impact_score, old.timestamp):
            best_by_code[event.code] = event
    ranked_codes = [
        code
        for code, _event in sorted(
            best_by_code.items(),
            key=lambda item: (item[1].impact_score, item[1].date, item[1].timestamp),
            reverse=True,
        )
    ][: max(1, min(int(max_codes or 300), 5000))]

    result = sync_daily_for_codes(
        ranked_codes,
        start_date=start,
        end_date=fetch_end,
        max_codes=max_codes,
        force=force,
    )
    result["event_start_date"] = start
    result["event_end_date"] = end
    result["fetch_end_date"] = fetch_end
    return result
