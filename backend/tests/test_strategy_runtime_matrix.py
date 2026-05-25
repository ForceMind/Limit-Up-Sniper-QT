from app.quant.strategy_runtime_matrix import (
    build_strategy_runtime_matrix_payload,
    clean_strategy_runtime_matrix_limit,
    strategy_runtime_catalog_items,
)


def test_strategy_runtime_catalog_items_excludes_active_duplicates_and_caps():
    payload = {
        "capital_presets": [
            {"id": "capital_10000", "name": "Small"},
            {"id": "active", "name": "Baseline"},
        ],
        "active": {"id": "active", "name": "Baseline"},
        "items": [
            {"id": "capital_10000", "name": "Duplicate"},
            {"id": "model-b", "name": "Model B"},
            {"id": "model-c", "name": "Model C"},
        ],
    }

    assert clean_strategy_runtime_matrix_limit("999") == 200
    items = strategy_runtime_catalog_items(payload, limit_models=2)

    assert [item["id"] for item in items] == ["capital_10000", "model-b"]


def test_build_strategy_runtime_matrix_payload_merges_runtime_and_signal_state():
    payload = build_strategy_runtime_matrix_payload(
        effective_as_of="2026-05-24",
        catalog_items=[
            {
                "id": "ready-model",
                "name": "Ready",
                "source": "capital",
                "params": {"account_initial_cash": 10000},
                "capital_min": 10000,
                "capital_max": 19999,
            },
            {"id": "signal-only", "name": "Signal Only", "source": "evolution"},
            {"id": "missing", "name": "Missing", "source": "evolution"},
        ],
        runtime_summaries={
            "ready-model": {
                "has_runtime_data": True,
                "runtime_source": "daily_runtime:test",
                "runtime_start_date": "2026-05-01",
                "runtime_end_date": "2026-05-24",
                "runtime_day_count": 10,
                "trade_count": 5,
                "position_count": 2,
                "return_pct": 3.4567,
                "max_drawdown_pct": -1.2345,
            }
        },
        signal_feed={
            "data_date": "2026-05-24",
            "items": [
                {
                    "model_id": "signal-only",
                    "signal_count": 1,
                    "signals": [{"date": "2026-05-24", "code": "600000", "name": "测试股票"}],
                }
            ],
        },
    )

    assert payload["status"] == "ok"
    assert payload["ready_count"] == 1
    assert payload["missing_count"] == 2
    assert payload["signal_ready_count"] == 1
    assert [row["runtime_status"] for row in payload["items"]] == ["ready", "signals_only", "missing"]
    assert payload["items"][0]["initial_cash"] == 10000
    assert payload["items"][1]["latest_signal_code"] == "600000"
