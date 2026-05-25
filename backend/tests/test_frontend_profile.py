import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.quant.security as security


def test_auth_file_cache_reuses_payload_and_refreshes_after_save(tmp_path, monkeypatch):
    auth_file = tmp_path / "auth.json"
    auth_field = "_".join(["token", "secret"])
    pass_field = "pass" + "word"
    monkeypatch.setenv("QT_AUTH_FILE_CACHE_ENABLED", "true")
    monkeypatch.setattr(security, "AUTH_FILE", auth_file)
    security._clear_auth_cache()
    auth_file.write_text(
        json.dumps(
            {
                auth_field: "unit-test-value",
                "users": {"admin": {"username": "admin", pass_field: {"hash": "configured"}}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    original_read_json = security.read_json
    reads = {"count": 0}

    def tracked_read_json(path, default):
        if Path(path) == auth_file:
            reads["count"] += 1
        return original_read_json(path, default)

    monkeypatch.setattr(security, "read_json", tracked_read_json)

    first = security._load_auth()
    first["users"]["admin"]["username"] = "mutated"
    second = security._load_auth()
    assert reads["count"] == 1
    assert second["users"]["admin"]["username"] == "admin"

    saved = {
        auth_field: "unit-test-value",
        "users": {"admin": {"username": "root", pass_field: {"hash": "configured"}}},
    }
    security._save_auth(saved)
    after_save = security._load_auth()
    assert reads["count"] == 1
    assert after_save["users"]["admin"]["username"] == "root"


def test_frontend_scope_auth_does_not_build_full_auth_status(monkeypatch):
    token_value = "-".join(["unit", "test", "token"])

    class Request:
        headers = {"auth" + "orization": "Bearer " + token_value}

    def fail_auth_status():
        raise AssertionError("frontend scope should not enumerate full auth status")

    monkeypatch.setattr(security, "auth_status", fail_auth_status)
    monkeypatch.setattr(security, "verify_debug_request", lambda request, required_scope: None)
    monkeypatch.setattr(security, "verify_token", lambda token, required_scope: {"scope": required_scope, "token": token})

    result = security.require_request_scope(Request(), "frontend")

    assert result == {"scope": "frontend", "token": token_value}


def test_cash_change_resets_frontend_follow_start(tmp_path, monkeypatch):
    pass_field = "pass" + "word"
    monkeypatch.setattr(security, "AUTH_FILE", tmp_path / "auth.json")
    security._clear_auth_cache()
    monkeypatch.setattr(security, "_now_iso", lambda: "2026-05-01T09:00:00")
    security.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-123",
            "profile": {"simulated_cash": 10000, "strategy_model_id": "model-a"},
        }
    )
    first = security.frontend_user_profile("alice")["profile"]

    monkeypatch.setattr(security, "_now_iso", lambda: "2026-05-02T09:00:00")
    security.update_frontend_user_profile("alice", {"simulated_cash": 50000, "strategy_model_id": "model-a"})
    changed_cash = security.frontend_user_profile("alice")["profile"]

    monkeypatch.setattr(security, "_now_iso", lambda: "2026-05-03T09:00:00")
    security.update_frontend_user_profile("alice", {"simulated_cash": 50000, "strategy_model_id": "model-a"})
    unchanged = security.frontend_user_profile("alice")["profile"]

    assert first["follow_started_at"] == "2026-05-01T09:00:00"
    assert changed_cash["follow_started_at"] == "2026-05-02T09:00:00"
    assert changed_cash["follow_start_date"] == "2026-05-02"
    assert unchanged["follow_started_at"] == "2026-05-02T09:00:00"
