from __future__ import annotations

import random
from typing import Any, Callable, Dict, List, Optional


NormalizeParams = Callable[[Dict[str, Any]], Dict[str, float]]
DigestPayload = Callable[..., str]


GENES: Dict[str, tuple[float, float]] = {
    "buy_threshold": (55, 90),
    "watch_threshold": (45, 80),
    "avoid_sell_threshold": (55, 92),
    "avoid_buy_ceiling": (45, 85),
    "sell_score_threshold": (55, 92),
    "stop_loss_pct": (-12, -2),
    "take_profit_pct": (3, 20),
    "max_hold_days": (1, 10),
    "max_positions": (1, 10),
    "top_n": (3, 20),
    "sentiment_weight": (0.10, 0.55),
    "event_weight": (0.10, 0.55),
    "technical_weight": (0.10, 0.55),
    "risk_weight": (0.05, 0.40),
    "sentiment_coef": (20, 90),
    "ai_score_coef": (1, 10),
    "event_impact_weight": (0.35, 0.85),
    "history_score_weight": (0.15, 0.65),
    "history_return_coef": (150, 700),
    "history_win_coef": (10, 100),
    "sell_negative_sentiment_coef": (5, 55),
    "sell_technical_risk_coef": (0.15, 1.25),
    "negative_sentiment_risk_penalty": (5, 35),
    "risk_event_penalty": (8, 45),
    "factor_score_coef": (0.08, 0.65),
    "factor_momentum_weight": (0.05, 0.60),
    "factor_volume_weight": (0.05, 0.45),
    "factor_breakout_weight": (0.05, 0.45),
    "factor_lhb_weight": (0.05, 0.55),
}


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


def candidate_elimination_reason(item: Dict[str, Any], selected: bool) -> str:
    if selected:
        return "保留进入下一代精英池"
    closed_trades = _safe_float(item.get("closed_trades"), 0)
    return_pct = _safe_float(item.get("return_pct"), 0)
    max_drawdown_pct = _safe_float(item.get("max_drawdown_pct"), 0)
    profit_factor = _safe_float(item.get("profit_factor"), 0)
    sharpe_ratio = _safe_float(item.get("sharpe_ratio"), 0)
    if closed_trades < 5:
        return "闭环交易不足，样本不够稳定"
    if return_pct < 0:
        return "收益为负"
    if max_drawdown_pct <= -20:
        return "最大回撤过大"
    if profit_factor and profit_factor < 1:
        return "盈亏比不足"
    if sharpe_ratio < 0:
        return "夏普为负，波动收益质量差"
    return "综合目标函数排名低于精英线"


def candidate_records_for_generation(
    *,
    run_id: str,
    generation: int,
    evaluated: List[Dict[str, Any]],
    elite_count: int,
    normalize_params: NormalizeParams,
    digest: DigestPayload,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for rank, item in enumerate(evaluated, start=1):
        params = normalize_params(item.get("params", {}))
        params_hash = digest("strategy_candidate_params", params)[:16]
        selected = rank <= elite_count
        records.append(
            {
                "candidate_id": digest("strategy_candidate", run_id, generation, rank, params_hash)[:32],
                "run_id": run_id,
                "generation": generation,
                "rank": rank,
                "selected": selected,
                "selection_role": "elite_survivor" if selected else "eliminated",
                "elimination_reason": candidate_elimination_reason(item, selected),
                "objective": _safe_float(item.get("objective"), 0),
                "return_pct": _safe_float(item.get("return_pct"), 0),
                "max_drawdown_pct": _safe_float(item.get("max_drawdown_pct"), 0),
                "sharpe_ratio": _safe_float(item.get("sharpe_ratio"), 0),
                "profit_factor": _safe_float(item.get("profit_factor"), 0),
                "win_rate": _safe_float(item.get("win_rate"), 0),
                "closed_trades": int(_safe_float(item.get("closed_trades"), 0)),
                "params_hash": params_hash,
                "params": params,
            }
        )
    return records


def mutate_strategy_params(
    params: Dict[str, Any],
    *,
    scale: float,
    normalize_params: NormalizeParams,
) -> Dict[str, float]:
    mutated = dict(params)
    for key, bounds in GENES.items():
        low, high = bounds
        current = _safe_float(mutated.get(key), (low + high) / 2)
        if random.random() < 0.72:
            current += random.gauss(0, (high - low) * scale)
        mutated[key] = max(low, min(high, current))
    return normalize_params(mutated)


def initial_population(
    base: Dict[str, Any],
    *,
    population_size: int,
    normalize_params: NormalizeParams,
) -> List[Dict[str, float]]:
    population = [normalize_params(base)]
    while len(population) < population_size:
        population.append(mutate_strategy_params(base, scale=0.35, normalize_params=normalize_params))
    return population


def next_generation_population(
    evaluated: List[Dict[str, Any]],
    *,
    population_size: int,
    normalize_params: NormalizeParams,
) -> List[Dict[str, float]]:
    elite_count = max(2, population_size // 5)
    elites = [item["params"] for item in evaluated[:elite_count]]
    population = [dict(item) for item in elites]
    while len(population) < population_size:
        parent_a = random.choice(elites)
        parent_b = random.choice(evaluated[: max(elite_count + 2, population_size // 2)])["params"]
        child = {}
        for key in GENES:
            child[key] = parent_a[key] if random.random() < 0.5 else parent_b[key]
        population.append(mutate_strategy_params(child, scale=0.18, normalize_params=normalize_params))
    return population[:population_size]


def build_evolution_models(
    evaluated: List[Dict[str, Any]],
    *,
    finished_at: str,
    normalize_params: NormalizeParams,
    max_models: int = 16,
) -> List[Dict[str, Any]]:
    stamp = "".join(ch for ch in finished_at if ch.isdigit())[:14]
    models = []
    for rank, item in enumerate(evaluated[:max_models], start=1):
        params = normalize_params(item.get("params", {}))
        models.append(
            {
                "id": f"evo-{stamp}-{rank:02d}",
                "name": f"进化策略 #{rank}",
                "source": "genetic_evolution",
                "reusable": True,
                "generated_at": finished_at,
                "rank": rank,
                "objective": item.get("objective", 0),
                "return_pct": item.get("return_pct", 0),
                "max_drawdown_pct": item.get("max_drawdown_pct", 0),
                "sharpe_ratio": item.get("sharpe_ratio", 0),
                "profit_factor": item.get("profit_factor", 0),
                "win_rate": item.get("win_rate", 0),
                "closed_trades": item.get("closed_trades", 0),
                "backtest": item.get("backtest", {}),
                "trade_records": item.get("trade_records", []),
                "delivery_records": item.get("delivery_records", []),
                "daily_settlements": item.get("daily_settlements", []),
                "params": params,
            }
        )
    return models


def evolution_evaluation_payload(
    *,
    result: Dict[str, Any],
    params: Dict[str, Any],
    account: Dict[str, Any],
    start_date: Optional[str],
    end_date: Optional[str],
) -> Dict[str, Any]:
    return_pct = _safe_float(result.get("return_pct"), 0)
    max_drawdown_pct = _safe_float(result.get("max_drawdown_pct"), 0)
    win_rate = _safe_float(result.get("win_rate"), 0)
    closed_trades = _safe_float(result.get("closed_trades"), 0)
    performance = result.get("performance") if isinstance(result.get("performance"), dict) else {}
    sharpe_ratio = _safe_float(performance.get("sharpe_ratio"), 0)
    profit_factor = _safe_float(performance.get("profit_factor"), 0)
    trade_records = result.get("trades") if isinstance(result.get("trades"), list) else []
    trade_penalty = 10.0 if closed_trades < 5 else 0.0
    objective = (
        return_pct
        - abs(max_drawdown_pct) * 0.85
        + sharpe_ratio * 3.2
        + min(max(profit_factor, 0), 4) * 1.2
        + win_rate * 0.03
        + min(closed_trades, 60) * 0.02
        - trade_penalty
    )
    return {
        "objective": round(objective, 4),
        "return_pct": round(return_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "sharpe_ratio": round(sharpe_ratio, 4),
        "profit_factor": round(profit_factor, 4),
        "win_rate": round(win_rate, 4),
        "closed_trades": int(closed_trades),
        "backtest": {
            "mode": result.get("mode", "daily"),
            "start_date": result.get("start_date") or start_date,
            "end_date": result.get("end_date") or end_date,
            "initial_cash": result.get("initial_cash", params.get("account_initial_cash")),
            "final_value": result.get("final_value", params.get("account_initial_cash")),
            "return_pct": round(return_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "sharpe_ratio": round(sharpe_ratio, 4),
            "profit_factor": round(profit_factor, 4),
            "win_rate": round(win_rate, 4),
            "closed_trades": int(closed_trades),
            "trade_count": len(trade_records),
            "total_fees": performance.get("total_fees", 0),
        },
        "trade_records": trade_records,
        "delivery_records": account.get("delivery_records", []),
        "daily_settlements": account.get("daily_settlements", []),
        "params": params,
    }
