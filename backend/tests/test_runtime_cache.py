import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.quant.runtime_cache as runtime_cache


def test_frontend_payload_cache_round_trips_and_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_cache, "QUANT_DB_FILE", tmp_path / "quant_data.sqlite3")
    parts = {"as_of": "2026-05-19", "strategy_model_id": "capital_10000", "cash": 10000}
    payload = {"status": "ok", "items": [{"code": "600000"}]}

    runtime_cache.save_payload_cache("front_recommendations", parts, payload, ttl_seconds=60)
    cached = runtime_cache.load_payload_cache("front_recommendations", parts, ttl_seconds=60)

    assert cached
    assert cached["frontend_payload_cache"] == "hit"
    assert cached["items"][0]["code"] == "600000"

    runtime_cache.save_payload_cache("front_recommendations", {"as_of": "2026-05-20"}, payload, ttl_seconds=0)
    assert runtime_cache.load_payload_cache("front_recommendations", {"as_of": "2026-05-20"}, ttl_seconds=60) is None


def test_runtime_cache_status_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_cache, "QUANT_DB_FILE", tmp_path / "quant_data.sqlite3")
    runtime_cache.save_payload_cache(
        "front_daily_plan",
        {"as_of": "2026-05-19", "strategy": "capital_10000"},
        {"status": "ok", "buy_list": []},
        ttl_seconds=60,
    )

    status = runtime_cache.runtime_cache_status()
    assert status["status"] == "ok"
    assert status["tables"]["frontend_payload_cache"]["row_count"] == 1
    assert status["total_rows"] >= 1

    result = runtime_cache.clear_runtime_cache("payload")
    assert result["status"] == "ok"
    assert result["deleted"]["frontend_payload_cache"] == 1
    assert result["cache"]["tables"]["frontend_payload_cache"]["row_count"] == 0
