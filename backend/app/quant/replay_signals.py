from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional


BUY_ACTION = "买入候选"
WATCH_ACTION = "重点观察"
NO_BUY_ACTION = "暂不买入"
AVOID_ACTION = "回避/卖出"

ScoreBundle = Callable[[Dict[str, Any], Dict[str, Any], str], Dict[str, Any]]
StockName = Callable[[str, str], str]
Tradeable = Callable[[str], bool]
SignalDt = Callable[[Any], Any]


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


def replay_signal_action(*, buy_score: float, sell_score: float, params: Dict[str, Any]) -> str:
    buy = _safe_float(buy_score, 0)
    sell = _safe_float(sell_score, 0)
    if buy >= _safe_float(params.get("buy_threshold"), 0):
        action = BUY_ACTION
    elif buy >= _safe_float(params.get("watch_threshold"), 0):
        action = WATCH_ACTION
    else:
        action = NO_BUY_ACTION
    if sell >= _safe_float(params.get("avoid_sell_threshold"), 0) and buy < _safe_float(params.get("avoid_buy_ceiling"), 0):
        action = AVOID_ACTION
    return action


def build_replay_candidate_scores(
    events: Iterable[Any],
    *,
    corr: Dict[str, Any],
    current_date: str,
    params: Dict[str, Any],
    score_bundle: ScoreBundle,
    stock_name: StockName,
    is_tradeable: Tradeable,
    signal_dt: Optional[SignalDt] = None,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for event in events:
        grouped.setdefault(event.code, {"events": []})["events"].append(event)

    candidates = []
    for code, bundle in grouped.items():
        if not is_tradeable(code):
            continue
        scores = score_bundle(bundle, corr, current_date)
        events_sorted = sorted(bundle["events"], key=lambda item: _safe_float(getattr(item, "impact_score", 0), 0), reverse=True)
        if not events_sorted:
            continue
        primary = events_sorted[0]
        buy_score = _safe_float(scores.get("buy_score"), 0)
        sell_score = _safe_float(scores.get("sell_score"), 0)
        item = {
            "code": code,
            "name": stock_name(code, getattr(primary, "name", "")),
            "action": replay_signal_action(buy_score=buy_score, sell_score=sell_score, params=params),
            "buy_score": round(buy_score, 2),
            "sell_score": round(sell_score, 2),
            "reason": getattr(primary, "reason", ""),
            "latest_event_date": current_date,
        }
        if signal_dt is not None:
            value = signal_dt(primary)
            item["signal_time"] = value.strftime("%Y-%m-%d %H:%M:%S") if value else ""
            item["signal_dt"] = value
        candidates.append(item)
    candidates.sort(key=lambda item: item["buy_score"], reverse=True)
    return candidates


def build_daily_signal_orders(
    candidates: List[Dict[str, Any]],
    *,
    current_date: str,
    next_date: str,
    positions: List[Dict[str, Any]],
    pending_buys: List[Dict[str, Any]],
    top_n: int,
) -> Dict[str, List[Dict[str, Any]]]:
    if not next_date:
        return {"orders": [], "signals": []}
    held_or_pending = {pos["code"] for pos in positions} | {order["code"] for order in pending_buys}
    orders = []
    signals = []
    for item in candidates[: max(1, int(top_n or 1))]:
        if item.get("latest_event_date") != current_date:
            continue
        if item.get("action") != BUY_ACTION:
            continue
        if item["code"] in held_or_pending:
            continue
        order = {
            "signal_date": current_date,
            "execute_on": next_date,
            "code": item["code"],
            "name": item["name"],
            "buy_score": item["buy_score"],
            "sell_score": item["sell_score"],
            "reason": str(item.get("reason") or "")[:180],
        }
        orders.append(order)
        signals.append(order)
        held_or_pending.add(item["code"])
    return {"orders": orders, "signals": signals}


def build_intraday_signal_order(
    item: Dict[str, Any],
    *,
    current_date: str,
    next_date: str,
    execute_today: bool,
) -> Dict[str, Any]:
    return {
        "signal_date": current_date,
        "signal_time": item.get("signal_time", ""),
        "execute_on": current_date if execute_today else next_date,
        "code": item["code"],
        "name": item["name"],
        "buy_score": item["buy_score"],
        "sell_score": item["sell_score"],
        "reason": str(item.get("reason") or "")[:180],
        "signal_dt": None,
    }
