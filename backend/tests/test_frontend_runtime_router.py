from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.frontend_runtime import build_frontend_runtime_router


def test_frontend_runtime_router_preserves_query_contract():
    calls = []

    def public_snapshot_payload(as_of=None, mobile=False, light=True):
        calls.append(
            {
                "endpoint": "public_snapshot",
                "as_of": as_of,
                "mobile": mobile,
                "light": light,
            }
        )
        return {"status": "ok", "source": "public"}

    def snapshot_payload(request, as_of=None, mobile=False, light=True, include_catalog=False):
        calls.append(
            {
                "endpoint": "snapshot",
                "path": request.url.path,
                "as_of": as_of,
                "mobile": mobile,
                "light": light,
                "include_catalog": include_catalog,
            }
        )
        return {"status": "ok", "source": "snapshot"}

    def strategy_models_payload(request):
        calls.append(
            {
                "endpoint": "strategy_models",
                "path": request.url.path,
            }
        )
        return {"status": "ok", "strategy_catalog_included": True}

    def trading_account_payload(request, as_of=None, limit=500, force=False, defer=True):
        calls.append(
            {
                "endpoint": "trading_account",
                "path": request.url.path,
                "as_of": as_of,
                "limit": limit,
                "force": force,
                "defer": defer,
            }
        )
        return {"status": "ok", "account": {"cash": 20000}}

    app = FastAPI()
    app.include_router(
        build_frontend_runtime_router(
            public_snapshot_payload=public_snapshot_payload,
            snapshot_payload=snapshot_payload,
            strategy_models_payload=strategy_models_payload,
            trading_account_payload=trading_account_payload,
            account_defer_default=False,
        )
    )
    client = TestClient(app)

    public_response = client.get(
        "/api/front/public_snapshot",
        params={"as_of": "2026-05-24", "mobile": "true", "light": "false"},
    )
    snapshot_response = client.get(
        "/api/front/snapshot",
        params={"as_of": "2026-05-25", "mobile": "true", "light": "false", "include_catalog": "true"},
    )
    models_response = client.get("/api/front/strategy_models")
    account_response = client.get(
        "/api/front/trading_account",
        params={"as_of": "2026-05-25", "limit": 80, "force": "true", "defer": "true"},
    )

    assert public_response.status_code == 200
    assert snapshot_response.status_code == 200
    assert models_response.status_code == 200
    assert account_response.status_code == 200
    assert calls == [
        {
            "endpoint": "public_snapshot",
            "as_of": "2026-05-24",
            "mobile": True,
            "light": False,
        },
        {
            "endpoint": "snapshot",
            "path": "/api/front/snapshot",
            "as_of": "2026-05-25",
            "mobile": True,
            "light": False,
            "include_catalog": True,
        },
        {
            "endpoint": "strategy_models",
            "path": "/api/front/strategy_models",
        },
        {
            "endpoint": "trading_account",
            "path": "/api/front/trading_account",
            "as_of": "2026-05-25",
            "limit": 80,
            "force": True,
            "defer": True,
        },
    ]


def test_frontend_runtime_router_uses_configured_account_defer_default():
    calls = []

    def noop_public(as_of=None, mobile=False, light=True):
        return {"status": "ok"}

    def noop_snapshot(request, as_of=None, mobile=False, light=True, include_catalog=False):
        return {"status": "ok"}

    def noop_models(request):
        return {"status": "ok"}

    def trading_account_payload(request, as_of=None, limit=500, force=False, defer=True):
        calls.append(defer)
        return {"status": "ok"}

    app = FastAPI()
    app.include_router(
        build_frontend_runtime_router(
            public_snapshot_payload=noop_public,
            snapshot_payload=noop_snapshot,
            strategy_models_payload=noop_models,
            trading_account_payload=trading_account_payload,
            account_defer_default=False,
        )
    )
    client = TestClient(app)

    response = client.get("/api/front/trading_account")

    assert response.status_code == 200
    assert calls == [False]
