from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.quant_basic import build_quant_basic_router


def test_quant_basic_router_preserves_query_contracts():
    calls = []

    def record(endpoint, **payload):
        calls.append({"endpoint": endpoint, **payload})
        return {"status": "ok", "endpoint": endpoint}

    app = FastAPI()
    app.include_router(
        build_quant_basic_router(
            dashboard_payload=lambda as_of, light: record("dashboard", as_of=as_of, light=light),
            recommendations_payload=lambda as_of, lookback_days, top_n: record(
                "recommendations",
                as_of=as_of,
                lookback_days=lookback_days,
                top_n=top_n,
            ),
            daily_plan_payload=lambda as_of, start_date, limit_days: record(
                "daily_plan",
                as_of=as_of,
                start_date=start_date,
                limit_days=limit_days,
            ),
            strategy_params_payload=lambda: record("strategy_params"),
            strategy_params_update_payload=lambda payload: record("strategy_params_update", payload=payload),
            strategy_params_reset_payload=lambda: record("strategy_params_reset"),
            events_payload=lambda as_of, limit: record("events", as_of=as_of, limit=limit),
            news_payload=lambda as_of, limit, fallback_latest, source, keyword, code: record(
                "news",
                as_of=as_of,
                limit=limit,
                fallback_latest=fallback_latest,
                source=source,
                keyword=keyword,
                code=code,
            ),
            correlation_payload=lambda as_of, hold_days: record("correlation", as_of=as_of, hold_days=hold_days),
            portfolio_payload=lambda as_of: record("portfolio", as_of=as_of),
            trading_account_payload=lambda as_of, limit: record("trading_account", as_of=as_of, limit=limit),
            run_payload=lambda as_of, calibrate: record("run", as_of=as_of, calibrate=calibrate),
            news_history_payload=lambda limit: record("news_history", limit=limit),
        )
    )
    client = TestClient(app)

    responses = [
        client.get("/api/quant/dashboard", params={"as_of": "2026-05-24", "light": "true"}),
        client.get("/api/quant/recommendations", params={"as_of": "2026-05-24", "lookback_days": 3, "top_n": 12}),
        client.get("/api/quant/daily_plan", params={"as_of": "2026-05-24", "start_date": "2026-05-01", "limit_days": 60}),
        client.get("/api/quant/strategy_params"),
        client.post("/api/quant/strategy_params", json={"buy_threshold": 72}),
        client.post("/api/quant/strategy_params/reset"),
        client.get("/api/quant/events", params={"as_of": "2026-05-24", "limit": 7}),
        client.get(
            "/api/quant/news",
            params={
                "as_of": "2026-05-24",
                "limit": 9,
                "fallback_latest": "false",
                "source": "Fixture",
                "keyword": "AI",
                "code": "600001",
            },
        ),
        client.get("/api/quant/correlation", params={"as_of": "2026-05-24", "hold_days": 5}),
        client.get("/api/quant/portfolio", params={"as_of": "2026-05-24"}),
        client.get("/api/quant/trading_account", params={"as_of": "2026-05-24", "limit": 30}),
        client.post("/api/quant/run", params={"as_of": "2026-05-24", "calibrate": "false"}),
        client.get("/api/news_history", params={"limit": 12}),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert calls == [
        {"endpoint": "dashboard", "as_of": "2026-05-24", "light": True},
        {"endpoint": "recommendations", "as_of": "2026-05-24", "lookback_days": 3, "top_n": 12},
        {"endpoint": "daily_plan", "as_of": "2026-05-24", "start_date": "2026-05-01", "limit_days": 60},
        {"endpoint": "strategy_params"},
        {"endpoint": "strategy_params_update", "payload": {"buy_threshold": 72}},
        {"endpoint": "strategy_params_reset"},
        {"endpoint": "events", "as_of": "2026-05-24", "limit": 7},
        {
            "endpoint": "news",
            "as_of": "2026-05-24",
            "limit": 9,
            "fallback_latest": False,
            "source": "Fixture",
            "keyword": "AI",
            "code": "600001",
        },
        {"endpoint": "correlation", "as_of": "2026-05-24", "hold_days": 5},
        {"endpoint": "portfolio", "as_of": "2026-05-24"},
        {"endpoint": "trading_account", "as_of": "2026-05-24", "limit": 30},
        {"endpoint": "run", "as_of": "2026-05-24", "calibrate": False},
        {"endpoint": "news_history", "limit": 12},
    ]
