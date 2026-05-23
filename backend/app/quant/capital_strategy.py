from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.quant.engine import safe_float


DEFAULT_FRONTEND_STRATEGY_ID = "capital_10000"

CAPITAL_BANDS: List[Dict[str, Any]] = [
    {
        "id": "capital_10000",
        "name": "1万小资金策略",
        "label": "1万",
        "min_cash": 10_000.0,
        "max_cash": 19_999.99,
        "default_cash": 10_000.0,
        "max_positions": 1.0,
        "top_n": 4.0,
        "paper_position_value": 9_000.0,
        "buy_threshold": 75.0,
        "description": "只允许一只持仓，优先筛掉一手金额过高的股票。",
    },
    {
        "id": "capital_20000_50000",
        "name": "2万-5万小资金策略",
        "label": "2万-5万",
        "min_cash": 20_000.0,
        "max_cash": 49_999.99,
        "default_cash": 30_000.0,
        "max_positions": 2.0,
        "top_n": 6.0,
        "paper_position_value": 13_000.0,
        "buy_threshold": 73.0,
        "description": "最多两只持仓，控制单票资金，避免资金被高价股占满。",
    },
    {
        "id": "capital_50000_100000",
        "name": "5万-10万中小资金策略",
        "label": "5万-10万",
        "min_cash": 50_000.0,
        "max_cash": 99_999.99,
        "default_cash": 80_000.0,
        "max_positions": 3.0,
        "top_n": 8.0,
        "paper_position_value": 24_000.0,
        "buy_threshold": 72.0,
        "description": "最多三只持仓，在分散和可买一手之间做平衡。",
    },
    {
        "id": "capital_100000_plus",
        "name": "10万以上标准策略",
        "label": "10万以上",
        "min_cash": 100_000.0,
        "max_cash": 10_000_000.0,
        "default_cash": 200_000.0,
        "max_positions": 5.0,
        "top_n": 10.0,
        "paper_position_value": 30_000.0,
        "buy_threshold": 72.0,
        "description": "使用标准多仓位配置，适合跟随进化模型。",
    },
]


def is_capital_strategy_id(model_id: Any) -> bool:
    text = str(model_id or "").strip()
    return any(text == str(band["id"]) for band in CAPITAL_BANDS)


def capital_band_for_cash(cash: Any) -> Dict[str, Any]:
    value = max(10_000.0, min(10_000_000.0, safe_float(cash, 10_000.0)))
    for band in CAPITAL_BANDS:
        if safe_float(band.get("min_cash"), 0) <= value <= safe_float(band.get("max_cash"), 0):
            return dict(band)
    return dict(CAPITAL_BANDS[-1])


def apply_capital_constraints(params: Optional[Dict[str, Any]], cash: Any) -> Dict[str, Any]:
    base = dict(params) if isinstance(params, dict) else {}
    simulated_cash = max(10_000.0, min(10_000_000.0, safe_float(cash, 10_000.0)))
    band = capital_band_for_cash(simulated_cash)
    band_max_positions = max(1.0, safe_float(band.get("max_positions"), 1.0))
    current_positions = max(1.0, safe_float(base.get("max_positions"), band_max_positions))
    max_positions = min(current_positions, band_max_positions)
    position_cap = simulated_cash / max_positions
    band_position_value = safe_float(band.get("paper_position_value"), position_cap)
    current_position_value = safe_float(base.get("paper_position_value"), band_position_value)
    paper_position_value = min(current_position_value, band_position_value, position_cap)

    base["account_initial_cash"] = round(simulated_cash, 2)
    base["max_positions"] = max_positions
    base["top_n"] = min(max(1.0, safe_float(base.get("top_n"), band.get("top_n"))), safe_float(band.get("top_n"), 10.0))
    base["buy_threshold"] = max(safe_float(base.get("buy_threshold"), band.get("buy_threshold")), safe_float(band.get("buy_threshold"), 72.0))
    base["paper_position_value"] = round(max(1_000.0, paper_position_value), 2)
    base["capital_mode"] = str(band["id"])
    base["capital_label"] = str(band["label"])
    base["capital_note"] = str(band["description"])
    return base


def capital_presets(base_params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    presets: List[Dict[str, Any]] = []
    for band in CAPITAL_BANDS:
        default_cash = safe_float(band.get("default_cash"), 10_000.0)
        params = apply_capital_constraints(base_params, default_cash)
        params["max_positions"] = safe_float(band.get("max_positions"), params.get("max_positions"))
        params["top_n"] = safe_float(band.get("top_n"), params.get("top_n"))
        params["paper_position_value"] = safe_float(band.get("paper_position_value"), params.get("paper_position_value"))
        params["buy_threshold"] = max(safe_float(params.get("buy_threshold"), 72.0), safe_float(band.get("buy_threshold"), 72.0))
        presets.append(
            {
                "id": str(band["id"]),
                "name": str(band["name"]),
                "source": "capital_preset",
                "reusable": True,
                "is_capital_preset": True,
                "rank": str(band["label"]),
                "capital_min": safe_float(band.get("min_cash"), 0),
                "capital_max": safe_float(band.get("max_cash"), 0),
                "description": str(band.get("description") or ""),
                "params": params,
                "objective": None,
                "return_pct": None,
                "max_drawdown_pct": None,
                "win_rate": None,
                "closed_trades": 0,
            }
        )
    return presets


def recommended_strategy_id(cash: Any, models: Iterable[Dict[str, Any]]) -> str:
    simulated_cash = max(10_000.0, min(10_000_000.0, safe_float(cash, 10_000.0)))
    band = capital_band_for_cash(simulated_cash)
    if simulated_cash < 100_000:
        return str(band["id"])

    candidates: List[Dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("id") or "").strip()
        if not model_id or model_id == "active" or is_capital_strategy_id(model_id):
            continue
        params = model.get("params") if isinstance(model.get("params"), dict) else {}
        position_value = safe_float(params.get("paper_position_value"), 0)
        max_positions = max(1.0, safe_float(params.get("max_positions"), 1))
        if position_value <= 0 or position_value > simulated_cash / max_positions:
            continue
        candidates.append(model)
    if candidates:
        best = max(
            candidates,
            key=lambda item: (
                safe_float(item.get("objective"), 0),
                safe_float(item.get("return_pct"), 0),
                safe_float(item.get("win_rate"), 0),
            ),
        )
        return str(best.get("id") or band["id"])
    return str(band["id"])

