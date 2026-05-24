import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant import frontend_precompute
from app.quant import runtime_cache


def test_frontend_precompute_writes_recommendations_and_daily_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_cache, "QUANT_DB_FILE", tmp_path / "quant_data.sqlite3")
    monkeypatch.setattr(
        frontend_precompute,
        "frontend_user_summary",
        lambda: {
            "status": "ok",
            "items": [
                {
                    "username": "alice",
                    "created_at": "2026-05-01T09:30:00",
                    "profile": {
                        "simulated_cash": 10000,
                        "strategy_model_id": "capital_10000",
                        "follow_start_date": "2026-05-10",
                    },
                    "disabled": False,
                }
            ],
        },
    )
    monkeypatch.setattr(
        frontend_precompute.strategy_evolution,
        "models",
        lambda limit=80, include_records=False: {"status": "ok", "active": {"id": "active", "params": {}}, "items": [], "count": 0},
    )
    monkeypatch.setattr(
        frontend_precompute.quant_engine,
        "strategy_params",
        lambda updates=None: {
            "account_initial_cash": 10000,
            "max_positions": 1,
            "paper_position_value": 9000,
            "top_n": 4,
            "buy_threshold": 75,
            **(updates if isinstance(updates, dict) else {}),
        },
    )
    monkeypatch.setattr(frontend_precompute.quant_engine, "strategy_source", lambda: {"type": "test"})
    monkeypatch.setattr(frontend_precompute.quant_engine, "latest_event_date", lambda: "2026-05-20")
    monkeypatch.setattr(frontend_precompute.quant_engine, "first_data_date", lambda: "2026-05-01")
    monkeypatch.setattr(frontend_precompute.quant_engine, "latest_price", lambda code, as_of=None: {"close": 10})
    monkeypatch.setattr(
        frontend_precompute.quant_engine,
        "recommendations",
        lambda as_of=None, lookback_days=2, top_n=30: {"status": "ok", "as_of": as_of, "items": [{"code": "600000", "buy_score": 88}]},
    )
    monkeypatch.setattr(
        frontend_precompute.quant_engine,
        "daily_plan",
        lambda as_of=None, start_date=None, limit_days=120: {"status": "ok", "as_of": as_of, "start_date": start_date, "buy_list": [{"code": "600000"}]},
    )

    result = frontend_precompute.precompute_frontend_payloads(limit_users=1, top_n=12, limit_days=120)

    assert result["status"] == "ok"
    assert result["saved"] == 2
    context = frontend_precompute.frontend_user_contexts(limit_users=1)[0]
    recommendations = runtime_cache.load_payload_cache(
        "front_recommendations",
        frontend_precompute.frontend_payload_cache_parts(
            context,
            "front_recommendations",
            {"as_of": "2026-05-20", "lookback_days": 2, "top_n": 12},
        ),
        ttl_seconds=900,
    )
    daily_plan = runtime_cache.load_payload_cache(
        "front_daily_plan",
        frontend_precompute.frontend_payload_cache_parts(
            context,
            "front_daily_plan",
            {"as_of": "2026-05-20", "start_date": "2026-05-01", "limit_days": 120},
        ),
        ttl_seconds=1800,
    )

    assert recommendations["items"][0]["affordable"] is True
    assert daily_plan["buy_list"][0]["max_buy_qty"] == 900
