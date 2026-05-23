import sqlite3
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


def test_user_follow_account_persists_user_scoped_snapshot_and_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution_module, "QUANT_DB_FILE", tmp_path / "quant_data.sqlite3")
    monkeypatch.setenv("QT_USER_FOLLOW_ACCOUNT_CACHE_TTL_SECONDS", "3600")
    evolution = StrategyEvolution()
    params = {"buy_threshold": 72, "paper_position_value": 9000}
    account = {
        "status": "ok",
        "as_of": "2026-05-19",
        "follow_start_date": "2026-05-01",
        "strategy_account_source": "runtime_tables",
        "account": {"total_asset": 102500, "return_pct": 2.5, "position_count": 1, "deal_count": 2},
        "positions": [
            {
                "code": "600000",
                "name": "娴﹀彂閾惰",
                "qty": 100,
                "entry_date": "2026-05-18",
                "entry_price": 10,
                "last_price": 10.25,
                "market_value": 1025,
                "pnl_pct": 2.5,
            }
        ],
        "history_deals": [
            {"date": "2026-05-18", "time": "09:30:00", "side": "BUY", "code": "600000", "qty": 100, "price": 10, "amount": 1000},
        ],
        "today_deals": [
            {"date": "2026-05-19", "time": "14:55:00", "side": "SELL", "code": "600001", "qty": 100, "price": 12, "amount": 1200, "pnl_pct": 5},
        ],
    }

    evolution.save_user_follow_account(
        "alice",
        "model-a",
        params,
        100000,
        "2026-05-01",
        "2026-05-19",
        50,
        account,
        model_version="run-a",
        source="runtime_tables",
    )
    cached = evolution.load_user_follow_account(
        "alice",
        "model-a",
        100000,
        "2026-05-01",
        "2026-05-19",
        50,
        model_version="run-a",
        params=params,
    )
    other_user = evolution.load_user_follow_account(
        "bob",
        "model-a",
        100000,
        "2026-05-01",
        "2026-05-19",
        50,
        model_version="run-a",
        params=params,
    )

    assert cached
    assert cached["strategy_account_cache"] == "user_follow"
    assert cached["account"]["total_asset"] == 102500
    assert cached["positions"][0]["code"] == "600000"
    assert other_user is None
    with sqlite3.connect(tmp_path / "quant_data.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_follow_snapshots").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM user_follow_positions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM user_follow_trades").fetchone()[0] == 2


def test_strategy_daily_runtime_persists_and_loads_follow_account(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution_module, "QUANT_DB_FILE", tmp_path / "quant_data.sqlite3")
    evolution = StrategyEvolution()
    model = {"id": "model-runtime-a", "name": "运行表测试策略", "run_id": "run-a", "generated_at": "2026-05-20"}
    params = {"account_initial_cash": 100000, "max_positions": 2}
    timeline = {
        "mode": "daily",
        "start_date": "2026-05-01",
        "end_date": "2026-05-04",
        "initial_cash": 100000,
        "trades": [
            {"date": "2026-05-01", "time": "09:30:00", "side": "BUY", "code": "600000", "name": "浦发银行", "qty": 300, "price": 10, "score": 80},
            {"date": "2026-05-02", "time": "14:55:00", "side": "SELL", "code": "600000", "name": "浦发银行", "qty": 300, "price": 11, "pnl_pct": 10},
            {"date": "2026-05-03", "time": "09:30:00", "side": "BUY", "code": "600003", "name": "东北高速", "qty": 300, "price": 8, "score": 82},
        ],
        "days": [
            {
                "date": "2026-05-01",
                "signals": [{"code": "600000", "name": "浦发银行", "buy_score": 80, "sell_score": 20, "reason": "测试信号"}],
                "positions": [{"code": "600000", "name": "浦发银行", "qty": 300, "entry_date": "2026-05-01", "entry_price": 10, "last_price": 10, "market_value": 3000, "pnl_pct": 0}],
            },
            {
                "date": "2026-05-03",
                "signals": [{"code": "600003", "name": "东北高速", "buy_score": 82, "sell_score": 18, "reason": "测试信号"}],
                "positions": [{"code": "600003", "name": "东北高速", "qty": 300, "entry_date": "2026-05-03", "entry_price": 8, "last_price": 8, "market_value": 2400, "pnl_pct": 0}],
            },
        ],
    }

    persisted = evolution.save_daily_runtime(
        model=model,
        params=params,
        timeline=timeline,
        start_date="2026-05-01",
        end_date="2026-05-04",
        mode="daily",
    )
    account = evolution.load_runtime_account(
        "model-runtime-a",
        100000,
        "2026-05-02",
        "2026-05-04",
        50,
        model_version=evolution.runtime_model_version(model),
        params=params,
    )

    assert persisted["status"] == "ok"
    assert persisted["signal_count"] == 2
    assert persisted["trade_count"] == 3
    assert persisted["position_count"] == 2
    assert persisted["settlement_count"] == 3
    assert persisted["snapshot_count"] == 2
    assert account
    assert account["strategy_account_source"] == "runtime_tables"
    assert account["follow_start_date"] == "2026-05-02"
    assert account["runtime_signal_count"] == 1
    assert account["runtime_settlement_count"] == 2
    assert account["runtime_snapshot_as_of"] == "2026-05-03"
    assert {deal["code"] for deal in account["history_deals"]} == {"600003"}


def test_runtime_model_summary_uses_daily_runtime_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution_module, "QUANT_DB_FILE", tmp_path / "quant_data.sqlite3")
    evolution = StrategyEvolution()
    model = {"id": "capital_10000", "name": "小资金策略", "run_id": "capital-band"}
    params = {"account_initial_cash": 10000, "max_positions": 1, "paper_position_value": 9000}
    timeline = {
        "mode": "daily",
        "start_date": "2026-05-01",
        "end_date": "2026-05-03",
        "initial_cash": 10000,
        "trades": [
            {"date": "2026-05-01", "time": "09:30:00", "side": "BUY", "code": "600000", "name": "浦发银行", "qty": 100, "price": 10, "score": 81},
            {"date": "2026-05-03", "time": "14:55:00", "side": "SELL", "code": "600000", "name": "浦发银行", "qty": 100, "price": 11, "pnl_pct": 10},
        ],
        "days": [
            {
                "date": "2026-05-01",
                "total_value": 10000,
                "cash": 9000,
                "market_value": 1000,
                "signals": [{"code": "600000", "name": "浦发银行", "buy_score": 81, "sell_score": 16, "reason": "测试信号"}],
                "positions": [{"code": "600000", "name": "浦发银行", "qty": 100, "entry_date": "2026-05-01", "entry_price": 10, "last_price": 10, "market_value": 1000, "pnl_pct": 0}],
            },
            {
                "date": "2026-05-02",
                "total_value": 10050,
                "cash": 9000,
                "market_value": 1050,
                "signals": [],
                "positions": [{"code": "600000", "name": "浦发银行", "qty": 100, "entry_date": "2026-05-01", "entry_price": 10, "last_price": 10.5, "market_value": 1050, "pnl_pct": 5}],
            },
            {
                "date": "2026-05-03",
                "total_value": 10100,
                "cash": 10100,
                "market_value": 0,
                "signals": [],
                "positions": [],
            },
        ],
    }

    persisted = evolution.save_daily_runtime(
        model=model,
        params=params,
        timeline=timeline,
        start_date="2026-05-01",
        end_date="2026-05-03",
        mode="daily",
    )
    summaries = evolution.runtime_model_summaries([{**model, "params": params}])

    assert persisted["status"] == "ok"
    assert summaries["capital_10000"]["has_runtime_data"] is True
    assert summaries["capital_10000"]["runtime_day_count"] == 3
    assert summaries["capital_10000"]["trade_count"] == 2
    assert summaries["capital_10000"]["closed_trades"] == 1
    assert summaries["capital_10000"]["win_rate"] == 100
    assert summaries["capital_10000"]["return_pct"] == 1
