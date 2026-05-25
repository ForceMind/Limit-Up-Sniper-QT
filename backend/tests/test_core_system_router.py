from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers.core_system import build_core_system_router


def test_core_system_router_preserves_auth_config_debug_and_status_contracts():
    calls = []

    def simple(endpoint):
        def payload():
            calls.append({"endpoint": endpoint})
            return {"status": "ok", "endpoint": endpoint}

        return payload

    def debug_status_payload(request: Request):
        calls.append({"endpoint": "debug_status", "path": request.url.path})
        return {"status": "ok", "endpoint": "debug_status"}

    def body_payload(endpoint):
        def payload(body):
            calls.append({"endpoint": endpoint, "body": body})
            return {"status": "ok", "endpoint": endpoint}

        return payload

    def request_body_payload(endpoint):
        def payload(request: Request, body):
            calls.append({"endpoint": endpoint, "path": request.url.path, "body": body})
            return {"status": "ok", "endpoint": endpoint}

        return payload

    app = FastAPI()
    app.include_router(
        build_core_system_router(
            version_payload=simple("version"),
            auth_status_payload=simple("auth_status"),
            debug_status_payload=debug_status_payload,
            debug_routes_payload=simple("debug_routes"),
            auth_setup_payload=body_payload("auth_setup"),
            auth_login_payload=request_body_payload("auth_login"),
            auth_register_payload=request_body_payload("auth_register"),
            config_status_payload=simple("config_status"),
            config_runtime_payload=simple("config_runtime"),
            config_update_payload=body_payload("config_update"),
            status_payload=simple("status"),
        )
    )
    client = TestClient(app)

    responses = [
        client.get("/api/version"),
        client.get("/api/auth/status"),
        client.get("/api/debug/status"),
        client.get("/api/debug/routes"),
        client.post("/api/auth/setup", json={"username": "admin"}),
        client.post("/api/auth/login", json={"username": "admin", "password": "pw"}),
        client.post("/api/auth/register", json={"username": "alice", "password": "pw"}),
        client.get("/api/config/status"),
        client.get("/api/config/runtime"),
        client.post("/api/config/runtime", json={"key": "value"}),
        client.get("/api/status"),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert calls == [
        {"endpoint": "version"},
        {"endpoint": "auth_status"},
        {"endpoint": "debug_status", "path": "/api/debug/status"},
        {"endpoint": "debug_routes"},
        {"endpoint": "auth_setup", "body": {"username": "admin"}},
        {
            "endpoint": "auth_login",
            "path": "/api/auth/login",
            "body": {"username": "admin", "password": "pw"},
        },
        {
            "endpoint": "auth_register",
            "path": "/api/auth/register",
            "body": {"username": "alice", "password": "pw"},
        },
        {"endpoint": "config_status"},
        {"endpoint": "config_runtime"},
        {"endpoint": "config_update", "body": {"key": "value"}},
        {"endpoint": "status"},
    ]
