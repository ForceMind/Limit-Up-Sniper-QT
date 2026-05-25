from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.admin_job_runs import build_admin_job_runs_router


def test_admin_job_runs_router_preserves_query_contracts():
    calls = []

    def news_fetch_payload(hours, pages, page_size, background, process):
        calls.append(
            {
                "endpoint": "news_fetch",
                "hours": hours,
                "pages": pages,
                "page_size": page_size,
                "background": background,
                "process": process,
            }
        )
        return {"status": "ok", "job": "news_fetch"}

    def market_sync_payload(date, source, max_codes, force, include_latest, background, process):
        calls.append(
            {
                "endpoint": "market_sync",
                "date": date,
                "source": source,
                "max_codes": max_codes,
                "force": force,
                "include_latest": include_latest,
                "background": background,
                "process": process,
            }
        )
        return {"status": "ok", "job": "market_sync"}

    def ai_analyze_payload(as_of, max_items, batch_size, background, process):
        calls.append(
            {
                "endpoint": "ai_analyze",
                "as_of": as_of,
                "max_items": max_items,
                "batch_size": batch_size,
                "background": background,
                "process": process,
            }
        )
        return {"status": "ok", "job": "ai_analysis"}

    def trading_run_payload(date, notify, background, process):
        calls.append(
            {
                "endpoint": "trading_run",
                "date": date,
                "notify": notify,
                "background": background,
                "process": process,
            }
        )
        return {"status": "ok", "job": "trade_cycle"}

    def strategy_replay_payload(start_date, end_date, mode, batch_days, use_cursor, background, process):
        calls.append(
            {
                "endpoint": "strategy_replay",
                "start_date": start_date,
                "end_date": end_date,
                "mode": mode,
                "batch_days": batch_days,
                "use_cursor": use_cursor,
                "background": background,
                "process": process,
            }
        )
        return {"status": "ok", "job": "strategy_replay"}

    def frontend_payload_precompute_payload(
        as_of,
        usernames,
        limit_users,
        force,
        background,
        process,
        lookback_days,
        top_n,
        limit_days,
        max_seconds,
    ):
        calls.append(
            {
                "endpoint": "frontend_payload_precompute",
                "as_of": as_of,
                "usernames": usernames,
                "limit_users": limit_users,
                "force": force,
                "background": background,
                "process": process,
                "lookback_days": lookback_days,
                "top_n": top_n,
                "limit_days": limit_days,
                "max_seconds": max_seconds,
            }
        )
        return {"status": "ok", "job": "frontend_payload_precompute"}

    def frontend_account_precompute_payload(
        as_of,
        usernames,
        limit_users,
        limit,
        force,
        background,
        process,
        drain_queue,
    ):
        calls.append(
            {
                "endpoint": "frontend_account_precompute",
                "as_of": as_of,
                "usernames": usernames,
                "limit_users": limit_users,
                "limit": limit,
                "force": force,
                "background": background,
                "process": process,
                "drain_queue": drain_queue,
            }
        )
        return {"status": "ok", "job": "frontend_account_precompute"}

    def system_startup_payload(
        date,
        start_date,
        end_date,
        news_hours,
        news_pages,
        ai_items,
        market_codes,
        notify,
        background,
        process,
        run_strategy_replay,
    ):
        calls.append(
            {
                "endpoint": "system_startup",
                "date": date,
                "start_date": start_date,
                "end_date": end_date,
                "news_hours": news_hours,
                "news_pages": news_pages,
                "ai_items": ai_items,
                "market_codes": market_codes,
                "notify": notify,
                "background": background,
                "process": process,
                "run_strategy_replay": run_strategy_replay,
            }
        )
        return {"status": "ok", "job": "system_startup"}

    app = FastAPI()
    app.include_router(
        build_admin_job_runs_router(
            news_fetch_payload=news_fetch_payload,
            market_sync_payload=market_sync_payload,
            ai_analyze_payload=ai_analyze_payload,
            trading_run_payload=trading_run_payload,
            strategy_replay_payload=strategy_replay_payload,
            frontend_payload_precompute_payload=frontend_payload_precompute_payload,
            frontend_account_precompute_payload=frontend_account_precompute_payload,
            system_startup_payload=system_startup_payload,
            news_fetch_process_default=False,
            market_sync_process_default=False,
            ai_analysis_process_default=False,
            trade_cycle_process_default=False,
            heavy_job_process_default=False,
            frontend_payload_process_default=False,
            frontend_account_process_default=False,
            system_startup_process_default=False,
            system_startup_run_strategy_replay_default=False,
        )
    )
    client = TestClient(app)

    responses = [
        client.post("/api/jobs/news/fetch", params={"hours": 24, "pages": 8, "page_size": 30}),
        client.post(
            "/api/jobs/market/sync",
            params={
                "date": "2026-05-25",
                "source": "events",
                "max_codes": 12,
                "force": "true",
                "include_latest": "false",
                "background": "false",
                "process": "true",
            },
        ),
        client.post(
            "/api/jobs/ai/analyze",
            params={"as_of": "2026-05-24", "max_items": 15, "batch_size": 5, "process": "true"},
        ),
        client.post(
            "/api/jobs/trading/run",
            params={"date": "2026-05-23", "notify": "false", "background": "false", "process": "true"},
        ),
        client.post(
            "/api/jobs/strategy/replay",
            params={
                "start_date": "2026-03-01",
                "end_date": "2026-05-25",
                "mode": "daily",
                "batch_days": 7,
                "use_cursor": "true",
                "process": "true",
            },
        ),
        client.post(
            "/api/jobs/frontend/precompute",
            params={
                "as_of": "2026-05-25",
                "usernames": "alice,bob",
                "limit_users": 2,
                "force": "true",
                "background": "false",
                "process": "true",
                "lookback_days": 3,
                "top_n": 20,
                "limit_days": 60,
                "max_seconds": 11,
            },
        ),
        client.post(
            "/api/jobs/frontend/account_precompute",
            params={
                "as_of": "2026-05-25",
                "usernames": "alice",
                "limit_users": 1,
                "limit": 80,
                "force": "true",
                "background": "false",
                "process": "true",
                "drain_queue": "false",
            },
        ),
        client.post(
            "/api/jobs/daily/run",
            params={"date": "2026-05-22", "notify": "false", "process": "true"},
        ),
        client.post(
            "/api/admin/system/startup",
            params={
                "date": "2026-05-25",
                "start_date": "2026-03-01",
                "end_date": "2026-05-24",
                "news_hours": 48,
                "news_pages": 6,
                "ai_items": 30,
                "market_codes": 300,
                "notify": "false",
                "background": "false",
                "process": "true",
                "run_strategy_replay": "true",
            },
        ),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert calls == [
        {
            "endpoint": "news_fetch",
            "hours": 24,
            "pages": 8,
            "page_size": 30,
            "background": True,
            "process": False,
        },
        {
            "endpoint": "market_sync",
            "date": "2026-05-25",
            "source": "events",
            "max_codes": 12,
            "force": True,
            "include_latest": False,
            "background": False,
            "process": True,
        },
        {
            "endpoint": "ai_analyze",
            "as_of": "2026-05-24",
            "max_items": 15,
            "batch_size": 5,
            "background": True,
            "process": True,
        },
        {
            "endpoint": "trading_run",
            "date": "2026-05-23",
            "notify": False,
            "background": False,
            "process": True,
        },
        {
            "endpoint": "strategy_replay",
            "start_date": "2026-03-01",
            "end_date": "2026-05-25",
            "mode": "daily",
            "batch_days": 7,
            "use_cursor": True,
            "background": True,
            "process": True,
        },
        {
            "endpoint": "frontend_payload_precompute",
            "as_of": "2026-05-25",
            "usernames": "alice,bob",
            "limit_users": 2,
            "force": True,
            "background": False,
            "process": True,
            "lookback_days": 3,
            "top_n": 20,
            "limit_days": 60,
            "max_seconds": 11,
        },
        {
            "endpoint": "frontend_account_precompute",
            "as_of": "2026-05-25",
            "usernames": "alice",
            "limit_users": 1,
            "limit": 80,
            "force": True,
            "background": False,
            "process": True,
            "drain_queue": False,
        },
        {
            "endpoint": "trading_run",
            "date": "2026-05-22",
            "notify": False,
            "background": True,
            "process": True,
        },
        {
            "endpoint": "system_startup",
            "date": "2026-05-25",
            "start_date": "2026-03-01",
            "end_date": "2026-05-24",
            "news_hours": 48,
            "news_pages": 6,
            "ai_items": 30,
            "market_codes": 300,
            "notify": False,
            "background": False,
            "process": True,
            "run_strategy_replay": True,
        },
    ]
