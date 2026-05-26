from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


AggregateStats = Callable[[List[float]], Dict[str, Any]]
FutureReturn = Callable[[str, str, int], Optional[Dict[str, Any]]]
KlineLoader = Callable[[str], List[Dict[str, Any]]]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "--"}:
            return default
        return float(text)
    except Exception:
        return default


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def backtest_event_score(events: Iterable[Any]) -> float:
    items = list(events)
    if not items:
        return 0.0
    impact = statistics.mean(_safe_float(getattr(event, "impact_score", 0), 0) for event in items)
    sentiment = statistics.mean(_safe_float(getattr(event, "sentiment", 0), 0) for event in items)
    ai_score = max((_safe_float(getattr(event, "ai_score", 0), 0) for event in items), default=0.0)
    score = 45 + impact * 0.35 + sentiment * 20
    if ai_score > 0:
        score += (ai_score - 5) * 3
    return _clamp(score)


def backtest_event_outcome_summary(
    trades: List[Dict[str, Any]],
    *,
    top_n: int,
    aggregate_stats: AggregateStats,
) -> Dict[str, Any]:
    returns = [_safe_float(item.get("return_pct"), 0) for item in trades]
    compounded = 1.0
    equity_curve = []
    for ret in returns:
        compounded *= 1 + (ret / 100.0) / max(1, int(top_n or 1))
        equity_curve.append(compounded)
    peak = 1.0
    max_drawdown = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)

    buckets: Dict[str, List[float]] = {"58-65": [], "65-72": [], "72-80": [], "80-100": []}
    for trade in trades:
        score = _safe_float(trade.get("score"), 0)
        ret = _safe_float(trade.get("return_pct"), 0)
        if score < 65:
            buckets["58-65"].append(ret)
        elif score < 72:
            buckets["65-72"].append(ret)
        elif score < 80:
            buckets["72-80"].append(ret)
        else:
            buckets["80-100"].append(ret)

    return {
        "returns": returns,
        "avg_return_pct": round(statistics.mean(returns), 3) if returns else 0.0,
        "median_return_pct": round(statistics.median(returns), 3) if returns else 0.0,
        "win_rate": round(sum(1 for ret in returns if ret > 0) / len(returns) * 100, 2) if returns else 0.0,
        "compounded_return_pct": round((compounded - 1) * 100, 3),
        "max_drawdown_pct": round(max_drawdown * 100, 3),
        "score_buckets": {key: aggregate_stats(value) for key, value in buckets.items()},
    }


def backtest_data_diagnostics(
    *,
    events: Iterable[Any],
    start_date: Optional[str],
    end_date: Optional[str],
    hold_days: int,
    load_kline: KlineLoader,
    future_return: FutureReturn,
    is_tradeable: Callable[[str], bool],
    sqlite_file: Path,
) -> Dict[str, Any]:
    scoped_events = [
        event
        for event in events
        if (not start_date or event.date >= start_date)
        and (not end_date or event.date <= end_date)
        and is_tradeable(event.code)
    ]
    codes = sorted({event.code for event in scoped_events})
    missing_daily = []
    insufficient_forward = []
    covered = 0
    for code in codes:
        rows = load_kline(code)
        if not rows:
            missing_daily.append(code)
            continue
        event_dates = [event.date for event in scoped_events if event.code == code]
        has_forward = False
        for event_date in event_dates[:20]:
            if future_return(code, event_date, hold_days):
                has_forward = True
                break
        if has_forward:
            covered += 1
        else:
            insufficient_forward.append(code)
    warnings = []
    if not scoped_events:
        warnings.append("no_events_in_range")
    if missing_daily:
        warnings.append("missing_daily_kline")
    if insufficient_forward:
        warnings.append("insufficient_forward_kline")
    return {
        "event_count": len(scoped_events),
        "event_stock_count": len(codes),
        "daily_kline_covered_stock_count": covered,
        "missing_daily_kline_count": len(missing_daily),
        "insufficient_forward_kline_count": len(insufficient_forward),
        "missing_daily_kline_codes": missing_daily[:50],
        "insufficient_forward_kline_codes": insufficient_forward[:50],
        "warnings": warnings,
        "sqlite_enabled": sqlite_file.exists(),
        "sqlite_file": str(sqlite_file),
    }
