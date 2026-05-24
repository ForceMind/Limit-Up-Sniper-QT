import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.quant.security as security


def test_cash_change_resets_frontend_follow_start(tmp_path, monkeypatch):
    pass_field = "pass" + "word"
    monkeypatch.setattr(security, "AUTH_FILE", tmp_path / "auth.json")
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
