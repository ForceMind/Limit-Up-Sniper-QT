from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.admin_access import build_admin_access_router


def test_admin_access_router_preserves_query_contract():
    calls = []

    def access_logs_payload(limit=220, offset=0, username=None, ip=None, path=None, status_code=None):
        calls.append(
            {
                "endpoint": "access_logs",
                "limit": limit,
                "offset": offset,
                "username": username,
                "ip": ip,
                "path": path,
                "status_code": status_code,
            }
        )
        return {"status": "ok", "items": []}

    def access_security_payload(limit=120):
        calls.append(
            {
                "endpoint": "access_security",
                "limit": limit,
            }
        )
        return {"status": "ok", "items": []}

    def block_payload(payload):
        calls.append(
            {
                "endpoint": "block",
                "payload": payload,
            }
        )
        return {"status": "ok", "blocked": True}

    def unblock_payload(payload):
        calls.append(
            {
                "endpoint": "unblock",
                "payload": payload,
            }
        )
        return {"status": "ok", "blocked": False}

    def block_all_payload(payload):
        calls.append(
            {
                "endpoint": "block_all",
                "payload": payload,
            }
        )
        return {"status": "ok", "blocked_count": 1}

    app = FastAPI()
    app.include_router(
        build_admin_access_router(
            access_logs_payload=access_logs_payload,
            access_security_payload=access_security_payload,
            block_payload=block_payload,
            unblock_payload=unblock_payload,
            block_all_payload=block_all_payload,
        )
    )
    client = TestClient(app)

    logs_response = client.get(
        "/api/admin/access_logs",
        params={
            "limit": 5,
            "offset": 2,
            "username": "demo",
            "ip": "127.0.0.1",
            "path": "/api/front/profile",
            "status_code": 404,
        },
    )
    security_response = client.get("/api/admin/access_security", params={"limit": 20})
    block_response = client.post("/api/admin/access_security/block", json={"ip": "1.2.3.4", "reason": "test"})
    unblock_response = client.post("/api/admin/access_security/unblock", json={"ip": "1.2.3.4"})
    block_all_response = client.post("/api/admin/access_security/block_all", json={"limit": 100})

    assert logs_response.status_code == 200
    assert security_response.status_code == 200
    assert block_response.status_code == 200
    assert unblock_response.status_code == 200
    assert block_all_response.status_code == 200
    assert calls == [
        {
            "endpoint": "access_logs",
            "limit": 5,
            "offset": 2,
            "username": "demo",
            "ip": "127.0.0.1",
            "path": "/api/front/profile",
            "status_code": 404,
        },
        {
            "endpoint": "access_security",
            "limit": 20,
        },
        {
            "endpoint": "block",
            "payload": {"ip": "1.2.3.4", "reason": "test"},
        },
        {
            "endpoint": "unblock",
            "payload": {"ip": "1.2.3.4"},
        },
        {
            "endpoint": "block_all",
            "payload": {"limit": 100},
        },
    ]


def test_admin_access_router_validates_limits_and_status_code():
    app = FastAPI()
    app.include_router(
        build_admin_access_router(
            access_logs_payload=lambda limit=220, offset=0, username=None, ip=None, path=None, status_code=None: {"status": "ok"},
            access_security_payload=lambda limit=120: {"status": "ok"},
            block_payload=lambda payload: {"status": "ok"},
            unblock_payload=lambda payload: {"status": "ok"},
            block_all_payload=lambda payload: {"status": "ok"},
        )
    )
    client = TestClient(app)

    bad_logs_limit = client.get("/api/admin/access_logs", params={"limit": 1001})
    bad_status_code = client.get("/api/admin/access_logs", params={"status_code": 99})
    bad_security_limit = client.get("/api/admin/access_security", params={"limit": 501})

    assert bad_logs_limit.status_code == 422
    assert bad_status_code.status_code == 422
    assert bad_security_limit.status_code == 422
