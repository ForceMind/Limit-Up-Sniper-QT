from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from app.quant.engine_utils import clamp, contains_sample_marker, read_json, safe_float, write_json
from app.quant.quant_paths import STATE_FILE
from app.quant.strategy_defaults import DEFAULT_STRATEGY_PARAMS


Now = Callable[[], datetime]


def normalize_strategy_params(raw: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    raw = raw if isinstance(raw, dict) else {}
    params = {
        key: safe_float(raw.get(key), default)
        for key, default in DEFAULT_STRATEGY_PARAMS.items()
    }
    for key in ("sentiment_weight", "event_weight", "technical_weight", "risk_weight"):
        params[key] = max(0.0, params[key])
    weight_total = sum(params[key] for key in ("sentiment_weight", "event_weight", "technical_weight", "risk_weight")) or 1.0
    for key in ("sentiment_weight", "event_weight", "technical_weight", "risk_weight"):
        params[key] = round(params[key] / weight_total, 4)
    params["buy_threshold"] = clamp(params["buy_threshold"], 40, 95)
    params["watch_threshold"] = clamp(params["watch_threshold"], 30, params["buy_threshold"])
    params["avoid_sell_threshold"] = clamp(params["avoid_sell_threshold"], 40, 95)
    params["avoid_buy_ceiling"] = clamp(params["avoid_buy_ceiling"], 30, 90)
    params["sell_score_threshold"] = clamp(params["sell_score_threshold"], 40, 98)
    params["stop_loss_pct"] = max(-30.0, min(-1.0, params["stop_loss_pct"]))
    params["take_profit_pct"] = clamp(params["take_profit_pct"], 1, 40)
    params["max_hold_days"] = max(1.0, min(30.0, params["max_hold_days"]))
    params["paper_max_hold_days"] = max(1.0, min(60.0, params["paper_max_hold_days"]))
    params["max_positions"] = max(1.0, min(20.0, params["max_positions"]))
    params["top_n"] = max(1.0, min(50.0, params["top_n"]))
    params["account_initial_cash"] = max(10000.0, min(10_000_000.0, params["account_initial_cash"]))
    params["paper_position_value"] = max(1000.0, min(2_000_000.0, params["paper_position_value"]))
    params["sentiment_coef"] = clamp(params["sentiment_coef"], 0, 80)
    params["ai_score_coef"] = clamp(params["ai_score_coef"], 0, 20)
    params["event_impact_weight"] = clamp(params["event_impact_weight"], 0, 1)
    params["history_score_weight"] = clamp(params["history_score_weight"], 0, 1)
    params["history_return_coef"] = clamp(params["history_return_coef"], 0, 1000)
    params["history_win_coef"] = clamp(params["history_win_coef"], 0, 120)
    params["sell_negative_sentiment_coef"] = clamp(params["sell_negative_sentiment_coef"], 0, 80)
    params["sell_technical_risk_coef"] = clamp(params["sell_technical_risk_coef"], 0, 2)
    combo = params["event_impact_weight"] + params["history_score_weight"]
    if combo <= 0:
        params["event_impact_weight"] = DEFAULT_STRATEGY_PARAMS["event_impact_weight"]
        params["history_score_weight"] = DEFAULT_STRATEGY_PARAMS["history_score_weight"]
    else:
        params["event_impact_weight"] = round(params["event_impact_weight"] / combo, 4)
        params["history_score_weight"] = round(params["history_score_weight"] / combo, 4)
    params["negative_sentiment_risk_penalty"] = clamp(params["negative_sentiment_risk_penalty"], 0, 60)
    params["risk_event_penalty"] = clamp(params["risk_event_penalty"], 0, 80)
    params["factor_score_coef"] = clamp(params["factor_score_coef"], 0, 1)
    factor_keys = ("factor_momentum_weight", "factor_volume_weight", "factor_breakout_weight", "factor_lhb_weight")
    for key in factor_keys:
        params[key] = max(0.0, params[key])
    factor_total = sum(params[key] for key in factor_keys) or 1.0
    for key in factor_keys:
        params[key] = round(params[key] / factor_total, 4)
    return params


def load_strategy_state(path: Path = STATE_FILE, now: Optional[Now] = None) -> Dict[str, Any]:
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("positions", [])
    payload.setdefault("trades", [])
    payload.setdefault(
        "model_weights",
        {"sentiment": 0.35, "event": 0.25, "technical": 0.25, "risk": 0.15},
    )
    raw_strategy_params = payload.get("strategy_params") if isinstance(payload.get("strategy_params"), dict) else {}
    has_configured_initial_cash = "account_initial_cash" in raw_strategy_params
    if "strategy_params" not in payload:
        weights = payload.get("model_weights") if isinstance(payload.get("model_weights"), dict) else {}
        payload["strategy_params"] = {
            **DEFAULT_STRATEGY_PARAMS,
            "sentiment_weight": safe_float(weights.get("sentiment"), DEFAULT_STRATEGY_PARAMS["sentiment_weight"]),
            "event_weight": safe_float(weights.get("event"), DEFAULT_STRATEGY_PARAMS["event_weight"]),
            "technical_weight": safe_float(weights.get("technical"), DEFAULT_STRATEGY_PARAMS["technical_weight"]),
            "risk_weight": safe_float(weights.get("risk"), DEFAULT_STRATEGY_PARAMS["risk_weight"]),
        }
    payload["strategy_params"] = normalize_strategy_params(payload.get("strategy_params"))
    default_cash = payload["strategy_params"]["account_initial_cash"]
    positions = payload.get("positions") if isinstance(payload.get("positions"), list) else []
    trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
    filtered_positions = [pos for pos in positions if not contains_sample_marker(pos)]
    filtered_trades = [trade for trade in trades if not contains_sample_marker(trade)]
    if len(filtered_positions) != len(positions) or len(filtered_trades) != len(trades):
        payload["positions"] = filtered_positions
        payload["trades"] = filtered_trades
        positions = filtered_positions
        trades = filtered_trades
        stamp = (now or datetime.now)().isoformat(timespec="seconds")
        payload["sample_state_filtered_at"] = stamp
    legacy_initial_cash = 1_000_000.0
    stored_initial_cash = safe_float(payload.get("initial_cash"), 0)
    stored_cash = safe_float(payload.get("cash"), 0)
    if (
        not has_configured_initial_cash
        and not positions
        and not trades
        and (stored_initial_cash <= 0 or abs(stored_initial_cash - legacy_initial_cash) < 0.01)
        and (stored_cash <= 0 or abs(stored_cash - legacy_initial_cash) < 0.01)
    ):
        payload["initial_cash"] = default_cash
        payload["cash"] = default_cash
        return payload
    initial_cash = safe_float(payload.get("initial_cash"), default_cash)
    if initial_cash <= 0:
        initial_cash = default_cash
    payload["initial_cash"] = initial_cash
    payload.setdefault("cash", initial_cash)
    return payload


def save_strategy_state(state: Dict[str, Any], path: Path = STATE_FILE) -> None:
    write_json(path, state)


def strategy_params_from_state(
    state: Dict[str, Any],
    *,
    thread_override: Optional[Dict[str, Any]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
    raw = state.get("strategy_params") if isinstance(state.get("strategy_params"), dict) else {}
    merged = {**DEFAULT_STRATEGY_PARAMS, **raw}
    if isinstance(thread_override, dict):
        merged.update(thread_override)
    if overrides:
        merged.update(overrides)
    return normalize_strategy_params(merged)


def strategy_source_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    source = state.get("strategy_source") if isinstance(state.get("strategy_source"), dict) else {}
    if not source:
        source = {
            "type": "default",
            "name": "默认系统参数",
            "description": "来自代码默认参数或早期 quant_state.json 兼容状态。",
        }
    return dict(source)


def apply_strategy_update(
    state: Dict[str, Any],
    params: Dict[str, float],
    updates: Dict[str, Any],
    source: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[Now] = None,
) -> Dict[str, Any]:
    old_initial_cash = safe_float(state.get("initial_cash"), DEFAULT_STRATEGY_PARAMS["account_initial_cash"])
    old_cash = safe_float(state.get("cash"), old_initial_cash)
    state["strategy_params"] = params
    if isinstance(updates, dict) and "account_initial_cash" in updates:
        new_initial_cash = params["account_initial_cash"]
        state["initial_cash"] = new_initial_cash
        state["cash"] = round(max(0.0, old_cash + new_initial_cash - old_initial_cash), 2)
    state["model_weights"] = {
        "sentiment": params["sentiment_weight"],
        "event": params["event_weight"],
        "technical": params["technical_weight"],
        "risk": params["risk_weight"],
    }
    state["strategy_updated_at"] = (now or datetime.now)().isoformat(timespec="seconds")
    if isinstance(source, dict) and source:
        state["strategy_source"] = {
            **source,
            "updated_at": state["strategy_updated_at"],
        }
    else:
        state["strategy_source"] = {
            "type": "manual",
            "name": "后台手动参数",
            "description": "来自后台策略参数保存。",
            "updated_at": state["strategy_updated_at"],
        }
    return {
        "status": "ok",
        "strategy_params": params,
        "strategy_source": state["strategy_source"],
        "updated_at": state["strategy_updated_at"],
    }


def apply_strategy_reset(
    state: Dict[str, Any],
    *,
    now: Optional[Now] = None,
) -> Dict[str, Any]:
    old_initial_cash = safe_float(state.get("initial_cash"), DEFAULT_STRATEGY_PARAMS["account_initial_cash"])
    old_cash = safe_float(state.get("cash"), old_initial_cash)
    params = normalize_strategy_params(DEFAULT_STRATEGY_PARAMS)
    state["strategy_params"] = params
    state["initial_cash"] = params["account_initial_cash"]
    state["cash"] = round(max(0.0, old_cash + params["account_initial_cash"] - old_initial_cash), 2)
    state["model_weights"] = {
        "sentiment": params["sentiment_weight"],
        "event": params["event_weight"],
        "technical": params["technical_weight"],
        "risk": params["risk_weight"],
    }
    state["strategy_updated_at"] = (now or datetime.now)().isoformat(timespec="seconds")
    state["strategy_source"] = {
        "type": "default",
        "name": "默认系统参数",
        "description": "后台恢复默认参数。",
        "updated_at": state["strategy_updated_at"],
    }
    return {
        "status": "ok",
        "strategy_params": params,
        "strategy_source": state["strategy_source"],
        "updated_at": state["strategy_updated_at"],
    }
