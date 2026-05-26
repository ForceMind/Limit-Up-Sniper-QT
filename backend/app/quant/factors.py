from __future__ import annotations

import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


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


def technical_profile_from_rows(
    rows: Sequence[Mapping[str, Any]],
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    scoped_rows = list(rows)
    if as_of:
        scoped_rows = [row for row in scoped_rows if str(row.get("date") or "") <= as_of]
    if len(scoped_rows) < 2:
        return {
            "score": 45.0,
            "risk": 55.0,
            "latest_close": 0.0,
            "latest_date": "",
            "ret_3d": 0.0,
            "ret_5d": 0.0,
            "ret_20d": 0.0,
            "volume_ratio": 1.0,
            "volatility": 0.0,
        }

    closes = [safe_float(row.get("close"), 0) for row in scoped_rows if safe_float(row.get("close"), 0) > 0]
    volumes = [safe_float(row.get("volume"), 0) for row in scoped_rows[-20:]]
    latest = scoped_rows[-1]
    if len(closes) < 2:
        return {
            "score": 45.0,
            "risk": 55.0,
            "latest_close": 0.0,
            "latest_date": "",
            "ret_3d": 0.0,
            "ret_5d": 0.0,
            "ret_20d": 0.0,
            "volume_ratio": 1.0,
            "volatility": 0.0,
        }

    def ret(days: int) -> float:
        if len(closes) <= days or closes[-days - 1] <= 0:
            return 0.0
        return closes[-1] / closes[-days - 1] - 1

    daily_returns = []
    for idx in range(max(1, len(closes) - 20), len(closes)):
        prev = closes[idx - 1]
        if prev > 0:
            daily_returns.append(closes[idx] / prev - 1)
    volatility = statistics.pstdev(daily_returns) if len(daily_returns) > 2 else 0.0
    avg_volume = statistics.mean(volumes[:-1]) if len(volumes) > 2 else (volumes[-1] if volumes else 1)
    volume_ratio = (volumes[-1] / avg_volume) if avg_volume > 0 and volumes else 1.0
    window_high = max(closes[-20:]) if len(closes) >= 20 else max(closes)
    drawdown = closes[-1] / window_high - 1 if window_high > 0 else 0.0

    ret_3d = ret(3)
    ret_5d = ret(5)
    ret_20d = ret(20)
    score = 50 + ret_3d * 450 + ret_5d * 260 + ret_20d * 80 + (volume_ratio - 1) * 8
    if drawdown < -0.08:
        score -= 10
    if volatility > 0.045:
        score -= (volatility - 0.045) * 220
    risk = 40 + volatility * 520 + max(0.0, -drawdown) * 120 + max(0.0, ret_5d - 0.12) * 120
    return {
        "score": round(clamp(score), 2),
        "risk": round(clamp(risk), 2),
        "latest_close": round(safe_float(latest.get("close"), 0), 3),
        "latest_date": latest.get("date"),
        "ret_3d": round(ret_3d * 100, 3),
        "ret_5d": round(ret_5d * 100, 3),
        "ret_20d": round(ret_20d * 100, 3),
        "volume_ratio": round(volume_ratio, 3),
        "volatility": round(volatility * 100, 3),
        "drawdown_from_20d_high_pct": round(drawdown * 100, 3),
        "near_20d_high": bool(closes[-1] >= window_high * 0.97),
    }


def lhb_factor_profile_from_rows(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    scoped_rows = list(rows)[:120]
    if not scoped_rows:
        return {"score": 50.0, "risk": 50.0, "net_buy_amount": 0.0, "hot_seat_count": 0, "sample_count": 0}

    dates = sorted(
        {str(row.get("trade_date") or "") for row in scoped_rows if row.get("trade_date")},
        reverse=True,
    )[:20]
    date_set = set(dates)
    recent = [row for row in scoped_rows if str(row.get("trade_date") or "") in date_set]
    buy_amount = sum(safe_float(row.get("buy_amount"), 0) for row in recent)
    sell_amount = sum(safe_float(row.get("sell_amount"), 0) for row in recent)
    net_amount = buy_amount - sell_amount
    gross_amount = max(1.0, buy_amount + sell_amount)
    hot_labels = {str(row.get("hot_money") or "").strip() for row in recent if str(row.get("hot_money") or "").strip()}
    active_seats = {
        str(row.get("buyer_seat_name") or "").strip()
        for row in recent
        if str(row.get("buyer_seat_name") or "").strip()
    }
    net_ratio = max(-1.0, min(1.0, net_amount / gross_amount))
    score = (
        50
        + net_ratio * 24
        + min(max(net_amount, 0.0) / 20_000_000, 16)
        + min(len(hot_labels), 4) * 4
        + min(len(date_set), 5) * 1.5
    )
    risk = 50 - net_ratio * 12 + max(0.0, -net_amount) / 20_000_000 * 8
    return {
        "score": round(clamp(score), 2),
        "risk": round(clamp(risk), 2),
        "net_buy_amount": round(net_amount, 2),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "net_buy_ratio": round(net_ratio, 4),
        "hot_seat_count": len(hot_labels),
        "active_seat_count": len(active_seats),
        "sample_count": len(recent),
        "date_count": len(date_set),
    }


def factor_profile_payload(
    params: Mapping[str, Any],
    technical: Mapping[str, Any],
    lhb: Mapping[str, Any],
) -> Dict[str, Any]:
    factor_weights = {
        "momentum": safe_float(params.get("factor_momentum_weight"), 0.35),
        "volume": safe_float(params.get("factor_volume_weight"), 0.20),
        "breakout": safe_float(params.get("factor_breakout_weight"), 0.20),
        "lhb": safe_float(params.get("factor_lhb_weight"), 0.25),
    }
    ret_3d = safe_float(technical.get("ret_3d"), 0)
    ret_5d = safe_float(technical.get("ret_5d"), 0)
    ret_20d = safe_float(technical.get("ret_20d"), 0)
    volume_ratio = safe_float(technical.get("volume_ratio"), 1)
    drawdown = safe_float(technical.get("drawdown_from_20d_high_pct"), 0)
    momentum_score = clamp(50 + ret_3d * 1.6 + ret_5d * 1.1 + ret_20d * 0.35)
    volume_score = clamp(50 + (volume_ratio - 1.0) * 18)
    breakout_score = clamp(58 + max(0.0, 3.0 + drawdown) * 4 if technical.get("near_20d_high") else 48 + drawdown * 0.8)
    lhb_score = safe_float(lhb.get("score"), 50)
    factor_score = (
        momentum_score * factor_weights["momentum"]
        + volume_score * factor_weights["volume"]
        + breakout_score * factor_weights["breakout"]
        + lhb_score * factor_weights["lhb"]
    )
    risk_adjustment = max(0.0, volume_ratio - 3.0) * 2.5 + max(0.0, safe_float(lhb.get("risk"), 50) - 55) * 0.2
    return {
        "score": round(clamp(factor_score), 2),
        "technical_adjustment": round((clamp(factor_score) - 50) * safe_float(params.get("factor_score_coef"), 0.28), 2),
        "risk_adjustment": round(risk_adjustment, 2),
        "weights": factor_weights,
        "momentum_score": round(momentum_score, 2),
        "volume_score": round(volume_score, 2),
        "breakout_score": round(breakout_score, 2),
        "lhb_score": round(lhb_score, 2),
        "lhb": dict(lhb),
    }
