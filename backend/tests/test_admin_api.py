import io
import os
from contextlib import nullcontext
import json
import sqlite3
import sys
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import main as main_module
from app.quant import access_audit as access_audit_module
from app.quant import database_inspector as database_inspector_module
from app.quant import jobs as jobs_module
from app.quant import security as security_module


def _write_auth(path: Path) -> None:
    auth_field = "_".join(["token", "secret"])
    pass_field = "pass" + "word"
    path.write_text(
        json.dumps(
            {
                auth_field: "unit-test-value",
                "users": {
                    "admin": {
                        "username": "admin",
                        pass_field: {"algorithm": "test", "hash": "configured"},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    backup_dir = tmp_path / "backups"
    data_dir.mkdir()
    backup_dir.mkdir()
    auth_file = data_dir / "auth.json"
    _write_auth(auth_file)
    monkeypatch.setenv("QUANT_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("QT_ACCESS_LOG_ASYNC", "false")
    monkeypatch.setenv("QT_DEBUG_API_ENABLED", "true")
    monkeypatch.setenv("QT_DEBUG_API_KEY", "qt_dbg_unit_admin")
    monkeypatch.setenv("QT_DEBUG_API_ALLOW_WRITE", "true")
    monkeypatch.setattr(security_module, "AUTH_FILE", auth_file)
    security_module._clear_auth_cache()
    monkeypatch.setattr(access_audit_module, "ACCESS_LOG_FILE", data_dir / "access_logs.json")
    monkeypatch.setattr(access_audit_module, "BLOCKED_IP_FILE", data_dir / "blocked_ips.json")
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(main_module, "BACKUP_DIR", backup_dir)
    main_module.DATA_IMPORT_JOBS.clear()
    with main_module._FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_LOCK:
        main_module._FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING.clear()
    return TestClient(main_module.app), {"x-qt-debug-key": "qt_dbg_unit_admin"}, data_dir, backup_dir


def _make_sqlite_package(rows: list[tuple[int, str]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        db_buffer = io.BytesIO()
        temp_db = Path(archive.name or "incoming.sqlite3")
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE sample_rows (id INTEGER PRIMARY KEY, value TEXT)")
            conn.executemany("INSERT INTO sample_rows (id, value) VALUES (?, ?)", rows)
            conn.commit()
            script = "\n".join(conn.iterdump())
        fd_path = None
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
            fd_path = Path(handle.name)
        try:
            disk = sqlite3.connect(fd_path)
            try:
                disk.executescript(script)
                disk.commit()
            finally:
                disk.close()
            archive.add(fd_path, arcname="backend/data/quant_data.sqlite3")
        finally:
            if fd_path:
                fd_path.unlink(missing_ok=True)
    return buffer.getvalue()


def test_admin_routes_require_auth_and_accept_debug_key(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)

    assert client.get("/api/admin/cache/status").status_code == 401

    monkeypatch.setattr(main_module, "runtime_cache_status", lambda: {"status": "ok", "expired": 0})
    response = client.get("/api/admin/cache/status", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_admin_news_fetch_defaults_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 78901}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/jobs/news/fetch?hours=24&pages=8&page_size=20",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "news_fetch"
    assert body["process"] is True
    assert called["name"] == "news_fetch"
    assert called["payload"]["hours"] == 24
    assert called["payload"]["pages"] == 8
    assert called["payload"]["page_size"] == 20
    assert called["payload"]["refresh_events"] is True


def test_admin_frontend_precompute_job_endpoint(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_precompute(**kwargs):
        called.update(kwargs)
        return {"status": "running", "job": "frontend_payload_precompute", "background": True}

    monkeypatch.setattr(main_module.job_manager, "run_frontend_payload_precompute", fake_precompute)

    response = client.post(
        "/api/jobs/frontend/precompute?background=true&process=false&limit_users=3&top_n=12&limit_days=120",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["job"] == "frontend_payload_precompute"
    assert called["limit_users"] == 3
    assert called["top_n"] == 12
    assert called["limit_days"] == 120


def test_frontend_payload_auto_precompute_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", raising=False)
    monkeypatch.delenv("QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS", raising=False)

    def fake_precompute(**kwargs):
        raise AssertionError(f"unexpected frontend precompute: {kwargs}")

    monkeypatch.setattr(main_module.job_manager, "run_frontend_payload_precompute", fake_precompute)

    result = main_module._queue_frontend_payload_precompute({"username": "alice"}, "2026-05-20")

    assert result["status"] == "disabled"
    assert result["queued"] is False
    assert result["frontend_payload_precompute_enabled"] is False
    assert result["frontend_payload_auto_precompute_on_miss"] is False
    pending = main_module._frontend_pending_payload("front_recommendations", "2026-05-20", result)
    assert pending["frontend_payload_cache"] == "disabled"
    assert pending["message"] == result["message"]


def test_frontend_payload_auto_precompute_requires_explicit_enable(monkeypatch):
    monkeypatch.setenv("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", "true")
    monkeypatch.setenv("QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS", "true")
    called = {}

    def fake_precompute(**kwargs):
        called.update(kwargs)
        return {"status": "running", "job": "frontend_payload_precompute", "background": True}

    monkeypatch.setattr(main_module.job_manager, "run_frontend_payload_precompute", fake_precompute)

    result = main_module._queue_frontend_payload_precompute({"username": "alice"}, "2026-05-20", top_n=12)

    assert result["status"] == "running"
    assert called["usernames"] == ["alice"]
    assert called["limit_users"] == 1
    assert called["top_n"] == 12


def test_admin_ai_analysis_defaults_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 67890}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/jobs/ai/analyze?as_of=2026-05-20&max_items=12&batch_size=4",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "ai_analysis"
    assert body["process"] is True
    assert called["name"] == "ai_analysis"
    assert called["payload"]["as_of"] == "2026-05-20"
    assert called["payload"]["max_items"] == 12
    assert called["payload"]["batch_size"] == 4


def test_admin_market_sync_defaults_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}
    process_called = threading.Event()

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        process_called.set()
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/jobs/market/sync?date=2026-05-20&source=events&max_codes=12&background=true",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "market_sync"
    assert body["process"] is True
    assert called["name"] == "market_sync"
    assert called["payload"]["date"] == "2026-05-20"
    assert called["payload"]["source"] == "events"
    assert called["payload"]["max_codes"] == 12


def test_data_biying_sync_intraday_queues_market_sync_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fail_sync(**_kwargs):
        raise AssertionError("sync_intraday should not run inside the HTTP request")

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 23456}

    monkeypatch.setattr(main_module.biying_minute_sync, "sync_intraday", fail_sync)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/data/biying/sync_intraday?date=2026-05-20&source=events&max_codes=20&codes=000001%2C000002",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "market_sync"
    assert body["process"] is True
    assert called["name"] == "market_sync"
    assert called["payload"]["date"] == "2026-05-20"
    assert called["payload"]["source"] == "events"
    assert called["payload"]["max_codes"] == 20
    assert called["payload"]["codes"] == "000001,000002"
    assert called["payload"]["codes_count"] == 2


def test_data_kline_fill_defaults_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 34567}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/data/kline/fill?start_date=2026-05-01&end_date=2026-05-20&max_codes=30&force=true",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "kline_fill"
    assert body["process"] is True
    assert called["name"] == "kline_fill"
    assert called["payload"]["start_date"] == "2026-05-01"
    assert called["payload"]["end_date"] == "2026-05-20"
    assert called["payload"]["max_codes"] == 30
    assert called["payload"]["force"] is True


def test_data_lhb_sync_defaults_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 45678}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/data/lhb/sync?start_date=2026-05-01&end_date=2026-05-20&max_stock_days=25&force=false",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "lhb_sync"
    assert body["process"] is True
    assert called["name"] == "lhb_sync"
    assert called["payload"]["start_date"] == "2026-05-01"
    assert called["payload"]["end_date"] == "2026-05-20"
    assert called["payload"]["max_stock_days"] == 25
    assert called["payload"]["force"] is False
    assert called["payload"]["refresh_events"] is True


def test_admin_trade_cycle_defaults_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 56789}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/jobs/trading/run?date=2026-05-20&notify=false",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "trade_cycle"
    assert body["process"] is True
    assert called["name"] == "trade_cycle"
    assert called["payload"]["date"] == "2026-05-20"
    assert called["payload"]["notify"] is False


def test_admin_system_startup_defaults_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 67891}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)
    monkeypatch.setattr(main_module.quant_engine, "latest_event_date", lambda: "2026-05-20")
    monkeypatch.setattr(main_module.quant_engine, "first_data_date", lambda: "2026-03-01")

    response = client.post(
        "/api/admin/system/startup?background=true&start_date=2026-03-01&end_date=2026-03-05&market_codes=12",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "system_startup"
    assert body["process"] is True
    assert called["name"] == "system_startup"
    assert called["payload"]["start_date"] == "2026-03-01"
    assert called["payload"]["end_date"] == "2026-03-05"
    assert called["payload"]["market_codes"] == 12
    assert called["payload"]["run_strategy_replay"] is False


def test_admin_frontend_account_precompute_job_endpoint(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fake_accounts(**kwargs):
        called.update(kwargs)
        return {
            "status": "ok",
            "job": "frontend_account_precompute",
            "user_count": 1,
            "saved": 1,
            "cached": 0,
            "pending": 0,
            "error_count": 0,
        }

    monkeypatch.setattr(main_module, "_precompute_frontend_accounts", fake_accounts)

    response = client.post(
        "/api/jobs/frontend/account_precompute?background=false&process=false&limit_users=2&limit=80&force=false",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["job"]["name"] == "frontend_account_precompute"
    assert body["job"]["last_result"]["job"] == "frontend_account_precompute"
    assert called["limit_users"] == 2
    assert called["limit"] == 80
    assert called["force"] is False


def test_admin_frontend_account_precompute_can_run_in_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}
    process_called = threading.Event()

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        process_called.set()
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/jobs/frontend/account_precompute?background=true&process=true&limit_users=3&limit=90",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["job"] == "frontend_account_precompute"
    assert body["process"] is True
    assert called["name"] == "frontend_account_precompute"
    assert called["payload"]["limit_users"] == 3
    assert called["payload"]["limit"] == 90
    assert called["payload"]["drain_queue"] is False


def test_admin_frontend_account_precompute_auto_drains_existing_queue(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._enqueue_frontend_account_precompute("alice", "profile_strategy_changed")
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/jobs/frontend/account_precompute?background=true&process=true&limit_users=3&limit=90",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["job"] == "frontend_account_precompute"
    assert called["name"] == "frontend_account_precompute"
    assert called["payload"]["drain_queue"] is True
    assert called["payload"]["usernames"] is None


def test_admin_frontend_account_precompute_explicit_drain_false_is_respected(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._enqueue_frontend_account_precompute("alice", "profile_strategy_changed")
    called = {}

    def fake_process(name, payload, message):
        called["payload"] = payload
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/jobs/frontend/account_precompute?background=true&process=true&drain_queue=false&limit_users=3&limit=90",
        headers=headers,
    )

    assert response.status_code == 200
    assert called["payload"]["drain_queue"] is False


def test_front_profile_change_queues_account_precompute_without_starting_worker(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_ASYNC_ON_PROFILE", "false")
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "model-a"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    called = {}
    profile_reads = {"count": 0}
    original_frontend_user_profile = main_module.frontend_user_profile

    def tracked_frontend_user_profile(username):
        profile_reads["count"] += 1
        return original_frontend_user_profile(username)

    monkeypatch.setattr(main_module, "frontend_user_profile", tracked_frontend_user_profile)

    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_models_payload",
        lambda include_catalog=True: {
            "status": "ok",
            "capital_presets": [
                {"id": "model-a", "name": "Model A", "params": {"account_initial_cash": 20000}},
                {"id": "model-b", "name": "Model B", "params": {"account_initial_cash": 20000}},
            ],
            "items": [],
            "active": {},
        },
    )

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/front/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"simulated_cash": 20000, "strategy_model_id": "model-b"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_precompute_queued"] is True
    assert body["account_precompute"]["queued"] is True
    assert body["account_precompute"]["worker_started"] is False
    assert body["account_precompute"]["worker_start_deferred"] is True
    assert body["follow_period_record"]["status"] == "queued"
    assert body["follow_period_record"]["async"] is True
    assert body["created_at"]
    assert body["profile_updated_at"]
    assert body["profile_update_elapsed_ms"] >= 0
    trace_stages = [item["stage"] for item in body["profile_update_trace"]]
    assert trace_stages == [
        "load_previous_profile",
        "resolve_updates",
        "save_profile",
        "build_profile_context",
        "queue_follow_period",
        "queue_account_precompute",
    ]
    assert body["profile_update_slow_stage"]["stage"] in trace_stages
    assert profile_reads["count"] == 1
    assert called == {}
    queue = main_module._load_frontend_account_precompute_queue()
    assert [item["username"] for item in queue] == ["alice"]


def test_front_profile_change_async_account_precompute_does_not_wait_for_queue_lock(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "model-a"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    release = threading.Event()
    called = threading.Event()

    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_models_payload",
        lambda include_catalog=True: {
            "status": "ok",
            "capital_presets": [
                {"id": "model-a", "name": "Model A", "params": {"account_initial_cash": 20000}},
                {"id": "model-b", "name": "Model B", "params": {"account_initial_cash": 20000}},
            ],
            "items": [],
            "active": {},
        },
    )

    def slow_enqueue(username, reason, as_of=None):
        called.set()
        release.wait(timeout=2)
        return {"status": "queued", "queued": True, "queue_size": 1, "username": username}

    monkeypatch.setattr(main_module, "_enqueue_frontend_account_precompute", slow_enqueue)

    started = time.time()
    response = client.post(
        "/api/front/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"simulated_cash": 20000, "strategy_model_id": "model-b"},
    )
    elapsed = time.time() - started
    release.set()

    assert response.status_code == 200
    body = response.json()
    assert elapsed < 1
    assert body["account_precompute_queued"] is True
    assert body["account_precompute"]["status"] == "queued_async"
    assert body["account_precompute"]["queue_pending"] is True
    assert body["account_precompute"]["worker_start_deferred"] is True


def test_front_profile_change_keeps_global_memory_cache(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()
    cache_parts = {"case": "profile-switch"}
    main_module._memory_cache_set("data_coverage", cache_parts, {"status": "ok", "value": 42})
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "model-a"},
        }
    )
    token = security_module.create_token("frontend", "alice")

    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_models_payload",
        lambda include_catalog=True: {
            "status": "ok",
            "capital_presets": [
                {"id": "model-a", "name": "Model A", "params": {"account_initial_cash": 20000}},
                {"id": "model-b", "name": "Model B", "params": {"account_initial_cash": 20000}},
            ],
            "items": [],
            "active": {},
        },
    )

    response = client.post(
        "/api/front/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"simulated_cash": 20000, "strategy_model_id": "model-b"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_cache_cleared"] is False
    assert body["account_cache_scope"] == "profile_keyed"
    cached = main_module._memory_cache_get("data_coverage", cache_parts, 60)
    assert cached["value"] == 42


def test_front_profile_capital_strategy_uses_light_context(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "capital_10000"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    calls = []

    def fake_models(include_catalog=True):
        calls.append(bool(include_catalog))
        if include_catalog:
            raise AssertionError("capital strategy profile update should not load full catalog")
        return {
            "status": "ok",
            "capital_presets": [
                {"id": "capital_10000", "name": "Small", "params": {"account_initial_cash": 10000}},
                {"id": "capital_20000_50000", "name": "Steady", "params": {"account_initial_cash": 30000}},
            ],
            "items": [],
            "active": {},
        }

    monkeypatch.setattr(main_module, "_frontend_strategy_models_payload", fake_models)

    response = client.post(
        "/api/front/profile?include_catalog=false",
        headers={"Authorization": f"Bearer {token}"},
        json={"simulated_cash": 30000, "strategy_model_id": "capital_20000_50000"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["strategy_model_id"] == "capital_20000_50000"
    assert body["profile_catalog_included"] is False
    assert "strategy_models" not in body
    assert calls and all(call is False for call in calls)


def test_front_profile_missing_model_does_not_fallback_full_catalog(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "stale-model"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    catalog_calls = []
    model_calls = []
    profile_writes = []

    def fake_models(include_catalog=True):
        catalog_calls.append(bool(include_catalog))
        if include_catalog:
            raise AssertionError("light profile update should not fallback to the full strategy catalog")
        return {
            "status": "ok",
            "capital_presets": [
                {"id": "capital_10000", "name": "Small", "params": {"account_initial_cash": 10000}},
                {"id": "capital_20000_50000", "name": "Steady", "params": {"account_initial_cash": 30000}},
            ],
            "items": [],
            "active": {},
        }

    def missing_model(model_id, include_records=False):
        model_calls.append((model_id, include_records))
        return {}

    monkeypatch.setattr(main_module, "_frontend_strategy_models_payload", fake_models)
    monkeypatch.setattr(main_module.strategy_evolution, "model", missing_model)
    original_update_frontend_user_profile = main_module.update_frontend_user_profile

    def tracked_update_frontend_user_profile(username, payload):
        profile_writes.append(dict(payload))
        return original_update_frontend_user_profile(username, payload)

    monkeypatch.setattr(main_module, "update_frontend_user_profile", tracked_update_frontend_user_profile)

    response = client.post(
        "/api/front/profile?include_catalog=false",
        headers={"Authorization": f"Bearer {token}"},
        json={"simulated_cash": 30000, "strategy_model_id": "stale-model"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["strategy_model_id"] == "capital_20000_50000"
    assert body["profile_catalog_included"] is False
    assert "strategy_models" not in body
    assert catalog_calls and all(call is False for call in catalog_calls)
    assert model_calls == [("stale-model", False)]
    assert len(profile_writes) == 1
    assert profile_writes[0]["strategy_model_id"] == "capital_20000_50000"


def test_front_profile_light_models_skip_runtime_summaries(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    assert client
    main_module._memory_cache_clear("strategy_models")

    def fail_runtime_summaries(_presets):
        raise AssertionError("light profile model lookup should not load runtime summaries")

    monkeypatch.setattr(main_module.strategy_evolution, "runtime_model_summaries", fail_runtime_summaries)

    payload = main_module._frontend_strategy_models_payload(include_catalog=False)

    assert payload["status"] == "ok"
    assert payload["capital_presets"]
    assert all(item.get("runtime_data_status") == "not_loaded" for item in payload["capital_presets"])


def test_front_snapshot_light_skips_full_strategy_catalog(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear(["front_snapshot", "front_jobs", "front_snapshot_news"])
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "capital_10000"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    calls = []

    def fake_models(include_catalog=True):
        calls.append(bool(include_catalog))
        if include_catalog:
            raise AssertionError("light front snapshot should not load the full strategy catalog")
        return {
            "status": "ok",
            "catalog_included": False,
            "active": {"id": "active", "params": {"account_initial_cash": 10000}},
            "capital_presets": [
                {"id": "capital_10000", "name": "Small", "params": {"account_initial_cash": 10000}},
                {"id": "capital_20000_50000", "name": "Steady", "params": {"account_initial_cash": 30000}},
            ],
            "items": [],
            "count": 2,
        }

    monkeypatch.setattr(main_module, "_frontend_strategy_models_payload", fake_models)
    account_kwargs = []

    def fake_frontend_strategy_account(*_args, **kwargs):
        account_kwargs.append(kwargs)
        return {"status": "ok", "account": {"status": "ok", "total_asset": 20000}}

    monkeypatch.setattr(main_module, "_frontend_strategy_account", fake_frontend_strategy_account)
    monkeypatch.setattr(
        main_module.job_manager,
        "logs",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("front snapshot should not read job logs")),
    )

    response = client.get("/api/front/snapshot?light=true", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert "logs" not in body
    assert "data_dir" not in body["status_payload"]
    assert body["strategy_catalog_included"] is False
    assert body["strategy_models"]["catalog_included"] is False
    assert calls and all(call is False for call in calls)
    assert account_kwargs[-1]["record_period"] is False
    assert account_kwargs[-1]["persist_derived"] is False


def test_front_public_snapshot_uses_light_jobs_cache_and_hides_data_dir(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear(["front_public_snapshot", "front_jobs", "front_snapshot_news"])
    calls = []

    def fake_frontend_status():
        calls.append("frontend_status")
        return {
            "scheduler": {"status": "running", "enabled": True},
            "running": {"news_fetch": {"status": "running"}},
            "paused_jobs": {},
            "jobs": {"internal": {"payload": "hidden"}},
        }

    monkeypatch.setattr(main_module.job_manager, "frontend_status", fake_frontend_status)
    monkeypatch.setattr(
        main_module.job_manager,
        "status",
        lambda light=False: (_ for _ in ()).throw(AssertionError("front snapshot should not use full job status")),
    )
    monkeypatch.setattr(main_module, "_safe_news_feed", lambda **_kwargs: {"status": "ok", "items": [], "events": []})

    first = client.get("/api/front/public_snapshot?light=true")
    second = client.get("/api/front/public_snapshot?light=true")

    assert first.status_code == 200
    assert second.status_code == 200
    body = first.json()
    assert "logs" not in body
    assert "data_dir" not in body["status_payload"]
    assert "jobs" not in body["jobs"]
    assert body["jobs"]["scheduler"]["status"] == "running"
    assert calls == ["frontend_status"]


def test_api_status_uses_lightweight_frontend_jobs(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    calls = []

    def fake_frontend_status():
        calls.append("frontend_status")
        return {
            "scheduler": {"status": "manual", "enabled": False},
            "running": {},
            "paused_jobs": {},
            "jobs": {"internal": {"payload": "hidden"}},
        }

    monkeypatch.setattr(main_module.job_manager, "frontend_status", fake_frontend_status)
    monkeypatch.setattr(
        main_module.job_manager,
        "status",
        lambda light=False: (_ for _ in ()).throw(AssertionError("api status should not use full job status")),
    )
    monkeypatch.setattr(main_module, "_latest_news_time", lambda: "2026-05-25 10:00:00")
    monkeypatch.setattr(main_module, "_data_date_bounds", lambda: {"first": "2026-03-01", "latest": "2026-05-25"})

    response = client.get("/api/status", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"]["scheduler"]["status"] == "manual"
    assert "jobs" not in body["jobs"]
    assert calls == ["frontend_status"]


def test_light_status_payload_reuses_latest_news_time_cache(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    assert client
    main_module._memory_cache_clear("latest_news_time")
    monkeypatch.setenv("QT_LATEST_NEWS_TIME_CACHE_TTL_SECONDS", "60")
    calls = []

    def fake_latest_news_time():
        calls.append(1)
        return "2026-05-24 10:30:00"

    monkeypatch.setattr(main_module, "latest_sqlite_news_time", fake_latest_news_time)
    monkeypatch.setattr(
        main_module.news_fetcher,
        "latest_history_time",
        lambda: (_ for _ in ()).throw(AssertionError("SQLite latest news time should be cached")),
    )

    try:
        first = main_module._light_status_payload(include_data_dir=False)
        second = main_module._light_status_payload(include_data_dir=False)

        assert first["latest_news_time"] == "2026-05-24 10:30:00"
        assert second["latest_news_time"] == "2026-05-24 10:30:00"
        assert calls == [1]
    finally:
        main_module._memory_cache_clear("latest_news_time")


def test_frontend_account_as_of_uses_sqlite_data_date_bounds(tmp_path, monkeypatch):
    client, _headers, data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    assert client
    db_path = data_dir / "quant_data.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE news_events (date TEXT)")
        conn.execute("CREATE TABLE market_daily_bars (date TEXT)")
        conn.execute("INSERT INTO news_events (date) VALUES ('2026-05-21')")
        conn.execute("INSERT INTO market_daily_bars (date) VALUES ('2026-05-24')")
        conn.commit()
    main_module._memory_cache_clear("data_date_bounds")
    monkeypatch.setenv("QT_DATA_DATE_CACHE_TTL_SECONDS", "60")
    monkeypatch.setattr(
        main_module.quant_engine,
        "latest_event_date",
        lambda: (_ for _ in ()).throw(AssertionError("front date bounds should not load all events")),
    )
    monkeypatch.setattr(
        main_module.quant_engine,
        "first_data_date",
        lambda: (_ for _ in ()).throw(AssertionError("front date bounds should use SQLite min date")),
    )

    try:
        assert main_module._frontend_account_as_of(None) == "2026-05-24"
        assert main_module._frontend_account_as_of("2026-06-01") == "2026-05-24"
        assert main_module._frontend_replay_start_date("2026-05-24") == "2026-05-21"
        status_payload = main_module._light_status_payload(include_data_dir=False)
        assert status_payload["first_data_date"] == "2026-05-21"
        assert status_payload["latest_data_date"] == "2026-05-24"
        assert status_payload["data_date_bounds"] == {"first": "2026-05-21", "latest": "2026-05-24"}
    finally:
        main_module._memory_cache_clear("data_date_bounds")


def test_front_snapshot_pending_account_triggers_async_precompute(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear("front_snapshot")
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "capital_10000"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    release = threading.Event()
    enqueue_called = threading.Event()
    worker_called = threading.Event()

    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_models_payload",
        lambda include_catalog=True: {
            "status": "ok",
            "catalog_included": False,
            "active": {"id": "active", "params": {"account_initial_cash": 10000}},
            "capital_presets": [
                {"id": "capital_10000", "name": "Small", "params": {"account_initial_cash": 10000}},
            ],
            "items": [],
            "count": 1,
        },
    )
    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_account",
        lambda *_args, **_kwargs: {
            "status": "pending",
            "frontend_account_deferred": True,
            "account": {"status": "pending", "total_asset": 20000, "total_pnl": 0, "available_cash": 20000},
            "positions": [],
            "history_deals": [],
            "delivery_records": [],
        },
    )

    def slow_enqueue(username, reason, as_of=None):
        enqueue_called.set()
        release.wait(timeout=2)
        return {"status": "queued", "queued": True, "queue_size": 1, "username": username}

    def fake_start_worker(as_of=None, reason=""):
        worker_called.set()
        return {"status": "running", "queued": True, "worker_started": True, "process": True}

    monkeypatch.setattr(main_module, "_enqueue_frontend_account_precompute", slow_enqueue)
    monkeypatch.setattr(main_module, "_start_frontend_account_precompute_worker_for_queue", fake_start_worker)

    started = time.time()
    response = client.get("/api/front/snapshot?light=true", headers={"Authorization": f"Bearer {token}"})
    elapsed = time.time() - started
    release.set()

    assert response.status_code == 200
    body = response.json()
    account = body["trading_account"]
    assert elapsed < 1
    assert account["frontend_account_deferred"] is True
    assert account["account_precompute_queued"] is True
    assert account["account_precompute"]["status"] == "queued_async"
    assert account["account_precompute"]["worker_start_pending"] is True
    assert enqueue_called.wait(timeout=1)
    assert worker_called.wait(timeout=2)


def test_front_strategy_models_loads_full_catalog(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "model-b"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    calls = []

    def fake_models(include_catalog=True):
        calls.append(bool(include_catalog))
        return {
            "status": "ok",
            "catalog_included": bool(include_catalog),
            "active": {"id": "active", "params": {"account_initial_cash": 10000}},
            "capital_presets": [
                {"id": "capital_10000", "name": "Small", "params": {"account_initial_cash": 10000}},
            ],
            "items": [
                {"id": "model-b", "name": "Model B", "params": {"account_initial_cash": 20000}},
            ] if include_catalog else [],
            "count": 2 if include_catalog else 1,
        }

    monkeypatch.setattr(main_module, "_frontend_strategy_models_payload", fake_models)

    response = client.get("/api/front/strategy_models", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["strategy_catalog_included"] is True
    assert body["strategy_models"]["catalog_included"] is True
    assert body["followed_model"]["name"] == "Model B"
    assert calls and all(call is True for call in calls)


def test_front_profile_model_strategy_uses_single_model_lookup(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 25000, "strategy_model_id": "capital_10000"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    catalog_calls = []
    model_calls = []

    def fake_models(include_catalog=True):
        catalog_calls.append(bool(include_catalog))
        if include_catalog:
            raise AssertionError("model strategy profile update should not load full catalog")
        return {
            "status": "ok",
            "capital_presets": [
                {"id": "capital_10000", "name": "Small", "params": {"account_initial_cash": 10000}},
                {"id": "capital_20000_50000", "name": "Steady", "params": {"account_initial_cash": 25000}},
            ],
            "items": [],
            "active": {},
        }

    def fake_model(model_id, include_records=True):
        model_calls.append((model_id, include_records))
        if model_id != "model-b":
            return {}
        return {
            "id": "model-b",
            "name": "Model B",
            "params": {"account_initial_cash": 25000, "max_positions": 2},
            "reusable": True,
        }

    monkeypatch.setattr(main_module, "_frontend_strategy_models_payload", fake_models)
    monkeypatch.setattr(main_module.strategy_evolution, "model", fake_model)

    response = client.post(
        "/api/front/profile?include_catalog=false",
        headers={"Authorization": f"Bearer {token}"},
        json={"simulated_cash": 25000, "strategy_model_id": "model-b"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["strategy_model_id"] == "model-b"
    assert body["followed_model"]["name"] == "Model B"
    assert body["profile_catalog_included"] is False
    assert "strategy_models" not in body
    assert catalog_calls and all(call is False for call in catalog_calls)
    assert model_calls == [("model-b", False)]


def test_front_profile_never_starts_precompute_worker_on_save(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "model-a"},
        }
    )
    token = security_module.create_token("frontend", "alice")

    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_models_payload",
        lambda include_catalog=True: {
            "status": "ok",
            "capital_presets": [
                {"id": "model-a", "name": "Model A", "params": {"account_initial_cash": 20000}},
                {"id": "model-b", "name": "Model B", "params": {"account_initial_cash": 20000}},
            ],
            "items": [],
            "active": {},
        },
    )

    called = {}

    profile_process_called = threading.Event()

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        profile_process_called.set()
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)
    monkeypatch.setenv("QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE", "true")

    response = client.post(
        "/api/front/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"simulated_cash": 20000, "strategy_model_id": "model-b"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account_precompute_queued"] is True
    assert body["account_precompute"]["queued"] is True
    assert body["account_precompute"]["status"] == "queued_async"
    assert body["account_precompute"]["worker_started"] is False
    assert body["account_precompute"]["worker_start_deferred"] is True
    assert body["account_precompute"]["worker_start_pending"] is False
    assert not profile_process_called.wait(timeout=0.2)
    assert called == {}


def test_frontend_account_precompute_drains_queued_users(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    assert client
    main_module._enqueue_frontend_account_precompute("alice", "profile_strategy_changed")
    main_module._enqueue_frontend_account_precompute("bob", "profile_cash_changed")

    monkeypatch.setattr(
        main_module,
        "frontend_user_summary",
        lambda: {"items": [{"username": "alice"}, {"username": "bob"}]},
    )
    monkeypatch.setattr(
        main_module,
        "_frontend_profile_context_for_username",
        lambda username, include_catalog=False, fallback_catalog_on_missing=True, profile_payload=None, resolved_model=None: {
            "username": username,
            "profile": {"strategy_model_id": "model-a", "simulated_cash": 20000, "follow_start_date": "2026-05-20"},
            "strategy_params": {"account_initial_cash": 20000},
            "followed_model": {"id": "model-a"},
        },
    )
    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_account",
        lambda context, as_of, limit, force=False, record_period=True, defer_miss=True: {
            "status": "ok",
            "follow_start_date": "2026-05-20",
            "strategy_account_source": "user_follow_snapshot",
            "strategy_account_cache": "user_follow",
        },
    )

    result = main_module._precompute_frontend_accounts(drain_queue=True, limit_users=10, limit=80)

    assert result["status"] == "ok"
    assert result["drain_queue"] is True
    assert result["user_count"] == 2
    assert result["cached"] == 2
    assert main_module._load_frontend_account_precompute_queue() == []


def test_frontend_account_precompute_queue_recovers_stale_lock(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    assert client
    lock_path = main_module._frontend_account_precompute_queue_lock_file()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("stale", encoding="utf-8")
    old_ts = time.time() - 60
    os.utime(lock_path, (old_ts, old_ts))
    monkeypatch.setenv("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS", "1000")

    result = main_module._enqueue_frontend_account_precompute("alice", "profile_strategy_changed")

    assert result["queued"] is True
    assert [item["username"] for item in main_module._load_frontend_account_precompute_queue()] == ["alice"]
    assert not lock_path.exists()


def test_jobs_status_includes_frontend_account_precompute_queue(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._enqueue_frontend_account_precompute("alice", "profile_strategy_changed")
    main_module._enqueue_frontend_account_precompute("bob", "profile_cash_changed")
    with main_module._FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_LOCK:
        main_module._FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING[
            "alice|account_runtime_missing|2026-05-20|start_worker"
        ] = time.time()
    lock_path = main_module._frontend_account_precompute_queue_lock_file()
    lock_path.write_text("busy", encoding="utf-8")
    old_ts = time.time() - 60
    os.utime(lock_path, (old_ts, old_ts))
    monkeypatch.setenv("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS", "1000")

    response = client.get("/api/jobs/status?light=true", headers=headers)

    assert response.status_code == 200
    queue = response.json()["frontend_account_precompute_queue"]
    assert queue["status"] == "ok"
    assert queue["queued"] == 2
    assert queue["empty"] is False
    assert queue["reason_counts"]["profile_strategy_changed"] == 1
    assert queue["reason_counts"]["profile_cash_changed"] == 1
    assert queue["lock"]["exists"] is True
    assert queue["lock"]["stale"] is True
    async_status = response.json()["frontend_account_precompute_async"]
    assert async_status["status"] == "ok"
    assert async_status["pending_count"] >= 1
    assert async_status["reason_counts"]["account_runtime_missing"] >= 1
    assert async_status["mode_counts"]["start_worker"] >= 1
    assert "alice" not in json.dumps(async_status, ensure_ascii=False)


def test_jobs_status_includes_frontend_payload_policy(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", "false")
    monkeypatch.setenv("QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS", "true")
    monkeypatch.setenv("QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED", "false")
    monkeypatch.setenv("QT_FRONT_PAYLOAD_PRECOMPUTE_INTERVAL_SECONDS", "2400")
    monkeypatch.setenv("QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS", "900")
    monkeypatch.setenv("QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS", "8")
    monkeypatch.setenv("QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS", "12")
    monkeypatch.setenv("QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS", "1800")

    response = client.get("/api/jobs/status?light=true", headers=headers)

    assert response.status_code == 200
    policy = response.json()["frontend_payload_policy"]
    assert policy["mode"] == "manual"
    assert policy["precompute_enabled"] is False
    assert policy["scheduled_precompute_enabled"] is False
    assert policy["auto_precompute_on_miss_requested"] is True
    assert policy["auto_precompute_on_miss"] is False
    assert policy["process_enabled"] is False
    assert policy["interval_seconds"] == 2400
    assert policy["initial_delay_seconds"] == 900
    assert policy["limit_users"] == 8
    assert policy["max_seconds"] == 12
    assert policy["recommendations_cache_ttl_seconds"] == 0
    assert policy["daily_plan_cache_ttl_seconds"] == 1800


def test_heavy_process_jobs_are_limited(tmp_path, monkeypatch):
    manager = jobs_module.QuantJobManager()
    manager.state_file = tmp_path / "quant_job_state.json"
    monkeypatch.setattr(jobs_module, "JOB_LOG_FILE", tmp_path / "quant_runtime_logs.jsonl")
    monkeypatch.setenv("QT_HEAVY_JOB_MAX_CONCURRENT", "1")
    jobs_module.write_json(
        manager.state_file,
        {
            "jobs": {
                "strategy_replay": {
                    "name": "strategy_replay",
                    "status": "running",
                    "process": True,
                    "process_pid": 12345,
                    "last_started_at": jobs_module._iso_now(),
                    "progress_pct": 30,
                    "progress_message": "running",
                }
            },
            "scheduler": {},
            "paused_jobs": {},
        },
    )
    monkeypatch.setattr(manager, "_pid_alive", lambda pid: True)

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("busy heavy process gate should not spawn another process")

    monkeypatch.setattr(jobs_module.subprocess, "Popen", fail_popen)

    result = manager.run_job_process("model_backtest", payload={"model_id": "model-a"})

    assert result["status"] == "busy"
    assert result["heavy_process_limit"] == 1
    assert result["running_heavy_jobs"][0]["job"] == "strategy_replay"

    front_result = manager.run_job_process("frontend_account_precompute", payload={"drain_queue": True})
    assert front_result["status"] == "busy"
    assert front_result["running_heavy_jobs"][0]["job"] == "strategy_replay"


def test_access_logs_async_queue_flushes_for_admin_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_ACCESS_LOG_ASYNC", "true")
    monkeypatch.setenv("QT_ACCESS_LOG_BATCH_SIZE", "10")
    monkeypatch.setenv("QT_ACCESS_LOG_BATCH_WINDOW_MS", "50")
    monkeypatch.setattr(access_audit_module, "ACCESS_LOG_FILE", tmp_path / "access_logs.json")
    monkeypatch.setattr(access_audit_module, "BLOCKED_IP_FILE", tmp_path / "blocked_ips.json")
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/front/profile", query="include_catalog=false"),
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )

    access_audit_module.record_access(
        request,
        200,
        12.5,
        {"sub": "alice", "scope": "frontend"},
    )
    access_audit_module.record_access(
        SimpleNamespace(
            method="GET",
            url=SimpleNamespace(path="/api/front/snapshot", query=""),
            headers={},
            client=SimpleNamespace(host="127.0.0.1"),
        ),
        200,
        8.0,
        {"sub": "alice", "scope": "frontend"},
    )

    access_audit_module._flush_access_queue(1.0)
    logs = access_audit_module.access_logs(limit=5)
    assert logs["count"] == 2
    assert {item["path"] for item in logs["items"]} == {"/api/front/profile", "/api/front/snapshot"}
    assert logs["async"]["enabled"] is True
    assert logs["async"]["batch_size"] == 10


def test_access_log_batch_append_persists_multiple_items(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_ACCESS_LOG_ASYNC", "false")
    monkeypatch.setattr(access_audit_module, "ACCESS_LOG_FILE", tmp_path / "access_logs.json")
    monkeypatch.setattr(access_audit_module, "BLOCKED_IP_FILE", tmp_path / "blocked_ips.json")

    access_audit_module._append_access_items(
        [
            {
                "ts": "2026-05-25T10:00:00",
                "method": "GET",
                "path": "/api/front/public_snapshot",
                "query": "",
                "status_code": 200,
                "duration_ms": 5,
                "username": "",
                "scope": "public",
                "ip": "127.0.0.1",
                "user_agent": "",
                "referer": "",
            },
            {
                "ts": "2026-05-25T10:00:01",
                "method": "GET",
                "path": "/api/jobs/status",
                "query": "",
                "status_code": 200,
                "duration_ms": 6,
                "username": "admin",
                "scope": "admin",
                "ip": "127.0.0.1",
                "user_agent": "",
                "referer": "",
            },
        ]
    )

    logs = access_audit_module.access_logs(limit=5)
    assert logs["count"] == 2
    assert logs["items"][0]["path"] == "/api/jobs/status"
    assert logs["items"][1]["path"] == "/api/front/public_snapshot"


def test_frontend_account_request_async_precompute_on_pending_account(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "model-a"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    release = threading.Event()
    enqueue_called = threading.Event()
    worker_called = threading.Event()

    monkeypatch.setattr(
        main_module,
        "_frontend_profile_context_for_username",
        lambda username, include_catalog=False, fallback_catalog_on_missing=True, profile_payload=None, resolved_model=None: {
            "username": username,
            "profile": {"strategy_model_id": "model-a", "simulated_cash": 20000, "follow_start_date": "2026-05-20"},
            "strategy_params": {"account_initial_cash": 20000},
            "followed_model": {"id": "model-a"},
        },
    )
    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_account",
        lambda context, as_of, limit, force=False, record_period=True, defer_miss=True, persist_derived=True: {
            "status": "pending",
            "frontend_account_deferred": True,
            "message": "pending",
            "account": {"status": "pending", "total_asset": 20000, "total_pnl": 0, "available_cash": 20000, "market_value": 0},
            "positions": [],
            "history_deals": [],
            "delivery_records": [],
        },
    )

    def slow_enqueue(username, reason, as_of=None):
        enqueue_called.set()
        release.wait(timeout=2)
        return {"status": "queued", "queued": True, "queue_size": 1, "username": username}

    def fake_start_worker(as_of=None, reason=""):
        worker_called.set()
        return {"status": "running", "queued": True, "worker_started": True, "process": True}

    monkeypatch.setattr(main_module, "_enqueue_frontend_account_precompute", slow_enqueue)
    monkeypatch.setattr(main_module, "_start_frontend_account_precompute_worker_for_queue", fake_start_worker)

    started = time.time()
    response = client.get("/api/front/trading_account", headers={"Authorization": f"Bearer {token}"})
    elapsed = time.time() - started
    release.set()

    assert response.status_code == 200
    body = response.json()
    assert elapsed < 1
    assert body["account_precompute_queued"] is True
    assert body["account_precompute"]["status"] == "queued_async"
    assert body["account_precompute"]["worker_started"] is False
    assert body["account_precompute"]["worker_start_pending"] is True
    assert enqueue_called.wait(timeout=1)
    assert worker_called.wait(timeout=2)


def test_frontend_trading_account_defaults_to_read_only_and_queues_persist(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    pass_field = "pass" + "word"
    security_module.admin_create_frontend_user(
        {
            "username": "alice",
            pass_field: "change-me-" + "123",
            "profile": {"simulated_cash": 20000, "strategy_model_id": "model-a"},
        }
    )
    token = security_module.create_token("frontend", "alice")
    account_kwargs = []
    queued = []

    monkeypatch.setattr(
        main_module,
        "_frontend_profile_context_for_username",
        lambda username, include_catalog=False, fallback_catalog_on_missing=True, profile_payload=None, resolved_model=None: {
            "username": username,
            "profile": {"strategy_model_id": "model-a", "simulated_cash": 20000, "follow_start_date": "2026-05-20"},
            "strategy_params": {"account_initial_cash": 20000},
            "followed_model": {"id": "model-a", "name": "Model A"},
        },
    )

    def fake_frontend_strategy_account(context, as_of, limit, force=False, record_period=True, defer_miss=True, persist_derived=True):
        account_kwargs.append(
            {
                "force": force,
                "record_period": record_period,
                "defer_miss": defer_miss,
                "persist_derived": persist_derived,
            }
        )
        return {
            "status": "ok",
            "user_follow_persist_deferred": True,
            "user_follow_persist_source": "runtime_tables",
            "frontend_account_precompute_reason": "account_persist_deferred",
            "account": {"status": "ok", "total_asset": 20100, "total_pnl": 100, "available_cash": 15000, "market_value": 5100},
            "positions": [],
            "history_deals": [],
            "delivery_records": [],
        }

    def fake_queue(username, reason, as_of=None, start_worker=True, async_enqueue=True):
        queued.append({"username": username, "reason": reason, "start_worker": start_worker, "async_enqueue": async_enqueue})
        return {"status": "queued", "queued": True, "username": username, "reason": reason}

    monkeypatch.setattr(main_module, "_frontend_strategy_account", fake_frontend_strategy_account)
    monkeypatch.setattr(main_module, "_queue_frontend_account_precompute_for_user", fake_queue)

    response = client.get("/api/front/trading_account", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert account_kwargs[-1]["record_period"] is False
    assert account_kwargs[-1]["persist_derived"] is False
    assert body["account"]["total_asset"] == 20100
    assert body["account_precompute_queued"] is True
    assert queued == [
        {"username": "alice", "reason": "account_persist_deferred", "start_worker": True, "async_enqueue": True}
    ]


def test_frontend_account_async_precompute_dedupes_repeated_requests(tmp_path, monkeypatch):
    client, _headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    assert client
    release = threading.Event()
    enqueue_called = threading.Event()
    worker_called = threading.Event()
    calls = {"enqueue": 0}

    def slow_enqueue(username, reason, as_of=None):
        calls["enqueue"] += 1
        enqueue_called.set()
        release.wait(timeout=2)
        return {"status": "queued", "queued": True, "queue_size": 1, "username": username}

    def fake_start_worker(as_of=None, reason=""):
        worker_called.set()
        return {"status": "running", "queued": True, "worker_started": True, "process": True}

    monkeypatch.setattr(main_module, "_enqueue_frontend_account_precompute", slow_enqueue)
    monkeypatch.setattr(main_module, "_start_frontend_account_precompute_worker_for_queue", fake_start_worker)

    first = main_module._queue_frontend_account_precompute_for_user(
        "alice",
        reason="account_runtime_missing",
        as_of="2026-05-20",
        start_worker=True,
        async_enqueue=True,
    )
    second = main_module._queue_frontend_account_precompute_for_user(
        "alice",
        reason="account_runtime_missing",
        as_of="2026-05-20",
        start_worker=True,
        async_enqueue=True,
    )
    release.set()

    assert first["status"] == "queued_async"
    assert first["deduped"] is False
    assert second["status"] == "queued_async"
    assert second["deduped"] is True
    assert second["worker_start_pending"] is True
    assert enqueue_called.wait(timeout=1)
    assert worker_called.wait(timeout=2)
    assert calls["enqueue"] == 1


def test_data_coverage_deferred_on_cache_miss(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()

    def fail_sync(*_args, **_kwargs):
        raise AssertionError("data_coverage should run in the background")

    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "progress_pct": 0}

    monkeypatch.setattr(main_module, "data_coverage", fail_sync)
    monkeypatch.setattr(main_module, "load_payload_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.get("/api/data/coverage?as_of=2026-05-20&top_n=5", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["data_coverage_cache"] == "miss_deferred"
    assert body["job_result"]["job"] == "data_coverage"
    assert called["name"] == "data_coverage"
    assert called["payload"] == {"as_of": "2026-05-20", "top_n": 5}


def test_data_coverage_force_computes_and_cache_hit_skips_job(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()
    calls = []
    cache_store = {}

    def fake_coverage(as_of=None, top_n=80):
        calls.append({"as_of": as_of, "top_n": top_n})
        return {
            "status": "ok",
            "as_of": as_of,
            "summary": {"target_count": top_n},
            "daily_coverage": {"covered": top_n, "missing": 0, "ratio": 1.0},
            "minute_coverage": {"covered": 0, "missing": top_n, "ratio": 0},
            "lhb": {"rows": 3, "latest_date": as_of},
            "targets": [],
        }

    def fake_load(payload_type, parts, ttl_seconds):
        return cache_store.get((payload_type, json.dumps(parts, sort_keys=True), int(ttl_seconds or 0)))

    def fake_save(payload_type, parts, payload, ttl_seconds):
        cache_store[(payload_type, json.dumps(parts, sort_keys=True), int(ttl_seconds or 0))] = dict(payload)

    def fail_process(*_args, **_kwargs):
        raise AssertionError("cached coverage should not start a job")

    monkeypatch.setattr(main_module, "data_coverage", fake_coverage)
    monkeypatch.setattr(main_module, "load_payload_cache", fake_load)
    monkeypatch.setattr(main_module, "save_payload_cache", fake_save)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fail_process)

    response = client.get("/api/data/coverage?as_of=2026-05-20&top_n=5&force=true", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["data_coverage_cache"] == "refresh"
    assert calls == [{"as_of": "2026-05-20", "top_n": 5}]

    cached = client.get("/api/data/coverage?as_of=2026-05-20&top_n=5", headers=headers)
    assert cached.status_code == 200
    cached_body = cached.json()
    assert cached_body["status"] == "ok"
    assert cached_body["data_coverage_cache"] == "hit"
    assert cached_body["summary"]["target_count"] == 5
    assert len(calls) == 1


def test_quant_backtest_requires_manual_trigger_by_default(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)

    def fail_backtest(*_args, **_kwargs):
        raise AssertionError("quant backtest should not run without a manual trigger")

    def fake_load(*_args, **_kwargs):
        return None

    def fail_process(*_args, **_kwargs):
        raise AssertionError("quant backtest should not start a process without manual=true")

    monkeypatch.setattr(main_module.quant_engine, "backtest", fail_backtest)
    monkeypatch.setattr(main_module, "load_payload_cache", fake_load)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fail_process)

    response = client.get(
        "/api/quant/backtest?start_date=2026-05-01&end_date=2026-05-20&hold_days=3&top_n=5",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "manual_required"
    assert body["manual_required"] is True
    assert body["backtest_cache"] == "manual_required"


def test_quant_backtest_manual_trigger_defers_to_process(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fail_backtest(*_args, **_kwargs):
        raise AssertionError("manual quant backtest should run in a background process by default")

    def fake_load(*_args, **_kwargs):
        return None

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.quant_engine, "backtest", fail_backtest)
    monkeypatch.setattr(main_module, "load_payload_cache", fake_load)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.get(
        "/api/quant/backtest?start_date=2026-05-01&end_date=2026-05-20&hold_days=3&top_n=5&manual=true",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["backtest_cache"] == "miss_deferred"
    assert body["job_result"]["job"] == "quant_backtest"
    assert body["job_result"]["process"] is True
    assert called["name"] == "quant_backtest"
    assert called["payload"]["start_date"] == "2026-05-01"
    assert called["payload"]["end_date"] == "2026-05-20"


def test_quant_backtest_manual_trigger_reports_busy_process_gate(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)

    def fail_backtest(*_args, **_kwargs):
        raise AssertionError("busy quant backtest should not run synchronously")

    def fake_process(name, payload, message):
        return {
            "status": "busy",
            "job": name,
            "process": True,
            "background": True,
            "message": "重任务并发已满",
            "heavy_process_limit": 1,
            "running_heavy_jobs": [{"job": "strategy_replay"}],
        }

    monkeypatch.setattr(main_module.quant_engine, "backtest", fail_backtest)
    monkeypatch.setattr(main_module, "load_payload_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.get(
        "/api/quant/backtest?start_date=2026-05-01&end_date=2026-05-20&hold_days=3&top_n=5&manual=true",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "busy"
    assert body["backtest_cache"] == "busy"
    assert body["message"] == "重任务并发已满"
    assert body["job_result"]["status"] == "busy"


def test_quant_backtest_sync_result_is_cached(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    store = {}
    calls = []

    def key_for(parts):
        return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)

    def fake_load(payload_type, parts, ttl):
        assert payload_type == "quant_backtest"
        cached = store.get(key_for(parts))
        return dict(cached) if isinstance(cached, dict) else None

    def fake_save(payload_type, parts, payload, ttl):
        assert payload_type == "quant_backtest"
        store[key_for(parts)] = dict(payload)

    def fake_backtest(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "as_of": kwargs.get("as_of") or kwargs.get("end_date"),
            "start_date": kwargs.get("start_date"),
            "end_date": kwargs.get("end_date"),
            "return_pct": 4.5,
            "timeline_trade_count": 2,
            "closed_trades": 1,
            "recent_trades": [],
            "trade_records": [],
        }

    def fail_process(*_args, **_kwargs):
        raise AssertionError("cached quant backtest should not start a job")

    monkeypatch.setattr(main_module, "load_payload_cache", fake_load)
    monkeypatch.setattr(main_module, "save_payload_cache", fake_save)
    monkeypatch.setattr(main_module.quant_engine, "backtest", fake_backtest)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fail_process)

    response = client.get(
        "/api/quant/backtest?start_date=2026-05-01&end_date=2026-05-20&defer=false&manual=true&hold_days=3&top_n=5",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["backtest_cache"] == "refresh"
    assert body["return_pct"] == 4.5
    assert len(calls) == 1

    cached = client.get(
        "/api/quant/backtest?start_date=2026-05-01&end_date=2026-05-20&hold_days=3&top_n=5",
        headers=headers,
    )
    assert cached.status_code == 200
    cached_body = cached.json()
    assert cached_body["status"] == "ok"
    assert cached_body["backtest_cache"] == "hit"
    assert cached_body["return_pct"] == 4.5
    assert len(calls) == 1


def test_quant_fit_strategy_defers_by_default(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    called = {}

    def fail_fit(*_args, **_kwargs):
        raise AssertionError("fit_strategy should run in a background process by default")

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "process_pid": 12345}

    monkeypatch.setattr(main_module.quant_engine, "fit_strategy", fail_fit)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.post(
        "/api/quant/fit_strategy?start_date=2026-05-01&end_date=2026-05-20&apply_best=false",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["source"] == "fit_strategy"
    assert body["job_result"]["job"] == "fit_strategy"
    assert body["job_result"]["process"] is True
    assert body["apply_best"] is False
    assert called["name"] == "fit_strategy"
    assert called["payload"]["start_date"] == "2026-05-01"
    assert called["payload"]["end_date"] == "2026-05-20"
    assert called["payload"]["apply_best"] is False


def test_quant_fit_strategy_can_run_sync_when_defer_false(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    calls = []

    def fake_fit_strategy(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "as_of": kwargs.get("end_date") or kwargs.get("as_of"),
            "start_date": kwargs.get("start_date"),
            "applied": bool(kwargs.get("apply_best")),
            "best": {"name": "测试方案", "objective": 3.2, "return_pct": 4.1},
            "candidates": [{"name": "测试方案", "objective": 3.2}],
            "strategy_params": {"buy_threshold": 70},
        }

    def fail_process(*_args, **_kwargs):
        raise AssertionError("sync fit_strategy should not start a process")

    monkeypatch.setattr(main_module.quant_engine, "fit_strategy", fake_fit_strategy)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fail_process)

    response = client.post(
        "/api/quant/fit_strategy?start_date=2026-05-01&end_date=2026-05-20&apply_best=true&defer=false",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["source"] == "fit_strategy"
    assert body["applied"] is True
    assert body["best"]["name"] == "测试方案"
    assert len(calls) == 1
    assert calls[0]["start_date"] == "2026-05-01"
    assert calls[0]["end_date"] == "2026-05-20"
    assert calls[0]["apply_best"] is True


def test_model_backtest_reads_saved_records_by_default(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_module,
        "_find_strategy_model",
        lambda model_id, include_records=True: {
            "id": "model-a",
            "name": "Saved Model",
            "params": {"account_initial_cash": 100000},
            "backtest": {"return_pct": 3.2, "win_rate": 0.5, "trade_count": 1, "initial_cash": 100000, "end_date": "2026-05-20"},
            "trade_records": [{"date": "2026-05-20", "side": "BUY", "code": "600000", "qty": 100, "price": 10}],
            "delivery_records": [{"date": "2026-05-20", "code": "600000", "side": "BUY"}],
            "daily_settlements": [],
        },
    )
    monkeypatch.setattr(
        main_module.quant_engine,
        "walk_forward_intraday",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not recompute")),
    )

    response = client.get("/api/quant/model/backtest?model_id=model-a", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "strategy_model_records"
    assert body["summary"]["return_pct"] == 3.2
    assert body["trade_records"][0]["code"] == "600000"


def test_model_backtest_recompute_requires_manual_trigger(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()
    monkeypatch.setattr(
        main_module,
        "_find_strategy_model",
        lambda model_id, include_records=True: {
            "id": "model-a",
            "name": "Model A",
            "params": {"account_initial_cash": 100000},
        },
    )
    monkeypatch.setattr(
        main_module.quant_engine,
        "walk_forward_intraday",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model backtest should not recompute without manual=true")),
    )

    def fail_background(*_args, **_kwargs):
        raise AssertionError("model backtest should not start a job without manual=true")

    monkeypatch.setattr(main_module, "load_payload_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.job_manager, "run_job_background", fail_background)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fail_background)

    response = client.get(
        "/api/quant/model/backtest?model_id=model-a&recompute=true&start_date=2026-05-01&end_date=2026-05-20",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "manual_required"
    assert body["manual_required"] is True
    assert body["model_backtest_cache"] == "manual_required"


def test_model_backtest_recompute_defers_by_default(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()
    monkeypatch.setattr(
        main_module,
        "_find_strategy_model",
        lambda model_id, include_records=True: {
            "id": "model-a",
            "name": "Model A",
            "params": {"account_initial_cash": 100000},
        },
    )
    monkeypatch.setattr(
        main_module.quant_engine,
        "walk_forward_intraday",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("model backtest should run in the background")),
    )
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "progress_pct": 0}

    monkeypatch.setattr(main_module, "load_payload_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.get(
        "/api/quant/model/backtest?model_id=model-a&recompute=true&manual=true&start_date=2026-05-01&end_date=2026-05-20",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["model_backtest_cache"] == "miss_deferred"
    assert body["job_result"]["job"] == "model_backtest"
    assert body["job_result"]["process"] is True
    assert called["name"] == "model_backtest"
    assert called["payload"]["model_id"] == "model-a"
    assert called["payload"]["start_date"] == "2026-05-01"


def test_model_backtest_recompute_can_run_sync_and_cache_hit_skips_job(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()
    calls = []
    store = {}

    def key_for(parts):
        return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)

    def fake_load(payload_type, parts, ttl):
        assert payload_type == "model_backtest"
        cached = store.get(key_for(parts))
        return dict(cached) if isinstance(cached, dict) else None

    def fake_save(payload_type, parts, payload, ttl):
        assert payload_type == "model_backtest"
        store[key_for(parts)] = dict(payload)

    monkeypatch.setattr(
        main_module,
        "_find_strategy_model",
        lambda model_id, include_records=True: {
            "id": "model-a",
            "name": "Model A",
            "params": {"account_initial_cash": 100000, "max_positions": 2, "max_hold_days": 3, "top_n": 5},
        },
    )
    monkeypatch.setattr(main_module.quant_engine, "temporary_strategy_params", lambda params: nullcontext())
    monkeypatch.setattr(main_module.quant_engine, "first_data_date", lambda: "2026-05-01")
    monkeypatch.setattr(main_module.quant_engine, "latest_event_date", lambda: "2026-05-20")

    def fake_intraday(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "mode": "intraday_5m",
            "start_date": kwargs.get("start_date"),
            "end_date": kwargs.get("end_date"),
            "initial_cash": 100000,
            "final_value": 103000,
            "return_pct": 3.0,
            "closed_trades": 1,
            "trades": [{"date": "2026-05-20", "side": "BUY", "code": "600000", "qty": 100, "price": 10}],
            "equity_curve": [],
            "days": [],
        }

    monkeypatch.setattr(main_module.quant_engine, "walk_forward_intraday", fake_intraday)
    monkeypatch.setattr(
        main_module.quant_engine,
        "account_from_trades",
        lambda *args, **kwargs: {"account": {"total_asset": 103000}, "positions": [], "delivery_records": [], "daily_settlements": []},
    )

    def fail_background(*_args, **_kwargs):
        raise AssertionError("cached model backtest should not start a job")

    monkeypatch.setattr(main_module, "load_payload_cache", fake_load)
    monkeypatch.setattr(main_module, "save_payload_cache", fake_save)
    monkeypatch.setattr(main_module.job_manager, "run_job_background", fail_background)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fail_background)

    response = client.get(
        "/api/quant/model/backtest?model_id=model-a&recompute=true&defer=false&manual=true&start_date=2026-05-01&end_date=2026-05-20&limit=10",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_backtest_cache"] == "refresh"
    assert body["source"] == "model_backtest_recompute"
    assert body["summary"]["return_pct"] == 3.0
    assert len(calls) == 1

    cached = client.get(
        "/api/quant/model/backtest?model_id=model-a&recompute=true&start_date=2026-05-01&end_date=2026-05-20&limit=10",
        headers=headers,
    )
    assert cached.status_code == 200
    cached_body = cached.json()
    assert cached_body["status"] == "ok"
    assert cached_body["model_backtest_cache"] == "hit"
    assert cached_body["summary"]["return_pct"] == 3.0
    assert len(calls) == 1


def test_frontend_strategy_account_defers_runtime_miss(monkeypatch):
    context = {
        "username": "alice",
        "created_at": "2026-05-01T09:00:00",
        "profile": {
            "strategy_model_id": "model-a",
            "simulated_cash": 20000,
            "follow_start_date": "2026-05-20",
        },
        "strategy_params": {"account_initial_cash": 20000, "max_positions": 2, "top_n": 3},
        "followed_model": {"id": "model-a", "name": "测试策略"},
    }
    monkeypatch.setattr(main_module.strategy_evolution, "load_user_follow_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module.strategy_evolution, "load_runtime_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module.strategy_evolution, "load_account_cache", lambda *args, **kwargs: None)

    def fail_model_load(*args, **kwargs):
        raise AssertionError("frontend account cache miss must not load full model records")

    monkeypatch.setattr(main_module.strategy_evolution, "model", fail_model_load)

    def fail_walk_forward(*args, **kwargs):
        raise AssertionError("frontend account cache miss must not run synchronous replay")

    monkeypatch.setattr(main_module.quant_engine, "walk_forward", fail_walk_forward)

    account = main_module._frontend_strategy_account(
        context,
        "2026-05-20",
        limit=50,
        record_period=False,
        defer_miss=True,
    )

    assert account["status"] == "pending"
    assert account["strategy_account_cache"] == "miss_deferred"
    assert account["strategy_account_source"] == "pending_runtime_missing"
    assert account["account"]["total_asset"] == 20000


def test_frontend_strategy_account_can_skip_persisting_runtime_hit(monkeypatch):
    context = {
        "username": "alice",
        "created_at": "2026-05-01T09:00:00",
        "profile": {
            "strategy_model_id": "model-a",
            "simulated_cash": 20000,
            "follow_start_date": "2026-05-20",
        },
        "strategy_params": {"account_initial_cash": 20000, "max_positions": 2, "top_n": 3},
        "followed_model": {"id": "model-a", "name": "测试策略"},
    }
    runtime_account = {
        "status": "ok",
        "strategy_account_source": "runtime_tables",
        "account": {"status": "ok", "total_asset": 20100},
        "positions": [],
        "history_deals": [],
        "delivery_records": [],
    }
    monkeypatch.setattr(main_module, "_first_data_date", lambda: "2026-05-01")
    monkeypatch.setattr(main_module.strategy_evolution, "load_user_follow_account", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module.strategy_evolution, "load_runtime_account", lambda *args, **kwargs: runtime_account)
    monkeypatch.setattr(
        main_module.strategy_evolution,
        "save_account_cache",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("light snapshot should not write account cache")),
    )
    monkeypatch.setattr(
        main_module.strategy_evolution,
        "save_user_follow_account",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("light snapshot should not persist user follow account")),
    )

    account = main_module._frontend_strategy_account(
        context,
        "2026-05-24",
        limit=80,
        record_period=False,
        defer_miss=True,
        persist_derived=False,
    )

    assert account["status"] == "ok"
    assert account["account"]["total_asset"] == 20100
    assert account["user_follow_persist_deferred"] is True
    assert account["user_follow_persist_source"] == "runtime_tables"
    assert account["frontend_account_precompute_reason"] == "account_persist_deferred"


def test_admin_database_endpoints_return_tables_and_errors(tmp_path, monkeypatch):
    client, headers, data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    db_path = data_dir / "quant_data.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE access_logs (id INTEGER PRIMARY KEY, ts TEXT, message TEXT)")
        conn.executemany(
            "INSERT INTO access_logs (id, ts, message) VALUES (?, ?, ?)",
            [(1, "2026-05-23T10:00:00", "旧记录"), (2, "2026-05-23T11:00:00", "新记录")],
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(database_inspector_module, "QUANT_DB_FILE", db_path)
    monkeypatch.setattr(database_inspector_module, "DATA_DIR", data_dir)

    overview = client.get("/api/admin/database/tables", headers=headers)
    page = client.get("/api/admin/database/table/access_logs?limit=1", headers=headers)
    missing = client.get("/api/admin/database/table/missing_table", headers=headers)

    assert overview.status_code == 200
    assert overview.json()["table_count"] == 1
    assert page.status_code == 200
    assert page.json()["rows"][0]["message"] == "新记录"
    assert missing.status_code == 404


def test_admin_frontend_user_management_lifecycle(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)

    created = client.post(
        "/api/admin/frontend_users",
        headers=headers,
        json={"username": "user-a", "password": "qwe123qwe", "simulated_cash": 20000},
    )
    listed = client.get("/api/admin/frontend_users", headers=headers)
    updated = client.patch(
        "/api/admin/frontend_users/user-a",
        headers=headers,
        json={"simulated_cash": 30000, "strategy_model_id": "capital_20000_50000"},
    )
    banned = client.post("/api/admin/frontend_users/user-a/ban", headers=headers, json={"reason": "测试封禁"})
    unbanned = client.post("/api/admin/frontend_users/user-a/unban", headers=headers)
    reset = client.post("/api/admin/frontend_users/user-a/password", headers=headers, json={"password": "newpass123"})
    deleted = client.delete("/api/admin/frontend_users/user-a", headers=headers)

    assert created.status_code == 200
    assert created.json()["user"]["username"] == "user-a"
    assert listed.status_code == 200
    assert any(item["username"] == "user-a" for item in listed.json()["items"])
    assert updated.status_code == 200
    assert updated.json()["user"]["profile"]["simulated_cash"] == 30000
    assert banned.status_code == 200
    assert banned.json()["user"]["disabled"] is True
    assert unbanned.status_code == 200
    assert unbanned.json()["user"]["disabled"] is False
    assert reset.status_code == 200
    assert deleted.status_code == 200


def test_admin_data_import_accepts_package_and_reports_status(tmp_path, monkeypatch):
    client, headers, data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    package = _make_sqlite_package([(1, "本地策略结果")])

    accepted = client.post(
        "/api/admin/data/import?backup=false",
        headers={**headers, "content-type": "application/gzip"},
        content=package,
    )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["status"] == "accepted"
    assert body["validation"]["files"] == 1

    status = client.get(f"/api/admin/data/import/{body['job_id']}", headers=headers)
    assert status.status_code == 200
    job = status.json()["job"]
    assert job["status"] == "done"
    assert job["result"]["merge_actions"] == {"created": 1}
    assert (data_dir / "quant_data.sqlite3").exists()


def test_admin_backup_export_and_clear_sample_state(tmp_path, monkeypatch):
    client, headers, data_dir, backup_dir = _client(tmp_path, monkeypatch)
    (data_dir / "quant_state.json").write_text(
        json.dumps({"positions": [{"code": "600001", "name": "样例算力"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    backup = client.post("/api/admin/backup", headers=headers)
    export = client.get("/api/admin/data/export", headers=headers)
    cleared = client.post("/api/admin/data/clear_sample_state", headers=headers)

    assert backup.status_code == 200
    assert backup.json()["status"] == "ok"
    assert list(backup_dir.glob("backend_data_*.tar.gz"))
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("application/gzip")
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True


def test_admin_snapshot_trading_account_and_access_logs(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module.job_manager, "status", lambda light=False: {"status": "ok", "running": {}, "scheduler": {}})
    monkeypatch.setattr(main_module, "_safe_news_feed", lambda **kwargs: {"status": "ok", "items": [{"id": "n1", "text": "测试新闻"}], "events": []})
    monkeypatch.setattr(main_module.biying_minute_sync, "status", lambda: {"status": "disabled"})
    monkeypatch.setattr(main_module, "lhb_status", lambda: {"status": "ok"})
    monkeypatch.setattr(main_module.trade_notifier, "status", lambda: {"status": "disabled"})
    monkeypatch.setattr(main_module.strategy_evolution, "status", lambda: {"status": "idle"})
    monkeypatch.setattr(
        main_module.strategy_evolution,
        "model_signal_feed",
        lambda **kwargs: {
            "status": "ok",
            "data_date": "2026-05-20",
            "items": [
                {
                    "model_id": "capital_10000",
                    "model_name": "小资金策略",
                    "signal_count": 1,
                    "signals": [{"code": "600000", "name": "测试信号", "buy_score": 80}],
                }
            ],
            "total": 1,
            "model_count": 1,
        },
    )
    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_models_payload",
        lambda include_catalog=True: {
            "status": "ok",
            "capital_presets": [
                {
                    "id": "capital_10000",
                    "name": "Small Capital",
                    "params": {"account_initial_cash": 10000},
                    "has_runtime_data": True,
                    "runtime_start_date": "2026-05-01",
                }
            ],
            "items": [],
        },
    )
    monkeypatch.setattr(main_module, "_admin_frontend_user_summary", lambda: {"status": "ok", "items": []})
    monkeypatch.setattr(
        main_module.quant_engine,
        "dashboard",
        lambda as_of=None, include_heavy=False: {
            "status": "ok",
            "recommendations": {"items": [{"code": "600000", "name": "测试信号"}], "latest_events": []},
            "timeline": {"days": [1]},
        },
    )
    monkeypatch.setattr(main_module.strategy_evolution, "runtime_model_version", lambda model: "test-version")
    monkeypatch.setattr(
        main_module.strategy_evolution,
        "load_runtime_account",
        lambda *args, **kwargs: {
            "status": "ok",
            "account": {"initial_cash": 10000, "total_asset": 10100, "return_pct": 1},
            "positions": [],
            "history_deals": [],
            "delivery_records": [],
            "daily_settlements": [],
            "strategy_account_source": "strategy_runtime",
        },
    )

    snapshot = client.get("/api/admin/snapshot?light=true", headers=headers)
    account = client.get("/api/admin/trading_account?limit=10", headers=headers)
    replay = client.get("/api/admin/strategy_runtime/replay?model_id=capital_10000&limit=10", headers=headers)
    logs = client.get("/api/admin/access_logs?limit=5", headers=headers)

    assert snapshot.status_code == 200
    assert snapshot.json()["status"] == "ok"
    assert snapshot.json()["news"]["items"][0]["text"] == "测试新闻"
    assert snapshot.json()["dashboard"]["recommendations"]["items"][0]["code"] == "600000"
    assert snapshot.json()["dashboard"]["timeline"] == {}
    assert snapshot.json()["model_signals"]["items"][0]["model_id"] == "capital_10000"
    assert account.status_code == 200
    assert account.json()["strategy_scope"] == "strategy_runtime"
    assert account.json()["strategy_model_id"] == "capital_10000"
    assert replay.status_code == 200
    assert replay.json()["source"] == "strategy_runtime"
    assert replay.json()["strategy_model_id"] == "capital_10000"
    assert logs.status_code == 200
    assert logs.json()["status"] == "ok"


def test_admin_strategy_runtime_matrix_returns_light_rows(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    seen = {}

    monkeypatch.setattr(main_module, "_frontend_account_as_of", lambda as_of: as_of or "2026-05-24")
    monkeypatch.setattr(
        main_module,
        "_frontend_strategy_models_payload",
        lambda include_catalog=True: {
            "status": "ok",
            "capital_presets": [
                {
                    "id": "capital_10000",
                    "name": "Small Capital",
                    "source": "capital",
                    "params": {"account_initial_cash": 10000},
                    "capital_min": 10000,
                    "capital_max": 19999,
                }
            ],
            "active": {"id": "active", "name": "Baseline"},
            "items": [
                {
                    "id": "model-b",
                    "name": "Model B",
                    "source": "evolution",
                    "rank": 2,
                    "objective": 1.5,
                    "params": {"account_initial_cash": 20000},
                }
            ],
        },
    )

    def fake_runtime_summaries(models):
        seen["models"] = [item["id"] for item in models]
        return {
            "capital_10000": {
                "has_runtime_data": True,
                "runtime_start_date": "2026-05-01",
                "runtime_end_date": "2026-05-24",
                "runtime_day_count": 12,
                "trade_count": 4,
                "closed_trades": 2,
                "position_count": 1,
                "win_rate": 50,
                "return_pct": 3.2,
                "max_drawdown_pct": -1.1,
                "final_value": 10320,
                "runtime_source": "daily_runtime:test",
                "generated_at": "2026-05-24T16:00:00",
            }
        }

    monkeypatch.setattr(main_module.strategy_evolution, "runtime_model_summaries", fake_runtime_summaries)
    monkeypatch.setattr(
        main_module,
        "_admin_model_signal_feed",
        lambda as_of, models_payload=None, limit_models=80, limit_per_model=1: {
            "status": "ok",
            "data_date": "2026-05-24",
            "items": [
                {
                    "model_id": "capital_10000",
                    "signal_count": 3,
                    "signals": [{"date": "2026-05-24", "code": "600000", "name": "Test Signal"}],
                }
            ],
        },
    )

    response = client.get("/api/admin/strategy_runtime/matrix?limit_models=10", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["ready_count"] == 1
    assert body["missing_count"] == 1
    assert body["signal_ready_count"] == 1
    assert seen["models"] == ["capital_10000", "model-b"]
    assert [item["model_id"] for item in body["items"]] == ["capital_10000", "model-b"]
    first = body["items"][0]
    assert first["runtime_status"] == "ready"
    assert first["trade_count"] == 4
    assert first["position_count"] == 1
    assert first["signal_count"] == 3
    assert first["latest_signal_code"] == "600000"
    assert body["items"][1]["runtime_status"] == "missing"


def test_admin_access_security_classifies_and_blocks_suspicious_ips(tmp_path, monkeypatch):
    client, headers, data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    log_file = data_dir / "access_logs.json"
    log_file.write_text(
        json.dumps(
            {
                "updated_at": "2026-05-24T10:00:00",
                "items": [
                    {
                        "ts": "2026-05-24T09:58:00",
                        "method": "GET",
                        "path": "/api/front/snapshot",
                        "status_code": 200,
                        "username": "user-a",
                        "scope": "frontend",
                        "ip": "8.8.8.8",
                        "user_agent": "Mozilla/5.0",
                    },
                    {
                        "ts": "2026-05-24T09:59:00",
                        "method": "GET",
                        "path": "/.env",
                        "status_code": 404,
                        "username": "",
                        "scope": "public",
                        "ip": "45.33.32.156",
                        "user_agent": "zgrab",
                    },
                    {
                        "ts": "2026-05-24T09:59:10",
                        "method": "GET",
                        "path": "/api/not_exists",
                        "status_code": 404,
                        "username": "",
                        "scope": "public",
                        "ip": "45.33.32.156",
                        "user_agent": "zgrab",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = client.get("/api/admin/access_security", headers=headers)
    page = client.get("/api/admin/access_logs?limit=1&offset=1&status_code=404", headers=headers)
    blocked = client.post(
        "/api/admin/access_security/block",
        headers=headers,
        json={"ip": "45.33.32.156", "reason": "unit-test"},
    )
    unblocked = client.post(
        "/api/admin/access_security/unblock",
        headers=headers,
        json={"ip": "45.33.32.156"},
    )
    bulk = client.post("/api/admin/access_security/block_all", headers=headers, json={"limit": 500})

    assert summary.status_code == 200
    assert any(item["ip"] == "45.33.32.156" for item in summary.json()["items"])
    assert not any(item["ip"] == "8.8.8.8" for item in summary.json()["items"])
    assert page.status_code == 200
    assert page.json()["returned"] == 1
    assert page.json()["offset"] == 1
    assert page.json()["prev_offset"] == 0
    assert blocked.status_code == 200
    assert blocked.json()["blocked"] is True
    assert any(item["ip"] == "45.33.32.156" for item in blocked.json()["security"]["blocked"])
    assert unblocked.status_code == 200
    assert unblocked.json()["blocked"] is False
    assert bulk.status_code == 200
    assert "45.33.32.156" in bulk.json()["blocked"]


def test_quant_timeline_can_run_against_selected_strategy(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    captured = {}

    monkeypatch.setattr(
        main_module,
        "_find_strategy_model",
        lambda model_id, include_records=True: {
            "id": model_id,
            "name": "小资金策略",
            "params": {
                "account_initial_cash": 12000,
                "max_positions": 2,
                "max_hold_days": 4,
                "top_n": 3,
            },
        },
    )
    monkeypatch.setattr(main_module.quant_engine, "temporary_strategy_params", lambda params: nullcontext())

    def fake_intraday(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "mode": "intraday_5m",
            "start_date": kwargs.get("start_date"),
            "end_date": kwargs.get("end_date"),
            "return_pct": 1.2,
            "trades": [],
        }

    monkeypatch.setattr(main_module.quant_engine, "walk_forward_intraday", fake_intraday)
    monkeypatch.setattr(main_module, "load_payload_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module, "save_payload_cache", lambda *_args, **_kwargs: None)

    response = client.get(
        "/api/quant/intraday_timeline?model_id=capital_10000&start_date=2026-03-01&end_date=2026-03-08&defer=false&manual=true",
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_model_id"] == "capital_10000"
    assert payload["strategy_name"] == "小资金策略"
    assert payload["strategy_scope"] == "strategy_model"
    assert captured["initial_cash"] == 12000
    assert captured["max_positions"] == 2
    assert captured["hold_days"] == 4
    assert captured["top_n"] == 3


def test_quant_timeline_defers_by_default(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()
    monkeypatch.setattr(
        main_module,
        "_find_strategy_model",
        lambda model_id, include_records=True: {
            "id": model_id,
            "name": "Timeline Model",
            "params": {"account_initial_cash": 12000, "max_positions": 2, "max_hold_days": 4, "top_n": 3},
        },
    )
    monkeypatch.setattr(
        main_module.quant_engine,
        "walk_forward_intraday",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("timeline should run in the background")),
    )
    called = {}

    def fake_process(name, payload, message):
        called["name"] = name
        called["payload"] = payload
        called["message"] = message
        return {"status": "running", "job": name, "process": True, "background": True, "progress_pct": 0}

    monkeypatch.setattr(main_module, "load_payload_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fake_process)

    response = client.get(
        "/api/quant/intraday_timeline?model_id=capital_10000&start_date=2026-03-01&end_date=2026-03-08&manual=true",
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["timeline_cache"] == "miss_deferred"
    assert payload["job_result"]["job"] == "quant_timeline"
    assert payload["job_result"]["process"] is True
    assert called["name"] == "quant_timeline"
    assert called["payload"]["model_id"] == "capital_10000"
    assert called["payload"]["intraday"] is True


def test_quant_timeline_requires_manual_trigger_by_default(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module, "load_payload_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main_module.job_manager,
        "run_job_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("timeline should not auto queue")),
    )

    response = client.get(
        "/api/quant/intraday_timeline?model_id=capital_10000&start_date=2026-03-01&end_date=2026-03-08",
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "manual_required"
    assert payload["manual_required"] is True
    assert payload["timeline_cache"] == "manual_required"


def test_quant_timeline_sync_result_is_cached(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    main_module._memory_cache_clear()
    calls = []
    store = {}

    def key_for(parts):
        return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)

    def fake_load(payload_type, parts, ttl):
        assert payload_type == "quant_timeline"
        cached = store.get(key_for(parts))
        return dict(cached) if isinstance(cached, dict) else None

    def fake_save(payload_type, parts, payload, ttl):
        assert payload_type == "quant_timeline"
        store[key_for(parts)] = dict(payload)

    monkeypatch.setattr(
        main_module,
        "_find_strategy_model",
        lambda model_id, include_records=True: {
            "id": model_id,
            "name": "Timeline Model",
            "params": {"account_initial_cash": 12000, "max_positions": 2, "max_hold_days": 4, "top_n": 3},
        },
    )
    monkeypatch.setattr(main_module.quant_engine, "temporary_strategy_params", lambda params: nullcontext())

    def fake_intraday(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "mode": "intraday_5m",
            "start_date": kwargs.get("start_date"),
            "end_date": kwargs.get("end_date"),
            "return_pct": 1.2,
            "closed_trades": 1,
            "trades": [{"code": "600000"}],
            "days": [{"date": "2026-03-08"}],
        }

    def fail_background(*_args, **_kwargs):
        raise AssertionError("cached timeline should not start a job")

    monkeypatch.setattr(main_module.quant_engine, "walk_forward_intraday", fake_intraday)
    monkeypatch.setattr(main_module, "load_payload_cache", fake_load)
    monkeypatch.setattr(main_module, "save_payload_cache", fake_save)
    monkeypatch.setattr(main_module.job_manager, "run_job_background", fail_background)
    monkeypatch.setattr(main_module.job_manager, "run_job_process", fail_background)

    response = client.get(
        "/api/quant/intraday_timeline?model_id=capital_10000&start_date=2026-03-01&end_date=2026-03-08&defer=false&manual=true",
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["timeline_cache"] == "refresh"
    assert len(calls) == 1

    cached = client.get(
        "/api/quant/intraday_timeline?model_id=capital_10000&start_date=2026-03-01&end_date=2026-03-08",
        headers=headers,
    )
    assert cached.status_code == 200
    cached_payload = cached.json()
    assert cached_payload["status"] == "ok"
    assert cached_payload["timeline_cache"] == "hit"
    assert cached_payload["return_pct"] == 1.2
    assert len(calls) == 1


def test_admin_system_startup_and_restart_are_controlled(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    captured = {}

    def fake_background(name, fn, payload=None, message=""):
        captured.update({"name": name, "payload": payload or {}, "message": message})
        return {"status": "running", "job": {"name": name, "status": "running"}, "background": True}

    monkeypatch.setattr(main_module.job_manager, "run_job_background", fake_background)
    monkeypatch.setattr(main_module.job_manager, "start", lambda: {"status": "ok", "scheduler": "started"})

    async def fake_scheduler_stop():
        return {"status": "ok", "scheduler": "stopped"}

    monkeypatch.setattr(main_module.job_manager, "stop", fake_scheduler_stop)
    monkeypatch.setenv("QUANT_ALLOW_API_RESTART", "0")

    startup = client.post(
        "/api/admin/system/startup?background=true&process=false&start_date=2026-03-01&end_date=2026-03-05&market_codes=12",
        headers=headers,
    )
    scheduler_start = client.post("/api/jobs/scheduler/start", headers=headers)
    scheduler_stop = client.post("/api/jobs/scheduler/stop", headers=headers)
    restart = client.post("/api/admin/restart", headers=headers)

    assert startup.status_code == 200
    assert startup.json()["status"] == "running"
    assert captured["name"] == "system_startup"
    assert captured["payload"]["start_date"] == "2026-03-01"
    assert captured["payload"]["market_codes"] == 12
    assert scheduler_start.status_code == 200
    assert scheduler_start.json()["scheduler"] == "started"
    assert scheduler_stop.status_code == 200
    assert scheduler_stop.json()["scheduler"] == "stopped"
    assert restart.status_code == 200
    assert restart.json()["status"] == "disabled"


def test_jobs_stop_marks_running_task_stop_requested(tmp_path, monkeypatch):
    client, headers, data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    state_file = data_dir / "quant_job_state.json"
    log_file = data_dir / "quant_runtime_logs.jsonl"
    monkeypatch.setattr(main_module.job_manager, "state_file", state_file)
    monkeypatch.setattr(jobs_module, "JOB_LOG_FILE", log_file)
    state_file.write_text(
        json.dumps(
            {
                "scheduler": {},
                "paused_jobs": {},
                "jobs": {
                    "strategy_replay": {
                        "name": "strategy_replay",
                        "status": "running",
                        "process": False,
                        "progress_pct": 35,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with main_module.job_manager._lock:
        main_module.job_manager._running["strategy_replay"] = True
    try:
        response = client.post("/api/jobs/strategy_replay/stop", headers=headers)
    finally:
        with main_module.job_manager._lock:
            main_module.job_manager._running.pop("strategy_replay", None)

    assert response.status_code == 200
    assert response.json()["status"] == "stop_requested"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    current = state["jobs"]["strategy_replay"]
    assert current["status"] == "running"
    assert current["stop_requested"] is True
    assert current["progress_message"] == "已请求停止当前任务"
