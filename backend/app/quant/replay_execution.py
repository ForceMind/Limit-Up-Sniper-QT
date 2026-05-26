from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Tuple


FeePayload = Callable[[float], Dict[str, Any]]


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


def lot_quantity_for_cash(
    *,
    cash: float,
    price: float,
    slots_left: int,
    initial_cash: float,
    max_positions: int,
    fee_payload: Optional[FeePayload] = None,
) -> int:
    clean_cash = safe_float(cash, 0)
    clean_price = safe_float(price, 0)
    if clean_cash <= 0 or clean_price <= 0:
        return 0
    slots = max(1, int(slots_left or 1))
    max_pos = max(1, int(max_positions or 1))
    allocation = min(clean_cash / slots, safe_float(initial_cash, clean_cash) / max_pos)
    qty = math.floor(allocation / clean_price / 100) * 100
    while qty > 0:
        gross_amount = qty * clean_price
        fees = fee_payload(gross_amount) if fee_payload else {}
        total_fee = safe_float(fees.get("total_fee") if isinstance(fees, dict) else 0, 0)
        if gross_amount + total_fee <= clean_cash:
            break
        qty -= 100
    return int(max(0, qty))


def replay_missed_order(date: str, order: Dict[str, Any], unfilled_reason: str) -> Dict[str, Any]:
    return {
        "date": date,
        "side": "MISS",
        "signal_date": order.get("signal_date", ""),
        "execute_on": date,
        "code": order.get("code", ""),
        "name": order.get("name", ""),
        "score": order.get("buy_score", 0),
        "status": "未成交",
        "unfilled_reason": unfilled_reason,
        "reason": order.get("reason", ""),
    }


def daily_buy_execution(
    *,
    order: Dict[str, Any],
    date: str,
    price: float,
    qty: int,
    fees: Dict[str, Any],
) -> Dict[str, Any]:
    clean_price = safe_float(price, 0)
    clean_qty = int(qty or 0)
    gross_amount = clean_qty * clean_price
    total_fee = safe_float(fees.get("total_fee"), 0)
    return {
        "cash_delta": -(gross_amount + total_fee),
        "position": {
            "code": order["code"],
            "name": order["name"],
            "qty": clean_qty,
            "entry_date": date,
            "signal_date": order.get("signal_date", ""),
            "entry_price": round(clean_price, 3),
            "entry_cost": round(gross_amount + total_fee, 2),
            "buy_score": order.get("buy_score", 0),
            "reason": order.get("reason", ""),
            "hold_days": 0,
        },
        "trade": {
            "date": date,
            "side": "BUY",
            "code": order["code"],
            "name": order["name"],
            "qty": clean_qty,
            "price": round(clean_price, 3),
            "amount": round(gross_amount, 2),
            "commission": fees["commission"],
            "stamp_duty": fees["stamp_duty"],
            "transfer_fee": fees["transfer_fee"],
            "total_fee": fees["total_fee"],
            "score": order.get("buy_score", 0),
            "signal_date": order.get("signal_date", ""),
            "reason": order.get("reason", ""),
        },
    }


def daily_sell_execution(
    *,
    position: Dict[str, Any],
    date: str,
    price: float,
    fees: Dict[str, Any],
    stop_loss_pct: float,
    take_profit_pct: float,
    sell_score: float = 0.0,
    sell_score_threshold: float = 0.0,
) -> Dict[str, Any]:
    clean_price = safe_float(price, 0)
    qty = safe_float(position.get("qty"), 0)
    gross_amount = qty * clean_price
    total_fee = safe_float(fees.get("total_fee"), 0)
    net_amount = gross_amount - total_fee
    entry_price = safe_float(position.get("entry_price"), clean_price)
    entry_cost = safe_float(position.get("entry_cost"), qty * entry_price)
    pnl_pct = (net_amount / entry_cost - 1) * 100 if entry_cost > 0 else (
        (clean_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
    )
    reason = "持仓到期"
    if pnl_pct <= safe_float(stop_loss_pct, -5.0):
        reason = "止损"
    elif pnl_pct >= safe_float(take_profit_pct, 8.0):
        reason = "止盈"
    elif safe_float(sell_score, 0) >= safe_float(sell_score_threshold, 0):
        reason = "卖出评分触发"
    return {
        "cash_delta": net_amount,
        "pnl_pct": pnl_pct,
        "trade": {
            "date": date,
            "side": "SELL",
            "code": position["code"],
            "name": position["name"],
            "qty": position["qty"],
            "price": round(clean_price, 3),
            "amount": round(gross_amount, 2),
            "commission": fees["commission"],
            "stamp_duty": fees["stamp_duty"],
            "transfer_fee": fees["transfer_fee"],
            "total_fee": fees["total_fee"],
            "net_amount": round(net_amount, 2),
            "pnl_pct": round(pnl_pct, 3),
            "reason": reason,
        },
    }


def intraday_buy_execution(
    *,
    order: Dict[str, Any],
    date: str,
    time: str,
    price: float,
    qty: int,
    mode: str,
) -> Dict[str, Any]:
    clean_price = safe_float(price, 0)
    clean_qty = int(qty or 0)
    return {
        "cash_delta": -(clean_qty * clean_price),
        "position": {
            "code": order["code"],
            "name": order["name"],
            "qty": clean_qty,
            "entry_date": date,
            "entry_time": time,
            "signal_date": order.get("signal_date", ""),
            "signal_time": order.get("signal_time", ""),
            "entry_price": round(clean_price, 3),
            "buy_score": order.get("buy_score", 0),
            "reason": order.get("reason", ""),
            "hold_days": 0,
            "entry_mode": mode,
        },
        "trade": {
            "date": date,
            "time": time,
            "side": "BUY",
            "code": order["code"],
            "name": order["name"],
            "qty": clean_qty,
            "price": round(clean_price, 3),
            "score": order.get("buy_score", 0),
            "signal_date": order.get("signal_date", ""),
            "signal_time": order.get("signal_time", ""),
            "reason": order.get("reason", ""),
            "mode": mode,
        },
    }


def intraday_sell_execution(
    *,
    position: Dict[str, Any],
    date: str,
    time: str,
    price: float,
    reason: str,
    mode: str,
) -> Dict[str, Any]:
    clean_price = safe_float(price, 0)
    qty = safe_float(position.get("qty"), 0)
    entry_price = safe_float(position.get("entry_price"), clean_price)
    pnl_pct = (clean_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
    return {
        "cash_delta": qty * clean_price,
        "pnl_pct": pnl_pct,
        "trade": {
            "date": date,
            "time": time,
            "side": "SELL",
            "code": position["code"],
            "name": position["name"],
            "qty": position["qty"],
            "price": round(clean_price, 3),
            "pnl_pct": round(pnl_pct, 3),
            "reason": reason,
            "mode": mode,
        },
    }


def replay_position_snapshot(
    position: Dict[str, Any],
    close_price: float,
    *,
    include_entry_time: bool = False,
    include_entry_mode: bool = False,
    require_close_for_pnl: bool = False,
) -> Tuple[Dict[str, Any], float]:
    clean_close = safe_float(close_price, 0)
    entry_price = safe_float(position.get("entry_price"), clean_close)
    qty = safe_float(position.get("qty"), 0)
    if entry_price > 0 and (clean_close > 0 or not require_close_for_pnl):
        pnl_pct = (clean_close / entry_price - 1) * 100
    else:
        pnl_pct = 0.0
    market_value = qty * clean_close
    payload = {
        "code": position.get("code"),
        "name": position.get("name"),
        "qty": qty,
        "entry_date": position.get("entry_date", ""),
        "entry_price": round(entry_price, 3),
        "last_price": round(clean_close, 3),
        "market_value": round(market_value, 2),
        "pnl_pct": round(pnl_pct, 3),
    }
    if include_entry_time:
        payload["entry_time"] = position.get("entry_time", "")
    if include_entry_mode:
        payload["entry_mode"] = position.get("entry_mode", "")
    return payload, market_value


def replay_day_valuation(
    *,
    date: str,
    cash: float,
    market_value: float,
    prev_total: float,
    initial_cash: float,
    position_count: int,
) -> Dict[str, Any]:
    total_value = safe_float(cash, 0) + safe_float(market_value, 0)
    daily_return = (total_value / prev_total - 1) * 100 if safe_float(prev_total, 0) > 0 else 0.0
    initial = safe_float(initial_cash, 0)
    return_pct = (total_value / initial - 1) * 100 if initial > 0 else 0.0
    return {
        "total_value": round(total_value, 2),
        "daily_return_pct": round(daily_return, 3),
        "equity_point": {
            "date": date,
            "total_value": round(total_value, 2),
            "return_pct": round(return_pct, 3),
            "position_count": int(position_count),
        },
    }
