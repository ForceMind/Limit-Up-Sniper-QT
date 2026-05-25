from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.admin_overview import build_admin_overview_router


def test_admin_overview_router_preserves_query_contract():
    calls = []

    def snapshot_payload(as_of=None, light=True):
        calls.append(
            {
                "endpoint": "snapshot",
                "as_of": as_of,
                "light": light,
            }
        )
        return {"status": "ok", "source": "snapshot"}

    def model_signals_payload(as_of=None, limit_models=24, limit_per_model=12):
        calls.append(
            {
                "endpoint": "model_signals",
                "as_of": as_of,
                "limit_models": limit_models,
                "limit_per_model": limit_per_model,
            }
        )
        return {"status": "ok", "items": []}

    app = FastAPI()
    app.include_router(
        build_admin_overview_router(
            snapshot_payload=snapshot_payload,
            model_signals_payload=model_signals_payload,
        )
    )
    client = TestClient(app)

    snapshot_response = client.get(
        "/api/admin/snapshot",
        params={"as_of": "2026-05-25", "light": "false"},
    )
    signals_response = client.get(
        "/api/admin/model_signals",
        params={"as_of": "2026-05-24", "limit_models": 12, "limit_per_model": 8},
    )

    assert snapshot_response.status_code == 200
    assert signals_response.status_code == 200
    assert calls == [
        {
            "endpoint": "snapshot",
            "as_of": "2026-05-25",
            "light": False,
        },
        {
            "endpoint": "model_signals",
            "as_of": "2026-05-24",
            "limit_models": 12,
            "limit_per_model": 8,
        },
    ]
