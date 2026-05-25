import sqlite3
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.news_repository import (
    latest_news_time,
    latest_news_time_query_plan,
    lightweight_news_feed,
    news_events_query_plan,
    news_feed_query_plan,
)


def _create_news_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE news_raw (
              id TEXT PRIMARY KEY,
              date TEXT,
              timestamp INTEGER,
              time_str TEXT,
              source TEXT,
              url TEXT,
              text TEXT NOT NULL,
              raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE news_events (
              event_id TEXT PRIMARY KEY,
              date TEXT,
              timestamp INTEGER,
              source TEXT,
              text TEXT,
              code TEXT,
              name TEXT,
              industry TEXT,
              event_type TEXT,
              sentiment REAL,
              impact_score REAL,
              ai_score REAL,
              reason TEXT,
              raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX idx_news_raw_timestamp_date ON news_raw(timestamp DESC, date DESC)")
        conn.execute("CREATE INDEX idx_news_raw_date_timestamp ON news_raw(date, timestamp DESC, time_str DESC)")
        conn.execute("CREATE INDEX idx_news_events_date_impact ON news_events(date, impact_score DESC, timestamp DESC)")
        conn.executemany(
            "INSERT INTO news_raw (id, date, timestamp, time_str, source, url, text, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("n1", "2026-05-22", 100, "2026-05-22 09:30:00", "CLS", "", "旧新闻", "{}"),
                ("n2", "2026-05-23", 200, "2026-05-23 09:30:00", "CLS", "", "半导体 新闻", "{}"),
                ("n3", "2026-05-23", 300, "2026-05-23 10:30:00", "CLS", "", "AI 新闻", "{}"),
            ],
        )
        conn.execute(
            """
            INSERT INTO news_events
            (event_id, date, timestamp, source, text, code, name, industry, event_type,
             sentiment, impact_score, ai_score, reason, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("e1", "2026-05-23", 300, "CLS", "AI 新闻", "600000", "浦发银行", "AI", "政策催化", 0.4, 88.5, 7.0, "测试原因", "{}"),
        )
        conn.commit()
    finally:
        conn.close()


def test_lightweight_news_feed_uses_sqlite_only(tmp_path):
    db_path = tmp_path / "quant_data.sqlite3"
    _create_news_db(db_path)

    feed = lightweight_news_feed(as_of="2026-05-23", limit=2, db_path=db_path)

    assert feed is not None
    assert feed["source"] == "sqlite_light"
    assert feed["data_date"] == "2026-05-23"
    assert [item["id"] for item in feed["items"]] == ["n3", "n2"]
    assert feed["events"][0]["code"] == "600000"


def test_lightweight_news_feed_falls_back_to_latest_date(tmp_path):
    db_path = tmp_path / "quant_data.sqlite3"
    _create_news_db(db_path)

    feed = lightweight_news_feed(as_of="2026-05-24", limit=1, db_path=db_path)

    assert feed is not None
    assert feed["requested_date"] == "2026-05-24"
    assert feed["data_date"] == "2026-05-23"
    assert feed["has_requested_date_data"] is False


def test_latest_news_time_reads_top_sqlite_row(tmp_path):
    db_path = tmp_path / "quant_data.sqlite3"
    _create_news_db(db_path)

    assert latest_news_time(db_path=db_path) == "2026-05-23 10:30:00"


def test_news_hot_queries_use_covering_indexes_without_temp_sort(tmp_path):
    db_path = tmp_path / "quant_data.sqlite3"
    _create_news_db(db_path)

    plans = [
        " | ".join(latest_news_time_query_plan(db_path)),
        " | ".join(news_feed_query_plan(db_path, "2026-05-23")),
        " | ".join(news_events_query_plan(db_path, "2026-05-23")),
    ]

    assert "idx_news_raw_timestamp_date" in plans[0]
    assert "idx_news_raw_date_timestamp" in plans[1]
    assert "idx_news_events_date_impact" in plans[2]
    assert all("TEMP B-TREE" not in plan.upper() for plan in plans)
