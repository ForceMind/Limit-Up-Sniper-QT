from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.quant.engine_utils import digits6, item_datetime, safe_float, short_hash
from app.quant.event_models import NewsEvent


MentionExtractor = Callable[[str, int], List[Tuple[str, str]]]
SampleEventFilter = Callable[[NewsEvent], bool]


def build_news_feed_payload(
    news_history: Iterable[Dict[str, Any]],
    events: Iterable[NewsEvent],
    *,
    extract_mentions: MentionExtractor,
    is_sample_event: SampleEventFilter,
    as_of: Optional[str] = None,
    limit: int = 120,
    fallback_latest: bool = True,
    source: Optional[str] = None,
    keyword: Optional[str] = None,
    code: Optional[str] = None,
    now: Callable[[], datetime] = datetime.now,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 120), 1000))
    source_filter = {part.strip().lower() for part in str(source or "").split(",") if part.strip()}
    keyword_filter = str(keyword or "").strip().lower()
    code_filter = digits6(code or "")
    rows = []
    for item in news_history:
        if not isinstance(item, dict):
            continue
        dt = item_datetime(item)
        if not dt:
            continue
        date = dt.strftime("%Y-%m-%d")
        if as_of and date > as_of:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        item_source = str(item.get("source") or "未知来源").strip() or "未知来源"
        if source_filter and item_source.lower() not in source_filter:
            continue
        if keyword_filter:
            haystack = " ".join(
                [
                    str(item.get("id") or ""),
                    item_source,
                    str(item.get("title") or ""),
                    text,
                ]
            ).lower()
            if keyword_filter not in haystack:
                continue
        if code_filter:
            mentions = extract_mentions(text, 20)
            mentioned_codes = {digits6(raw_code) for raw_code, _ in mentions}
            if code_filter not in mentioned_codes and code_filter not in text:
                continue
        timestamp = int(safe_float(item.get("timestamp"), dt.timestamp()))
        rows.append(
            {
                "id": str(item.get("id") or short_hash(f"{timestamp}|{item_source}|{text}")),
                "date": date,
                "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "source": item_source,
                "text": text,
                "timestamp": timestamp,
            }
        )

    rows.sort(key=lambda item: (item["timestamp"], item["time"]), reverse=True)
    available_dates = sorted({item["date"] for item in rows}, reverse=True)
    requested_date = as_of or (available_dates[0] if available_dates else now().strftime("%Y-%m-%d"))
    exact_rows = [item for item in rows if item["date"] == requested_date]
    data_date = requested_date if exact_rows else ""
    selected = exact_rows
    has_requested_date_data = bool(exact_rows)
    if not selected and fallback_latest:
        fallback_dates = [date for date in available_dates if not requested_date or date <= requested_date]
        data_date = fallback_dates[0] if fallback_dates else (available_dates[0] if available_dates else "")
        selected = [item for item in rows if item["date"] == data_date] if data_date else rows

    event_items = []
    if data_date:
        event_items = [
            event.compact()
            for event in sorted(
                [event for event in events if event.date == data_date],
                key=lambda event: (event.timestamp, event.impact_score),
                reverse=True,
            )
            if not is_sample_event(event)
        ][:limit]

    return {
        "status": "ok",
        "requested_date": requested_date,
        "data_date": data_date,
        "latest_available_date": available_dates[0] if available_dates else "",
        "has_requested_date_data": has_requested_date_data,
        "count": len(selected),
        "items": selected[:limit],
        "events": event_items,
        "available_dates": available_dates[:60],
        "filters": {
            "source": sorted(source_filter),
            "keyword": keyword_filter,
            "code": code_filter,
        },
    }
