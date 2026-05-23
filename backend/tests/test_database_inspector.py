import sqlite3
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.database_inspector import database_overview, database_table_rows


def test_database_overview_lists_tables_and_state_files(tmp_path):
    db_path = tmp_path / "quant_data.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE strategy_models (id TEXT PRIMARY KEY, generated_at TEXT, name TEXT)")
        conn.execute(
            "INSERT INTO strategy_models (id, generated_at, name) VALUES (?, ?, ?)",
            ("m1", "2026-05-23T10:00:00", "测试模型"),
        )
        conn.commit()
    finally:
        conn.close()

    (tmp_path / "strategy_evolution_state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "strategy_evolution_state.archived-20260523131912.json").write_text("{}", encoding="utf-8")

    overview = database_overview(db_path=db_path, data_dir=tmp_path)

    assert overview["status"] == "ok"
    assert overview["table_count"] == 1
    assert overview["total_rows"] == 1
    assert overview["tables"][0]["name"] == "strategy_models"
    assert {item["role"] for item in overview["state_files"]} == {"current", "archived"}


def test_database_table_rows_are_read_only_and_paginated(tmp_path):
    db_path = tmp_path / "quant_data.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE access_logs (id INTEGER PRIMARY KEY, ts TEXT, message TEXT)")
        conn.executemany(
            "INSERT INTO access_logs (id, ts, message) VALUES (?, ?, ?)",
            [
                (1, "2026-05-23T10:00:00", "旧记录"),
                (2, "2026-05-23T11:00:00", "新记录"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    page = database_table_rows("access_logs", limit=1, offset=0, db_path=db_path)

    assert page["row_count"] == 2
    assert page["limit"] == 1
    assert len(page["rows"]) == 1
    assert page["rows"][0]["message"] == "新记录"


def test_database_table_rows_rejects_unknown_table(tmp_path):
    db_path = tmp_path / "quant_data.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE known_table (id INTEGER)")
        conn.commit()
    finally:
        conn.close()

    try:
        database_table_rows("unknown_table", db_path=db_path)
    except LookupError as exc:
        assert "unknown_table" in str(exc)
    else:
        raise AssertionError("unknown table should fail")
