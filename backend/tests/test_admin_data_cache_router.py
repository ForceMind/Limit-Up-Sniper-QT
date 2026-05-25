from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.admin_data_cache import build_admin_data_cache_router


def test_admin_data_cache_router_preserves_query_contract():
    calls = []

    def database_tables_payload():
        calls.append({"endpoint": "database_tables"})
        return {"status": "ok", "tables": []}

    def database_table_payload(table_name, limit=50, offset=0):
        calls.append(
            {
                "endpoint": "database_table",
                "table_name": table_name,
                "limit": limit,
                "offset": offset,
            }
        )
        return {"status": "ok", "table_name": table_name, "items": []}

    def cache_status_payload():
        calls.append({"endpoint": "cache_status"})
        return {"status": "ok"}

    def cache_clear_payload(scope="expired"):
        calls.append(
            {
                "endpoint": "cache_clear",
                "scope": scope,
            }
        )
        return {"status": "ok", "scope": scope}

    app = FastAPI()
    app.include_router(
        build_admin_data_cache_router(
            database_tables_payload=database_tables_payload,
            database_table_payload=database_table_payload,
            cache_status_payload=cache_status_payload,
            cache_clear_payload=cache_clear_payload,
        )
    )
    client = TestClient(app)

    tables_response = client.get("/api/admin/database/tables")
    table_response = client.get(
        "/api/admin/database/table/strategy_runtime_trades",
        params={"limit": 25, "offset": 50},
    )
    cache_response = client.get("/api/admin/cache/status")
    clear_response = client.post("/api/admin/cache/clear", params={"scope": "all"})

    assert tables_response.status_code == 200
    assert table_response.status_code == 200
    assert cache_response.status_code == 200
    assert clear_response.status_code == 200
    assert calls == [
        {"endpoint": "database_tables"},
        {
            "endpoint": "database_table",
            "table_name": "strategy_runtime_trades",
            "limit": 25,
            "offset": 50,
        },
        {"endpoint": "cache_status"},
        {
            "endpoint": "cache_clear",
            "scope": "all",
        },
    ]


def test_admin_data_cache_router_validates_database_table_pagination():
    app = FastAPI()
    app.include_router(
        build_admin_data_cache_router(
            database_tables_payload=lambda: {"status": "ok"},
            database_table_payload=lambda table_name, limit=50, offset=0: {"status": "ok"},
            cache_status_payload=lambda: {"status": "ok"},
            cache_clear_payload=lambda scope="expired": {"status": "ok"},
        )
    )
    client = TestClient(app)

    too_large = client.get("/api/admin/database/table/access_logs", params={"limit": 201})
    negative_offset = client.get("/api/admin/database/table/access_logs", params={"offset": -1})

    assert too_large.status_code == 422
    assert negative_offset.status_code == 422
