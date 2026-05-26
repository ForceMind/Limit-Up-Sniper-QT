from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from app.quant.engine_utils import safe_float


AggregateStats = Callable[[List[float]], Dict[str, Any]]
FutureReturn = Callable[[str, str, int], Optional[Dict[str, Any]]]
SamplePredicate = Callable[[Any], bool]


def build_correlation_payload(
    events: Iterable[Any],
    *,
    as_of: str,
    hold_days: int,
    realized_by: str,
    future_return: FutureReturn,
    aggregate_stats: AggregateStats,
    is_sample_event: SamplePredicate,
    max_events: int,
) -> Dict[str, Any]:
    scoped_events = [
        event
        for event in events
        if not is_sample_event(event) and str(getattr(event, "date", "")) < as_of
    ]
    if len(scoped_events) > max_events:
        scoped_events.sort(
            key=lambda item: (
                str(getattr(item, "date", "")),
                safe_float(getattr(item, "timestamp", 0), 0),
                safe_float(getattr(item, "impact_score", 0), 0),
            ),
            reverse=True,
        )
        scoped_events = scoped_events[:max_events]

    by_code: Dict[str, List[float]] = {}
    by_theme: Dict[str, List[float]] = {}
    by_type: Dict[str, List[float]] = {}
    all_returns: List[float] = []
    for event in scoped_events:
        code = str(getattr(event, "code", "") or "")
        event_date = str(getattr(event, "date", "") or "")
        realized = future_return(code, event_date, hold_days)
        if not realized:
            continue
        if str(realized.get("exit_date", "")) > realized_by:
            continue
        ret = safe_float(realized.get("return_pct"), 0)
        event_type = str(getattr(event, "event_type", "") or "")
        industry = str(getattr(event, "industry", "") or "")
        all_returns.append(ret)
        by_code.setdefault(code, []).append(ret)
        by_theme.setdefault(f"{industry}|{event_type}", []).append(ret)
        by_type.setdefault(event_type, []).append(ret)

    return {
        "as_of": as_of,
        "realized_by": realized_by,
        "hold_days": hold_days,
        "sample_limit": max_events,
        "sample_scanned": len(scoped_events),
        "global": aggregate_stats(all_returns),
        "by_code": {key: aggregate_stats(val) for key, val in by_code.items() if len(val) >= 2},
        "by_theme": {key: aggregate_stats(val) for key, val in by_theme.items() if len(val) >= 3},
        "by_type": {key: aggregate_stats(val) for key, val in by_type.items() if len(val) >= 3},
    }
