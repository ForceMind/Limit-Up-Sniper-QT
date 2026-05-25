from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.admin_frontend_users import build_admin_frontend_users_router


def test_admin_frontend_users_router_preserves_contract():
    calls = []

    def list_users_payload():
        calls.append({"endpoint": "list"})
        return {"status": "ok", "users": []}

    def create_user_payload(request, payload):
        calls.append(
            {
                "endpoint": "create",
                "path": request.url.path,
                "payload": payload,
            }
        )
        return {"status": "ok", "user": payload}

    def update_user_payload(username, payload):
        calls.append(
            {
                "endpoint": "update",
                "username": username,
                "payload": payload,
            }
        )
        return {"status": "ok", "username": username}

    def reset_password_payload(username, payload):
        calls.append(
            {
                "endpoint": "password",
                "username": username,
                "payload": payload,
            }
        )
        return {"status": "ok", "username": username}

    def ban_user_payload(username, payload):
        calls.append(
            {
                "endpoint": "ban",
                "username": username,
                "payload": payload,
            }
        )
        return {"status": "ok", "username": username, "disabled": True}

    def unban_user_payload(username):
        calls.append(
            {
                "endpoint": "unban",
                "username": username,
            }
        )
        return {"status": "ok", "username": username, "disabled": False}

    def delete_user_payload(username):
        calls.append(
            {
                "endpoint": "delete",
                "username": username,
            }
        )
        return {"status": "ok", "username": username, "deleted": True}

    app = FastAPI()
    app.include_router(
        build_admin_frontend_users_router(
            list_users_payload=list_users_payload,
            create_user_payload=create_user_payload,
            update_user_payload=update_user_payload,
            reset_password_payload=reset_password_payload,
            ban_user_payload=ban_user_payload,
            unban_user_payload=unban_user_payload,
            delete_user_payload=delete_user_payload,
        )
    )
    client = TestClient(app)

    list_response = client.get("/api/admin/frontend_users")
    create_response = client.post(
        "/api/admin/frontend_users",
        json={"username": "alice", "password": "secret123", "simulated_cash": 20000},
    )
    update_response = client.patch(
        "/api/admin/frontend_users/alice",
        json={"simulated_cash": 30000, "strategy_model_id": "model-a"},
    )
    password_response = client.post(
        "/api/admin/frontend_users/alice/password",
        json={"password": "newpass123"},
    )
    ban_response = client.post("/api/admin/frontend_users/alice/ban", json={"reason": "unit-test"})
    unban_response = client.post("/api/admin/frontend_users/alice/unban")
    delete_response = client.delete("/api/admin/frontend_users/alice")

    assert list_response.status_code == 200
    assert create_response.status_code == 200
    assert update_response.status_code == 200
    assert password_response.status_code == 200
    assert ban_response.status_code == 200
    assert unban_response.status_code == 200
    assert delete_response.status_code == 200
    assert calls == [
        {"endpoint": "list"},
        {
            "endpoint": "create",
            "path": "/api/admin/frontend_users",
            "payload": {"username": "alice", "password": "secret123", "simulated_cash": 20000},
        },
        {
            "endpoint": "update",
            "username": "alice",
            "payload": {"simulated_cash": 30000, "strategy_model_id": "model-a"},
        },
        {
            "endpoint": "password",
            "username": "alice",
            "payload": {"password": "newpass123"},
        },
        {
            "endpoint": "ban",
            "username": "alice",
            "payload": {"reason": "unit-test"},
        },
        {
            "endpoint": "unban",
            "username": "alice",
        },
        {
            "endpoint": "delete",
            "username": "alice",
        },
    ]
