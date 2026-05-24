import io
import json
import sqlite3
import sys
import tarfile
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import main as main_module
from app.quant import access_audit as access_audit_module
from app.quant import database_inspector as database_inspector_module
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
    monkeypatch.setenv("QT_DEBUG_API_ENABLED", "true")
    monkeypatch.setenv("QT_DEBUG_API_KEY", "qt_dbg_unit_admin")
    monkeypatch.setenv("QT_DEBUG_API_ALLOW_WRITE", "true")
    monkeypatch.setattr(security_module, "AUTH_FILE", auth_file)
    monkeypatch.setattr(access_audit_module, "ACCESS_LOG_FILE", data_dir / "access_logs.json")
    monkeypatch.setattr(main_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(main_module, "BACKUP_DIR", backup_dir)
    main_module.DATA_IMPORT_JOBS.clear()
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
    assert logs.status_code == 200
    assert logs.json()["status"] == "ok"


def test_admin_system_startup_and_restart_are_controlled(tmp_path, monkeypatch):
    client, headers, _data_dir, _backup_dir = _client(tmp_path, monkeypatch)
    captured = {}

    def fake_background(name, fn, payload=None, message=""):
        captured.update({"name": name, "payload": payload or {}, "message": message})
        return {"status": "running", "job": {"name": name, "status": "running"}, "background": True}

    monkeypatch.setattr(main_module.job_manager, "run_job_background", fake_background)
    monkeypatch.setenv("QUANT_ALLOW_API_RESTART", "0")

    startup = client.post(
        "/api/admin/system/startup?background=true&start_date=2026-03-01&end_date=2026-03-05&market_codes=12",
        headers=headers,
    )
    restart = client.post("/api/admin/restart", headers=headers)

    assert startup.status_code == 200
    assert startup.json()["status"] == "running"
    assert captured["name"] == "system_startup"
    assert captured["payload"]["start_date"] == "2026-03-01"
    assert captured["payload"]["market_codes"] == 12
    assert restart.status_code == 200
    assert restart.json()["status"] == "disabled"
