from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.admin_strategy_runtime import build_admin_strategy_runtime_router


def test_admin_strategy_runtime_router_preserves_query_contract():
    calls = []

    def matrix_payload(as_of=None, limit_models=80, include_signals=True):
        calls.append(
            {
                "endpoint": "matrix",
                "as_of": as_of,
                "limit_models": limit_models,
                "include_signals": include_signals,
            }
        )
        return {"status": "ok", "items": [], "count": 0}

    def trading_payload(as_of=None, model_id=None, initial_cash=None, start_date=None, limit=1000):
        calls.append(
            {
                "endpoint": "trading",
                "as_of": as_of,
                "model_id": model_id,
                "initial_cash": initial_cash,
                "start_date": start_date,
                "limit": limit,
            }
        )
        return {"status": "ok", "strategy_model_id": model_id}

    def replay_payload(as_of=None, model_id=None, initial_cash=None, start_date=None, limit=1000):
        calls.append(
            {
                "endpoint": "replay",
                "as_of": as_of,
                "model_id": model_id,
                "initial_cash": initial_cash,
                "start_date": start_date,
                "limit": limit,
            }
        )
        return {"status": "ok", "source": "strategy_runtime"}

    app = FastAPI()
    app.include_router(
        build_admin_strategy_runtime_router(
            matrix_payload=matrix_payload,
            trading_account_payload=trading_payload,
            replay_payload=replay_payload,
        )
    )
    client = TestClient(app)

    matrix_response = client.get(
        "/api/admin/strategy_runtime/matrix",
        params={"as_of": "2026-05-24", "limit_models": 12, "include_signals": "false"},
    )
    trading_response = client.get(
        "/api/admin/trading_account",
        params={
            "as_of": "2026-05-24",
            "model_id": "capital_10000",
            "initial_cash": 20000,
            "start_date": "2026-05-01",
            "limit": 12,
        },
    )
    replay_response = client.get(
        "/api/admin/strategy_runtime/replay",
        params={
            "as_of": "2026-05-24",
            "model_id": "model-b",
            "initial_cash": 30000,
            "start_date": "2026-05-02",
            "limit": 20,
        },
    )

    assert matrix_response.status_code == 200
    assert trading_response.status_code == 200
    assert replay_response.status_code == 200
    assert matrix_response.json()["status"] == "ok"
    assert trading_response.json()["strategy_model_id"] == "capital_10000"
    assert replay_response.json()["source"] == "strategy_runtime"
    assert calls == [
        {
            "endpoint": "matrix",
            "as_of": "2026-05-24",
            "limit_models": 12,
            "include_signals": False,
        },
        {
            "endpoint": "trading",
            "as_of": "2026-05-24",
            "model_id": "capital_10000",
            "initial_cash": 20000.0,
            "start_date": "2026-05-01",
            "limit": 12,
        },
        {
            "endpoint": "replay",
            "as_of": "2026-05-24",
            "model_id": "model-b",
            "initial_cash": 30000.0,
            "start_date": "2026-05-02",
            "limit": 20,
        },
    ]
