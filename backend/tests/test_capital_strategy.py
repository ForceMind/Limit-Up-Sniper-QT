import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.capital_strategy import (
    apply_capital_constraints,
    capital_presets,
    recommended_strategy_id,
)


def test_capital_presets_cover_small_cash_bands():
    presets = capital_presets({"paper_position_value": 30000, "max_positions": 5, "top_n": 10})
    ids = [item["id"] for item in presets]

    assert ids[:3] == ["capital_10000", "capital_20000_50000", "capital_50000_100000"]
    assert presets[0]["params"]["max_positions"] == 1
    assert presets[0]["params"]["paper_position_value"] <= 10000


def test_apply_capital_constraints_reduces_position_size_for_small_cash():
    params = apply_capital_constraints({"paper_position_value": 30000, "max_positions": 5, "buy_threshold": 70}, 10000)

    assert params["account_initial_cash"] == 10000
    assert params["max_positions"] == 1
    assert params["paper_position_value"] <= 10000
    assert params["buy_threshold"] >= 75
    assert params["capital_mode"] == "capital_10000"


def test_recommended_strategy_prefers_capital_band_for_small_cash():
    models = [
        {"id": "capital_10000"},
        {"id": "capital_20000_50000"},
        {"id": "evolved_large", "objective": 999, "params": {"paper_position_value": 30000, "max_positions": 5}},
    ]

    assert recommended_strategy_id(10000, models) == "capital_10000"
    assert recommended_strategy_id(30000, models) == "capital_20000_50000"

