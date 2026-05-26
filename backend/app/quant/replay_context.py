from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional


AggregateStats = Callable[[List[float]], Dict[str, Any]]
FutureReturn = Callable[[str, str, int], Optional[Dict[str, Any]]]
PerformanceMetrics = Callable[[List[Dict[str, Any]], List[Dict[str, Any]], float, float], Dict[str, Any]]
SamplePredicate = Callable[[Any], bool]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "--"}:
            return default
        return float(text)
    except Exception:
        return default


class ReplayCorrelationState:
    def __init__(
        self,
        historical_outcomes: Iterable[Dict[str, Any]],
        *,
        hold_days: int,
        aggregate_stats: AggregateStats,
    ) -> None:
        self._historical_outcomes = sorted(
            [item for item in historical_outcomes if isinstance(item, dict)],
            key=lambda item: str(item.get("exit_date") or ""),
        )
        self._hold_days = int(hold_days)
        self._aggregate_stats = aggregate_stats
        self._outcome_idx = 0
        self._global_returns: List[float] = []
        self._code_returns: Dict[str, List[float]] = {}
        self._theme_returns: Dict[str, List[float]] = {}
        self._type_returns: Dict[str, List[float]] = {}

    def add_realized_outcomes_until(self, date: str) -> None:
        while (
            self._outcome_idx < len(self._historical_outcomes)
            and str(self._historical_outcomes[self._outcome_idx].get("exit_date") or "") <= date
        ):
            item = self._historical_outcomes[self._outcome_idx]
            event = item.get("event")
            ret = safe_float(item.get("return_pct"), 0)
            code = str(getattr(event, "code", "") or "")
            industry = str(getattr(event, "industry", "") or "")
            event_type = str(getattr(event, "event_type", "") or "")
            self._global_returns.append(ret)
            if code:
                self._code_returns.setdefault(code, []).append(ret)
            if industry or event_type:
                self._theme_returns.setdefault(f"{industry}|{event_type}", []).append(ret)
            if event_type:
                self._type_returns.setdefault(event_type, []).append(ret)
            self._outcome_idx += 1

    def current_corr(self, date: str) -> Dict[str, Any]:
        return {
            "as_of": date,
            "realized_by": date,
            "hold_days": self._hold_days,
            "global": self._aggregate_stats(self._global_returns),
            "by_code": {
                key: self._aggregate_stats(val)
                for key, val in self._code_returns.items()
                if len(val) >= 2
            },
            "by_theme": {
                key: self._aggregate_stats(val)
                for key, val in self._theme_returns.items()
                if len(val) >= 3
            },
            "by_type": {
                key: self._aggregate_stats(val)
                for key, val in self._type_returns.items()
                if len(val) >= 3
            },
        }


def replay_final_metrics(
    equity_curve: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    initial_cash: float,
    *,
    performance_metrics: PerformanceMetrics,
) -> Dict[str, Any]:
    initial = max(1.0, safe_float(initial_cash, 1.0))
    final_value = safe_float(equity_curve[-1].get("total_value"), initial) if equity_curve else initial
    closed_sells = [trade for trade in trades if trade.get("side") == "SELL"]
    win_rate = (
        sum(1 for trade in closed_sells if safe_float(trade.get("pnl_pct"), 0) > 0) / len(closed_sells) * 100
        if closed_sells
        else 0.0
    )
    peak = initial
    max_drawdown = 0.0
    for point in equity_curve:
        value = safe_float(point.get("total_value"), initial)
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    performance = performance_metrics(equity_curve, trades, initial, final_value)
    return {
        "final_value": round(final_value, 2),
        "return_pct": round((final_value / initial - 1) * 100, 3),
        "max_drawdown_pct": round(max_drawdown * 100, 3),
        "annualized_return_pct": performance["annualized_return_pct"],
        "sharpe_ratio": performance["sharpe_ratio"],
        "profit_factor": performance["profit_factor"],
        "total_fees": performance["total_fees"],
        "exposure_pct": performance["exposure_pct"],
        "closed_trades": len(closed_sells),
        "win_rate": round(win_rate, 2),
        "performance": performance,
    }


def historical_outcomes_for_replay(
    scoped_events: Iterable[Any],
    all_events: Iterable[Any],
    *,
    start_date: Optional[str],
    end_date: Optional[str],
    hold_days: int,
    history_limit: int,
    future_return: FutureReturn,
    is_sample_event: SamplePredicate,
) -> List[Dict[str, Any]]:
    scoped = list(scoped_events)
    if not scoped or history_limit <= 0:
        return []
    codes = {str(getattr(event, "code", "") or "") for event in scoped if getattr(event, "code", None)}
    industries = {
        str(getattr(event, "industry", "") or "")
        for event in scoped
        if getattr(event, "industry", None)
    }
    event_types = {
        str(getattr(event, "event_type", "") or "")
        for event in scoped
        if getattr(event, "event_type", None)
    }
    history_start_date = ""
    if start_date:
        try:
            history_start_date = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=180)).strftime("%Y-%m-%d")
        except Exception:
            history_start_date = ""

    candidates = []
    for event in all_events:
        event_date = str(getattr(event, "date", "") or "")
        if is_sample_event(event):
            continue
        if end_date and event_date >= end_date:
            continue
        if history_start_date and event_date < history_start_date:
            continue
        code = str(getattr(event, "code", "") or "")
        industry = str(getattr(event, "industry", "") or "")
        event_type = str(getattr(event, "event_type", "") or "")
        if codes and code not in codes and industry not in industries and event_type not in event_types:
            continue
        candidates.append(event)
    candidates.sort(
        key=lambda item: (
            str(getattr(item, "date", "") or ""),
            safe_float(getattr(item, "timestamp", 0), 0),
            safe_float(getattr(item, "impact_score", 0), 0),
        ),
        reverse=True,
    )

    outcomes = []
    for event in candidates[:history_limit]:
        code = str(getattr(event, "code", "") or "")
        event_date = str(getattr(event, "date", "") or "")
        realized = future_return(code, event_date, hold_days)
        if not realized:
            continue
        outcomes.append(
            {
                "exit_date": realized["exit_date"],
                "event": event,
                "return_pct": safe_float(realized.get("return_pct"), 0),
            }
        )
    outcomes.sort(key=lambda item: item["exit_date"])
    return outcomes


def empty_replay_result(
    *,
    start_date: Any,
    end_date: Any,
    initial_cash: float,
    mode: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if mode:
        payload["mode"] = mode
    payload.update(
        {
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
            "final_value": initial_cash,
            "return_pct": 0.0,
            "trades": [],
            "days": [],
            "equity_curve": [],
        }
    )
    return payload
