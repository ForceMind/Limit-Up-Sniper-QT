from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, List, Optional

from app.quant.strategy_defaults import DEFAULT_BROKER_FEE_PARAMS


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


def _digits6(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) > 6:
        text = text[-6:]
    return text if len(text) == 6 else ""


def broker_fees(side: str, amount: float, fee_params: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    amount = max(0.0, _safe_float(amount, 0))
    if amount <= 0:
        return {"commission": 0.0, "stamp_duty": 0.0, "transfer_fee": 0.0, "total_fee": 0.0}
    params = fee_params if isinstance(fee_params, dict) else DEFAULT_BROKER_FEE_PARAMS
    commission = max(_safe_float(params.get("min_commission"), 5.0), amount * _safe_float(params.get("commission_rate"), 0.00025))
    stamp_duty = amount * _safe_float(params.get("stamp_duty_rate"), 0.0005) if str(side).upper() == "SELL" else 0.0
    transfer_fee = amount * _safe_float(params.get("transfer_fee_rate"), 0.00001)
    total_fee = commission + stamp_duty + transfer_fee
    return {
        "commission": round(commission, 2),
        "stamp_duty": round(stamp_duty, 2),
        "transfer_fee": round(transfer_fee, 2),
        "total_fee": round(total_fee, 2),
    }


def trade_clock(trade: Dict[str, Any]) -> str:
    raw_time = str(trade.get("time") or "").strip()
    if raw_time:
        if len(raw_time) >= 19:
            return raw_time[:19]
        if len(raw_time) >= 8 and "-" not in raw_time[:8]:
            return f"{trade.get('date', '')} {raw_time[:8]}".strip()
        return raw_time
    return f"{trade.get('date', '')} 15:00:00".strip()


def account_from_trades_payload(
    trades: List[Dict[str, Any]],
    *,
    initial_cash: Optional[float] = None,
    as_of: Optional[str] = None,
    start_date: Optional[str] = None,
    limit: int = 0,
    drop_unmatched_sells: bool = False,
    strategy_params: Optional[Dict[str, Any]] = None,
    latest_event_date: Callable[[], str],
    latest_price: Callable[..., Optional[Dict[str, Any]]],
    stock_name: Callable[[str], str],
    is_sample_trade: Callable[[Any], bool] = lambda _trade: False,
    fee_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    as_of = as_of or latest_event_date()
    params = strategy_params if isinstance(strategy_params, dict) else {}
    initial_asset = max(1.0, _safe_float(initial_cash, _safe_float(params.get("account_initial_cash"), 200000.0)))
    raw_trades = [trade for trade in trades if isinstance(trade, dict) and not is_sample_trade(trade)]
    visible_trades = [
        trade
        for trade in raw_trades
        if not as_of or str(trade.get("date", "")) <= as_of
    ]
    if start_date:
        visible_trades = [
            trade
            for trade in visible_trades
            if str(trade.get("date", "")) >= str(start_date)
        ]
    visible_trades = sorted(
        enumerate(visible_trades, start=1),
        key=lambda pair: (str(pair[1].get("date") or ""), trade_clock(pair[1]), pair[0]),
    )

    lots_by_code: Dict[str, List[Dict[str, Any]]] = {}
    deals: List[Dict[str, Any]] = []
    daily_settlement: Dict[str, Dict[str, float]] = {}
    total_fees = 0.0
    realized_pnl = 0.0
    adjusted_cash = initial_asset

    for index, trade in visible_trades:
        side = str(trade.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            continue
        code = _digits6(trade.get("code"))
        if not code:
            continue
        qty = _safe_float(trade.get("qty"), 0)
        price = _safe_float(trade.get("price"), 0)
        amount = qty * price
        if qty <= 0 or price <= 0 or amount <= 0:
            continue

        fees = broker_fees(side, amount, fee_params)
        if "total_fee" in trade:
            total_fee = max(0.0, _safe_float(trade.get("total_fee"), fees["total_fee"]))
            fees["total_fee"] = round(total_fee, 2)
            fees["commission"] = round(max(0.0, _safe_float(trade.get("commission"), fees["commission"])), 2)
            fees["stamp_duty"] = round(max(0.0, _safe_float(trade.get("stamp_duty"), fees["stamp_duty"])), 2)
            fees["transfer_fee"] = round(max(0.0, _safe_float(trade.get("transfer_fee"), fees["transfer_fee"])), 2)
        total_fees += fees["total_fee"]
        trade_date = str(trade.get("date") or "")
        trade_time = trade_clock(trade)
        name = str(trade.get("name") or stock_name(code))
        cash_flow = 0.0
        cost_amount = 0.0
        deal_realized = 0.0

        if side == "BUY":
            cash_flow = -(amount + fees["total_fee"])
            cost_amount = amount + fees["total_fee"]
            lots_by_code.setdefault(code, []).append(
                {
                    "qty": qty,
                    "price": price,
                    "cost_amount": cost_amount,
                    "entry_date": trade_date,
                    "name": name,
                    "reason": trade.get("reason", ""),
                    "buy_score": _safe_float(trade.get("score"), 0),
                }
            )
        else:
            queue = lots_by_code.setdefault(code, [])
            held_qty = sum(max(0.0, _safe_float(lot.get("qty"), 0)) for lot in queue)
            if drop_unmatched_sells and held_qty <= 0:
                total_fees -= fees["total_fee"]
                continue
            if drop_unmatched_sells and held_qty < qty:
                total_fees -= fees["total_fee"]
                qty = held_qty
                amount = qty * price
                fees = broker_fees(side, amount, fee_params)
                total_fees += fees["total_fee"]
            sell_qty_left = qty
            while sell_qty_left > 0 and queue:
                lot = queue[0]
                lot_qty = _safe_float(lot.get("qty"), 0)
                if lot_qty <= 0:
                    queue.pop(0)
                    continue
                matched = min(sell_qty_left, lot_qty)
                lot_cost = _safe_float(lot.get("cost_amount"), 0) * matched / lot_qty
                cost_amount += lot_cost
                lot["qty"] = lot_qty - matched
                lot["cost_amount"] = _safe_float(lot.get("cost_amount"), 0) - lot_cost
                sell_qty_left -= matched
                if lot["qty"] <= 0.000001:
                    queue.pop(0)
            if sell_qty_left > 0:
                pnl_pct = _safe_float(trade.get("pnl_pct"), 0)
                fallback_cost_price = price / (1 + pnl_pct / 100) if pnl_pct > -99.0 else price
                cost_amount += sell_qty_left * fallback_cost_price
            cash_flow = amount - fees["total_fee"]
            deal_realized = cash_flow - cost_amount
            realized_pnl += deal_realized
        adjusted_cash += cash_flow

        deal = {
            "deal_id": f"BT-{trade_date.replace('-', '')}-{index:05d}",
            "date": trade_date,
            "time": trade_time,
            "side": side,
            "direction": "\u4e70\u5165" if side == "BUY" else "\u5356\u51fa",
            "code": code,
            "name": name,
            "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
            "price": round(price, 3),
            "amount": round(amount, 2),
            "commission": fees["commission"],
            "stamp_duty": fees["stamp_duty"],
            "transfer_fee": fees["transfer_fee"],
            "total_fee": fees["total_fee"],
            "net_amount": round(cash_flow, 2),
            "cost_amount": round(cost_amount, 2),
            "realized_pnl": round(deal_realized, 2),
            "score": round(_safe_float(trade.get("score"), 0), 2) if trade.get("score") is not None else None,
            "pnl_pct": round(_safe_float(trade.get("pnl_pct"), 0), 3) if trade.get("pnl_pct") is not None else None,
            "reason": trade.get("reason", ""),
        }
        deals.append(deal)

        bucket = daily_settlement.setdefault(
            trade_date,
            {
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "commission": 0.0,
                "stamp_duty": 0.0,
                "transfer_fee": 0.0,
                "total_fee": 0.0,
                "net_amount": 0.0,
                "realized_pnl": 0.0,
                "deal_count": 0.0,
            },
        )
        if side == "BUY":
            bucket["buy_amount"] += amount
        else:
            bucket["sell_amount"] += amount
        bucket["commission"] += fees["commission"]
        bucket["stamp_duty"] += fees["stamp_duty"]
        bucket["transfer_fee"] += fees["transfer_fee"]
        bucket["total_fee"] += fees["total_fee"]
        bucket["net_amount"] += cash_flow
        bucket["realized_pnl"] += deal_realized
        bucket["deal_count"] += 1

    positions = []
    position_cost = 0.0
    market_value = 0.0
    for code, lots in lots_by_code.items():
        active_lots = [lot for lot in lots if _safe_float(lot.get("qty"), 0) > 0]
        if not active_lots:
            continue
        qty = sum(_safe_float(lot.get("qty"), 0) for lot in active_lots)
        cost_amount = sum(_safe_float(lot.get("cost_amount"), 0) for lot in active_lots)
        first_lot = active_lots[0]
        price_row = latest_price(code, as_of=as_of)
        last_price = _safe_float((price_row or {}).get("close"), _safe_float(first_lot.get("price"), 0))
        cost_price = cost_amount / qty if qty > 0 else last_price
        value = qty * last_price
        pnl_amount = value - cost_amount
        position_cost += cost_amount
        market_value += value
        positions.append(
            {
                "code": code,
                "name": first_lot.get("name") or stock_name(code),
                "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                "available_qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                "entry_price": round(_safe_float(first_lot.get("price"), cost_price), 3),
                "cost_price": round(cost_price, 3),
                "cost_amount": round(cost_amount, 2),
                "last_price": round(last_price, 3),
                "last_date": (price_row or {}).get("date", as_of),
                "market_value": round(value, 2),
                "pnl_amount": round(pnl_amount, 2),
                "pnl_pct": round(pnl_amount / cost_amount * 100, 3) if cost_amount > 0 else 0.0,
                "buy_score": _safe_float(first_lot.get("buy_score"), 0),
                "reason": first_lot.get("reason", ""),
            }
        )

    settlement_rows = [
        {
            "date": date,
            "buy_amount": round(item["buy_amount"], 2),
            "sell_amount": round(item["sell_amount"], 2),
            "commission": round(item["commission"], 2),
            "stamp_duty": round(item["stamp_duty"], 2),
            "transfer_fee": round(item["transfer_fee"], 2),
            "total_fee": round(item["total_fee"], 2),
            "net_amount": round(item["net_amount"], 2),
            "realized_pnl": round(item["realized_pnl"], 2),
            "deal_count": int(item["deal_count"]),
        }
        for date, item in daily_settlement.items()
    ]
    deals.sort(key=lambda item: (item.get("time", ""), item.get("deal_id", "")), reverse=True)
    settlement_rows.sort(key=lambda item: item["date"], reverse=True)
    today_deals = [deal for deal in deals if deal.get("date") == as_of]
    total_asset = adjusted_cash + market_value
    total_pnl = total_asset - initial_asset
    if limit and limit > 0:
        visible_deals = deals[:limit]
        visible_settlements = settlement_rows[:limit]
    else:
        visible_deals = deals
        visible_settlements = settlement_rows
    return {
        "status": "ok",
        "as_of": as_of,
        "start_date": str(start_date or ""),
        "account": {
            "initial_cash": round(initial_asset, 2),
            "total_asset": round(total_asset, 2),
            "cash": round(adjusted_cash, 2),
            "available_cash": round(max(0.0, adjusted_cash), 2),
            "market_value": round(market_value, 2),
            "position_cost": round(position_cost, 2),
            "unrealized_pnl": round(market_value - position_cost, 2),
            "realized_pnl": round(realized_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "return_pct": round(total_pnl / initial_asset * 100, 3) if initial_asset > 0 else 0.0,
            "position_count": len(positions),
            "deal_count": len(deals),
            "total_fees": round(total_fees, 2),
        },
        "positions": positions,
        "today_deals": today_deals if not limit else today_deals[:limit],
        "history_deals": visible_deals,
        "delivery_records": visible_deals,
        "daily_settlements": visible_settlements,
    }
