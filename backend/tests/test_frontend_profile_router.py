from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.frontend_profile import build_frontend_profile_router


def test_frontend_profile_router_preserves_query_contract():
    calls = []

    def profile_payload(request):
        calls.append(
            {
                "endpoint": "profile",
                "path": request.url.path,
            }
        )
        return {"status": "ok", "profile": {"username": "demo"}}

    def update_profile_payload(request, payload, include_catalog):
        calls.append(
            {
                "endpoint": "update_profile",
                "path": request.url.path,
                "payload": payload,
                "include_catalog": include_catalog,
            }
        )
        return {"status": "ok", "profile_catalog_included": include_catalog}

    app = FastAPI()
    app.include_router(
        build_frontend_profile_router(
            profile_payload=profile_payload,
            update_profile_payload=update_profile_payload,
        )
    )
    client = TestClient(app)

    profile_response = client.get("/api/front/profile")
    update_response = client.post(
        "/api/front/profile",
        params={"include_catalog": "true"},
        json={"simulated_cash": 20000, "strategy_model_id": "model-a"},
    )

    assert profile_response.status_code == 200
    assert update_response.status_code == 200
    assert profile_response.json()["profile"]["username"] == "demo"
    assert update_response.json()["profile_catalog_included"] is True
    assert calls == [
        {
            "endpoint": "profile",
            "path": "/api/front/profile",
        },
        {
            "endpoint": "update_profile",
            "path": "/api/front/profile",
            "payload": {"simulated_cash": 20000, "strategy_model_id": "model-a"},
            "include_catalog": True,
        },
    ]
