from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List


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


def aggregate_return_stats(returns: List[float]) -> Dict[str, Any]:
    if not returns:
        return {"samples": 0, "avg_return_pct": 0.0, "win_rate": 0.0, "confidence": 0.0}
    wins = [ret for ret in returns if ret > 0]
    return {
        "samples": len(returns),
        "avg_return_pct": round(statistics.mean(returns), 3),
        "median_return_pct": round(statistics.median(returns), 3),
        "win_rate": round(len(wins) / len(returns) * 100, 2),
        "confidence": round(min(1.0, math.log(len(returns) + 1, 20)), 3),
    }


def performance_metrics_payload(
    equity_curve: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    initial_cash: float,
    final_value: float,
) -> Dict[str, Any]:
    initial_cash = max(1.0, _safe_float(initial_cash, 1.0))
    final_value = max(0.0, _safe_float(final_value, initial_cash))
    values = [_safe_float(point.get("total_value"), 0) for point in equity_curve if _safe_float(point.get("total_value"), 0) > 0]
    daily_returns = []
    previous = initial_cash
    for value in values:
        daily_returns.append((value / previous - 1.0) if previous > 0 else 0.0)
        previous = value

    total_return_pct = (final_value / initial_cash - 1.0) * 100
    trading_days = len(values)
    annualized_return_pct = ((final_value / initial_cash) ** (252 / trading_days) - 1.0) * 100 if trading_days > 0 and final_value > 0 else 0.0
    volatility_pct = statistics.stdev(daily_returns) * math.sqrt(252) * 100 if len(daily_returns) >= 2 else 0.0
    sharpe_ratio = (
        statistics.mean(daily_returns) / statistics.stdev(daily_returns) * math.sqrt(252)
        if len(daily_returns) >= 2 and statistics.stdev(daily_returns) > 0
        else 0.0
    )

    peak = initial_cash
    max_drawdown = 0.0
    drawdown_start = ""
    drawdown_end = ""
    current_peak_date = ""
    for point in equity_curve:
        date = str(point.get("date") or "")
        value = _safe_float(point.get("total_value"), initial_cash)
        if value >= peak:
            peak = value
            current_peak_date = date
        drawdown = value / peak - 1.0 if peak > 0 else 0.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            drawdown_start = current_peak_date
            drawdown_end = date

    sell_trades = [trade for trade in trades if str(trade.get("side") or "").upper() == "SELL"]
    buy_trades = [trade for trade in trades if str(trade.get("side") or "").upper() == "BUY"]
    sell_returns = [_safe_float(trade.get("pnl_pct"), 0) for trade in sell_trades]
    wins = [ret for ret in sell_returns if ret > 0]
    losses = [ret for ret in sell_returns if ret <= 0]
    gross_profit = sum(max(0.0, _safe_float(trade.get("net_amount"), 0) - _safe_float(trade.get("amount"), 0)) for trade in sell_trades)
    if gross_profit <= 0:
        gross_profit = sum(ret for ret in wins)
    gross_loss = abs(sum(min(0.0, ret) for ret in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    max_consecutive_losses = 0
    current_losses = 0
    for ret in sell_returns:
        if ret <= 0:
            current_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
        else:
            current_losses = 0

    total_fees = sum(_safe_float(trade.get("total_fee"), 0) for trade in trades)
    turnover_amount = sum(_safe_float(trade.get("amount"), 0) for trade in trades)
    exposure_days = sum(1 for point in equity_curve if _safe_float(point.get("position_count"), 0) > 0)
    avg_position_count = statistics.mean(_safe_float(point.get("position_count"), 0) for point in equity_curve) if equity_curve else 0.0

    return {
        "trading_days": trading_days,
        "total_return_pct": round(total_return_pct, 3),
        "annualized_return_pct": round(annualized_return_pct, 3),
        "volatility_pct": round(volatility_pct, 3),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "max_drawdown_pct": round(max_drawdown * 100, 3),
        "max_drawdown_start": drawdown_start,
        "max_drawdown_end": drawdown_end,
        "exposure_pct": round(exposure_days / trading_days * 100, 2) if trading_days else 0.0,
        "avg_position_count": round(avg_position_count, 3),
        "buy_trades": len(buy_trades),
        "sell_trades": len(sell_trades),
        "closed_trades": len(sell_trades),
        "win_rate": round(len(wins) / len(sell_returns) * 100, 2) if sell_returns else 0.0,
        "avg_trade_return_pct": round(statistics.mean(sell_returns), 3) if sell_returns else 0.0,
        "median_trade_return_pct": round(statistics.median(sell_returns), 3) if sell_returns else 0.0,
        "avg_win_pct": round(statistics.mean(wins), 3) if wins else 0.0,
        "avg_loss_pct": round(statistics.mean(losses), 3) if losses else 0.0,
        "best_trade_pct": round(max(sell_returns), 3) if sell_returns else 0.0,
        "worst_trade_pct": round(min(sell_returns), 3) if sell_returns else 0.0,
        "profit_factor": round(profit_factor, 4),
        "expectancy_pct": round(statistics.mean(sell_returns), 3) if sell_returns else 0.0,
        "max_consecutive_losses": max_consecutive_losses,
        "total_fees": round(total_fees, 2),
        "turnover_amount": round(turnover_amount, 2),
        "turnover_ratio": round(turnover_amount / initial_cash, 4),
    }
