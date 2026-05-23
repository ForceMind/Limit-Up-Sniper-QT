import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.evolution import StrategyEvolution


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
