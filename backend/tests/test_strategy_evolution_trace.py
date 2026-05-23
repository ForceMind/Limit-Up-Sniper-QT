import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.evolution import StrategyEvolution
import app.quant.evolution as evolution_module


def test_candidate_records_explain_elite_selection_and_elimination():
    evolution = StrategyEvolution()
    evaluated = [
        {
            "objective": 12.5,
            "return_pct": 8.0,
            "max_drawdown_pct": -3.0,
            "sharpe_ratio": 1.2,
            "profit_factor": 1.8,
            "win_rate": 58.0,
            "closed_trades": 18,
            "params": {},
        },
        {
            "objective": -6.0,
            "return_pct": -2.5,
            "max_drawdown_pct": -4.0,
            "sharpe_ratio": -0.2,
            "profit_factor": 0.8,
            "win_rate": 35.0,
            "closed_trades": 8,
            "params": {},
        },
    ]

    rows = evolution._candidate_records_for_generation("run-a", 1, evaluated, elite_count=1)

    assert rows[0]["selected"] is True
    assert rows[0]["elimination_reason"] == "保留进入下一代精英池"
    assert rows[1]["selected"] is False
    assert rows[1]["selection_role"] == "eliminated"
    assert rows[1]["elimination_reason"] == "收益为负"
    assert rows[1]["params_hash"]


def test_strategy_account_cache_round_trips_through_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution_module, "QUANT_DB_FILE", tmp_path / "quant_data.sqlite3")
    monkeypatch.setenv("QT_STRATEGY_ACCOUNT_CACHE_TTL_SECONDS", "3600")
    evolution = StrategyEvolution()
    params = {"buy_threshold": 72, "paper_position_value": 9000}
    account = {
        "status": "ok",
        "as_of": "2026-05-19",
        "follow_start_date": "2026-05-01",
        "strategy_account_source": "model_records",
        "account": {"total_asset": 101000, "return_pct": 1.0, "position_count": 1, "deal_count": 2},
        "positions": [{"code": "600000"}],
        "history_deals": [{"code": "600000", "side": "BUY"}],
    }

    evolution.save_account_cache(
        "model-a",
        params,
        100000,
        "2026-05-01",
        "2026-05-19",
        50,
        account,
        model_version="run-a",
        source="model_records",
    )
    cached = evolution.load_account_cache(
        "model-a",
        params,
        100000,
        "2026-05-01",
        "2026-05-19",
        50,
        model_version="run-a",
    )

    assert cached
    assert cached["strategy_account_cache"] == "hit"
    assert cached["account"]["total_asset"] == 101000
    assert cached["positions"][0]["code"] == "600000"
