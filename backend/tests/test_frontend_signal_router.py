from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.frontend_signal import build_frontend_signal_router


def test_frontend_signal_router_preserves_query_contract():
    calls = []

    def recommendations_payload(request, as_of=None, lookback_days=2, top_n=30, force=False, defer=True):
        calls.append(
            {
                "endpoint": "recommendations",
                "path": request.url.path,
                "as_of": as_of,
                "lookback_days": lookback_days,
                "top_n": top_n,
                "force": force,
                "defer": defer,
            }
        )
        return {"status": "ok", "items": []}

    def daily_plan_payload(request, as_of=None, start_date=None, limit_days=120, force=False, defer=True):
        calls.append(
            {
                "endpoint": "daily_plan",
                "path": request.url.path,
                "as_of": as_of,
                "start_date": start_date,
                "limit_days": limit_days,
                "force": force,
                "defer": defer,
            }
        )
        return {"status": "ok", "days": []}

    app = FastAPI()
    app.include_router(
        build_frontend_signal_router(
            recommendations_payload=recommendations_payload,
            daily_plan_payload=daily_plan_payload,
            payload_defer_default=False,
        )
    )
    client = TestClient(app)

    recommendations_response = client.get(
        "/api/front/recommendations",
        params={
            "as_of": "2026-05-25",
            "lookback_days": 5,
            "top_n": 12,
            "force": "true",
            "defer": "true",
        },
    )
    daily_plan_response = client.get(
        "/api/front/daily_plan",
        params={
            "as_of": "2026-05-25",
            "start_date": "2026-05-01",
            "limit_days": 60,
            "force": "true",
            "defer": "true",
        },
    )

    assert recommendations_response.status_code == 200
    assert daily_plan_response.status_code == 200
    assert calls == [
        {
            "endpoint": "recommendations",
            "path": "/api/front/recommendations",
            "as_of": "2026-05-25",
            "lookback_days": 5,
            "top_n": 12,
            "force": True,
            "defer": True,
        },
        {
            "endpoint": "daily_plan",
            "path": "/api/front/daily_plan",
            "as_of": "2026-05-25",
            "start_date": "2026-05-01",
            "limit_days": 60,
            "force": True,
            "defer": True,
        },
    ]


def test_frontend_signal_router_uses_configured_payload_defer_default():
    calls = []

    def recommendations_payload(request, as_of=None, lookback_days=2, top_n=30, force=False, defer=True):
        calls.append(("recommendations", defer))
        return {"status": "ok"}

    def daily_plan_payload(request, as_of=None, start_date=None, limit_days=120, force=False, defer=True):
        calls.append(("daily_plan", defer))
        return {"status": "ok"}

    app = FastAPI()
    app.include_router(
        build_frontend_signal_router(
            recommendations_payload=recommendations_payload,
            daily_plan_payload=daily_plan_payload,
            payload_defer_default=False,
        )
    )
    client = TestClient(app)

    recommendations_response = client.get("/api/front/recommendations")
    daily_plan_response = client.get("/api/front/daily_plan")

    assert recommendations_response.status_code == 200
    assert daily_plan_response.status_code == 200
    assert calls == [("recommendations", False), ("daily_plan", False)]
