from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "backend" / "data"
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "data" / "quant_data.sqlite3"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def num(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default


def integer(value: Any, default: int | None = None) -> int | None:
    value_num = num(value)
    if value_num is None:
        return default
    try:
        return int(value_num)
    except Exception:
        return default


def digest(*parts: Any) -> str:
    raw = "|".join(text(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def date_from_item(item: dict[str, Any], fallback: str = "") -> str:
    for key in ("date", "trade_date", "date_shanghai", "time", "time_str", "ts", "created_at", "updated_at", "analyzed_at"):
        value = text(item.get(key))
        if len(value) >= 10:
            return value[:10]
    return fallback


def iter_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                payload = {"message": line}
            if isinstance(payload, dict):
                yield line_no, payload
            else:
                yield line_no, {"payload": payload}


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        CREATE TABLE IF NOT EXISTS migration_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_dir TEXT NOT NULL,
            db_path TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS news_raw (
            id TEXT PRIMARY KEY,
            date TEXT,
            timestamp INTEGER,
            time_str TEXT,
            source TEXT,
            url TEXT,
            text TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_news_raw_date ON news_raw(date);
        CREATE INDEX IF NOT EXISTS idx_news_raw_source ON news_raw(source);

        CREATE TABLE IF NOT EXISTS news_analysis (
            record_key TEXT PRIMARY KEY,
            mode TEXT,
            analyzed_at TEXT,
            last_seen_at TEXT,
            provider TEXT,
            model TEXT,
            analysis_source TEXT,
            from_cache INTEGER,
            hit_count INTEGER,
            news_ids_json TEXT,
            result_summary TEXT,
            result_json TEXT,
            usage_json TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_news_analysis_analyzed_at ON news_analysis(analyzed_at);
        CREATE INDEX IF NOT EXISTS idx_news_analysis_model ON news_analysis(model);

        CREATE TABLE IF NOT EXISTS ai_cache (
            cache_key TEXT PRIMARY KEY,
            timestamp INTEGER,
            cached_at TEXT,
            data_json TEXT,
            meta_json TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_cache_timestamp ON ai_cache(timestamp);

        CREATE TABLE IF NOT EXISTS ai_usage_logs (
            usage_id TEXT PRIMARY KEY,
            ts INTEGER,
            time_shanghai TEXT,
            date_shanghai TEXT,
            provider TEXT,
            model TEXT,
            source TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cost_cny REAL,
            extra_json TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_usage_date ON ai_usage_logs(date_shanghai);
        CREATE INDEX IF NOT EXISTS idx_ai_usage_model ON ai_usage_logs(model);

        CREATE TABLE IF NOT EXISTS news_events (
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
        );
        CREATE INDEX IF NOT EXISTS idx_news_events_date ON news_events(date);
        CREATE INDEX IF NOT EXISTS idx_news_events_code_date ON news_events(code, date);
        CREATE INDEX IF NOT EXISTS idx_news_events_type ON news_events(event_type);

        CREATE TABLE IF NOT EXISTS market_daily_bars (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            close REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (code, date)
        );
        CREATE INDEX IF NOT EXISTS idx_market_daily_date ON market_daily_bars(date);

        CREATE TABLE IF NOT EXISTS market_minute_bars (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            open REAL,
            close REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (code, time)
        );
        CREATE INDEX IF NOT EXISTS idx_market_minute_code_date ON market_minute_bars(code, date);

        CREATE TABLE IF NOT EXISTS lhb_records (
            record_id TEXT PRIMARY KEY,
            trade_date TEXT,
            stock_code TEXT,
            stock_name TEXT,
            buyer_seat_name TEXT,
            buy_amount REAL,
            sell_amount REAL,
            hot_money TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_lhb_date_code ON lhb_records(trade_date, stock_code);

        CREATE TABLE IF NOT EXISTS market_snapshot_rows (
            row_id TEXT PRIMARY KEY,
            snapshot_source TEXT NOT NULL,
            date TEXT,
            time TEXT,
            code TEXT,
            name TEXT,
            current REAL,
            change_percent REAL,
            speed REAL,
            turnover REAL,
            amount REAL,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_market_snapshot_code_date ON market_snapshot_rows(code, date);

        CREATE TABLE IF NOT EXISTS market_pool_items (
            item_id TEXT PRIMARY KEY,
            pool_name TEXT NOT NULL,
            date TEXT,
            code TEXT,
            name TEXT,
            current REAL,
            change_percent REAL,
            concept TEXT,
            reason TEXT,
            strategy TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_market_pool_code_date ON market_pool_items(code, date);

        CREATE TABLE IF NOT EXISTS watchlist_items (
            item_id TEXT PRIMARY KEY,
            code TEXT,
            name TEXT,
            strategy_type TEXT,
            concept TEXT,
            initial_score REAL,
            news_summary TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_watchlist_code ON watchlist_items(code);

        CREATE TABLE IF NOT EXISTS paper_accounts (
            as_of TEXT PRIMARY KEY,
            cash REAL,
            positions_count INTEGER,
            trades_count INTEGER,
            model_weights_json TEXT,
            strategy_params_json TEXT,
            last_calibration_json TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper_positions (
            position_id TEXT PRIMARY KEY,
            as_of TEXT,
            code TEXT,
            name TEXT,
            qty REAL,
            entry_price REAL,
            entry_date TEXT,
            buy_score REAL,
            reason TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_paper_positions_as_of ON paper_positions(as_of);

        CREATE TABLE IF NOT EXISTS paper_trades (
            trade_id TEXT PRIMARY KEY,
            as_of TEXT,
            side TEXT,
            date TEXT,
            code TEXT,
            name TEXT,
            qty REAL,
            price REAL,
            score REAL,
            amount REAL,
            reason TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_paper_trades_date_code ON paper_trades(date, code);

        CREATE TABLE IF NOT EXISTS strategy_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT,
            started_at TEXT,
            finished_at TEXT,
            duration_ms REAL,
            generations INTEGER,
            population_size INTEGER,
            start_date TEXT,
            end_date TEXT,
            applied INTEGER,
            objective REAL,
            return_pct REAL,
            max_drawdown_pct REAL,
            win_rate REAL,
            closed_trades INTEGER,
            best_params_json TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_strategy_runs_finished ON strategy_runs(finished_at);

        CREATE TABLE IF NOT EXISTS strategy_model_metrics (
            metric_id TEXT PRIMARY KEY,
            run_id TEXT,
            generation INTEGER,
            best_objective REAL,
            best_return_pct REAL,
            best_drawdown_pct REAL,
            best_win_rate REAL,
            population INTEGER,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_strategy_metrics_run ON strategy_model_metrics(run_id);

        CREATE TABLE IF NOT EXISTS strategy_models (
            model_id TEXT PRIMARY KEY,
            run_id TEXT,
            generated_at TEXT,
            rank INTEGER,
            name TEXT,
            source TEXT,
            reusable INTEGER,
            objective REAL,
            return_pct REAL,
            max_drawdown_pct REAL,
            sharpe_ratio REAL,
            profit_factor REAL,
            win_rate REAL,
            closed_trades INTEGER,
            params_json TEXT NOT NULL DEFAULT '{}',
            backtest_json TEXT NOT NULL DEFAULT '{}',
            raw_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_strategy_models_run ON strategy_models(run_id, rank);
        CREATE INDEX IF NOT EXISTS idx_strategy_models_generated ON strategy_models(generated_at);

        CREATE TABLE IF NOT EXISTS strategy_model_records (
            record_id TEXT PRIMARY KEY,
            model_id TEXT,
            run_id TEXT,
            record_type TEXT,
            seq INTEGER,
            date TEXT,
            time TEXT,
            side TEXT,
            code TEXT,
            name TEXT,
            qty REAL,
            price REAL,
            pnl_pct REAL,
            raw_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_strategy_records_model ON strategy_model_records(model_id, record_type);
        CREATE INDEX IF NOT EXISTS idx_strategy_records_code_date ON strategy_model_records(code, date);

        CREATE TABLE IF NOT EXISTS access_logs (
            access_id TEXT PRIMARY KEY,
            ts TEXT,
            method TEXT,
            path TEXT,
            query TEXT,
            status_code INTEGER,
            duration_ms REAL,
            username TEXT,
            scope TEXT,
            ip TEXT,
            user_agent TEXT,
            referer TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_access_logs_ts ON access_logs(ts);
        CREATE INDEX IF NOT EXISTS idx_access_logs_username ON access_logs(username);
        CREATE INDEX IF NOT EXISTS idx_access_logs_ip ON access_logs(ip);

        CREATE TABLE IF NOT EXISTS job_logs (
            log_id TEXT PRIMARY KEY,
            source_file TEXT,
            line_no INTEGER,
            ts TEXT,
            level TEXT,
            job TEXT,
            stage TEXT,
            message TEXT,
            payload_json TEXT,
            raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_job_logs_ts ON job_logs(ts);
        CREATE INDEX IF NOT EXISTS idx_job_logs_job ON job_logs(job);
        """
    )


def upsert_news(conn: sqlite3.Connection, source_dir: Path) -> int:
    items = read_json(source_dir / "news_history.json", [])
    if not isinstance(items, list):
        return 0
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        news_id = text(item.get("id")) or digest(item.get("timestamp"), item.get("time_str"), item.get("text"))
        rows.append(
            (
                news_id,
                date_from_item(item),
                integer(item.get("timestamp")),
                text(item.get("time_str")),
                text(item.get("source")),
                text(item.get("url")),
                text(item.get("text")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO news_raw
        (id, date, timestamp, time_str, source, url, text, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_news_analysis(conn: sqlite3.Connection, source_dir: Path) -> int:
    items = read_json(source_dir / "news_analysis_records.json", [])
    if not isinstance(items, list):
        return 0
    rows = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        record_key = text(item.get("record_key")) or digest(idx, item.get("analyzed_at"), item.get("news_ids"))
        summary = item.get("result_summary")
        rows.append(
            (
                record_key,
                text(item.get("mode")),
                text(item.get("analyzed_at")),
                text(item.get("last_seen_at")),
                text(item.get("provider")),
                text(item.get("model")),
                text(item.get("analysis_source")),
                1 if item.get("from_cache") else 0,
                integer(item.get("hit_count"), 0),
                json_text(item.get("news_ids") or []),
                summary if isinstance(summary, str) else json_text(summary),
                json_text(item.get("result")),
                json_text(item.get("usage")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO news_analysis
        (record_key, mode, analyzed_at, last_seen_at, provider, model, analysis_source,
         from_cache, hit_count, news_ids_json, result_summary, result_json, usage_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_ai_cache(conn: sqlite3.Connection, source_dir: Path) -> int:
    payload = read_json(source_dir / "ai_cache.json", {})
    if not isinstance(payload, dict):
        return 0
    rows = []
    for cache_key, item in payload.items():
        if not isinstance(item, dict):
            item = {"data": item}
        rows.append(
            (
                text(cache_key),
                integer(item.get("timestamp")),
                text(item.get("cached_at") or item.get("time") or item.get("created_at")),
                json_text(item.get("data")),
                json_text(item.get("meta")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO ai_cache
        (cache_key, timestamp, cached_at, data_json, meta_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_ai_usage_logs(conn: sqlite3.Connection, source_dir: Path) -> int:
    rows = []
    for line_no, item in iter_jsonl(source_dir / "ai_usage_logs.jsonl"):
        usage_id = digest("ai_usage_logs.jsonl", line_no, item)
        rows.append(
            (
                usage_id,
                integer(item.get("ts")),
                text(item.get("time_shanghai")),
                text(item.get("date_shanghai")),
                text(item.get("provider")),
                text(item.get("model")),
                text(item.get("source")),
                integer(item.get("prompt_tokens")),
                integer(item.get("completion_tokens")),
                integer(item.get("total_tokens")),
                num(item.get("cost_cny")),
                json_text(item.get("extra")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO ai_usage_logs
        (usage_id, ts, time_shanghai, date_shanghai, provider, model, source,
         prompt_tokens, completion_tokens, total_tokens, cost_cny, extra_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_events(conn: sqlite3.Connection, source_dir: Path) -> int:
    payload = read_json(source_dir / "quant_events_cache.json", {})
    items = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return 0
    rows = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        event_id = text(item.get("event_id")) or digest(idx, item.get("date"), item.get("code"), item.get("text"))
        rows.append(
            (
                event_id,
                text(item.get("date")) or date_from_item(item),
                integer(item.get("timestamp")),
                text(item.get("source")),
                text(item.get("text")),
                text(item.get("code")),
                text(item.get("name")),
                text(item.get("industry")),
                text(item.get("event_type")),
                num(item.get("sentiment")),
                num(item.get("impact_score")),
                num(item.get("ai_score")),
                text(item.get("reason")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO news_events
        (event_id, date, timestamp, source, text, code, name, industry, event_type,
         sentiment, impact_score, ai_score, reason, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_daily_bars(conn: sqlite3.Connection, source_dir: Path) -> int:
    rows = []
    for path in sorted((source_dir / "kline_day_cache").glob("*.json")):
        code = path.stem
        items = read_json(path, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            date = text(item.get("date"))[:10]
            if not date:
                continue
            rows.append(
                (
                    code,
                    date,
                    num(item.get("open")),
                    num(item.get("close")),
                    num(item.get("high")),
                    num(item.get("low")),
                    num(item.get("volume")),
                    num(item.get("amount")),
                    json_text(item),
                )
            )
    conn.executemany(
        """
        INSERT OR REPLACE INTO market_daily_bars
        (code, date, open, close, high, low, volume, amount, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_minute_bars(conn: sqlite3.Connection, source_dir: Path) -> int:
    rows = []
    for path in sorted((source_dir / "kline_cache").glob("*.csv")):
        stem = path.stem
        if "_" not in stem:
            continue
        code, fallback_date = stem.split("_", 1)
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for item in reader:
                    if not isinstance(item, dict):
                        continue
                    time_value = text(item.get("time"))
                    date = time_value[:10] if len(time_value) >= 10 else fallback_date[:10]
                    if not time_value:
                        time_value = f"{date} 00:00:00"
                    rows.append(
                        (
                            code,
                            date,
                            time_value,
                            num(item.get("open")),
                            num(item.get("close")),
                            num(item.get("high")),
                            num(item.get("low")),
                            num(item.get("volume")),
                            num(item.get("amount")),
                            json_text(item),
                        )
                    )
        except Exception:
            continue
    conn.executemany(
        """
        INSERT OR REPLACE INTO market_minute_bars
        (code, date, time, open, close, high, low, volume, amount, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_lhb(conn: sqlite3.Connection, source_dir: Path) -> int:
    path = source_dir / "lhb_history.csv"
    if not path.exists():
        return 0
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for idx, item in enumerate(reader, start=1):
            record_id = digest("lhb", idx, item.get("trade_date"), item.get("stock_code"), item.get("buyer_seat_name"), item.get("buy_amount"))
            rows.append(
                (
                    record_id,
                    text(item.get("trade_date")),
                    text(item.get("stock_code")),
                    text(item.get("stock_name")),
                    text(item.get("buyer_seat_name")),
                    num(item.get("buy_amount")),
                    num(item.get("sell_amount")),
                    text(item.get("hot_money")),
                    json_text(item),
                )
            )
    conn.executemany(
        """
        INSERT OR REPLACE INTO lhb_records
        (record_id, trade_date, stock_code, stock_name, buyer_seat_name, buy_amount, sell_amount, hot_money, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_market_auxiliary(conn: sqlite3.Connection, source_dir: Path) -> dict[str, int]:
    counts = {"market_snapshot_rows": 0, "market_pool_items": 0, "watchlist_items": 0}
    all_market = read_json(source_dir / "biying_all_market_cache.json", {})
    snapshot_rows = all_market.get("rows") if isinstance(all_market, dict) else []
    rows = []
    if isinstance(snapshot_rows, list):
        for idx, item in enumerate(snapshot_rows):
            if not isinstance(item, dict):
                continue
            row_id = digest("biying_all_market_cache", idx, item.get("code"), item.get("time"), item)
            rows.append(
                (
                    row_id,
                    "biying_all_market_cache",
                    date_from_item(item),
                    text(item.get("time")),
                    text(item.get("code")),
                    text(item.get("name")),
                    num(item.get("current")),
                    num(item.get("change_percent")),
                    num(item.get("speed")),
                    num(item.get("turnover")),
                    num(item.get("amount")),
                    json_text(item),
                )
            )
    conn.executemany(
        """
        INSERT OR REPLACE INTO market_snapshot_rows
        (row_id, snapshot_source, date, time, code, name, current, change_percent, speed, turnover, amount, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    counts["market_snapshot_rows"] = len(rows)

    pools = read_json(source_dir / "market_pools.json", {})
    pool_rows = []
    if isinstance(pools, dict):
        for pool_name, items in pools.items():
            if not isinstance(items, list):
                continue
            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                item_id = digest("market_pool", pool_name, idx, item.get("code"), item.get("time"), item)
                pool_rows.append(
                    (
                        item_id,
                        text(pool_name),
                        date_from_item(item),
                        text(item.get("code")),
                        text(item.get("name")),
                        num(item.get("current")),
                        num(item.get("change_percent")),
                        text(item.get("concept")),
                        text(item.get("reason")),
                        text(item.get("strategy")),
                        json_text(item),
                    )
                )
    conn.executemany(
        """
        INSERT OR REPLACE INTO market_pool_items
        (item_id, pool_name, date, code, name, current, change_percent, concept, reason, strategy, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        pool_rows,
    )
    counts["market_pool_items"] = len(pool_rows)

    watchlist = read_json(source_dir / "watchlist.json", [])
    watch_rows = []
    if isinstance(watchlist, list):
        for idx, item in enumerate(watchlist):
            if not isinstance(item, dict):
                continue
            item_id = digest("watchlist", idx, item.get("code"), item.get("name"), item.get("news_summary"), item)
            watch_rows.append(
                (
                    item_id,
                    text(item.get("code")),
                    text(item.get("name")),
                    text(item.get("strategy_type")),
                    text(item.get("concept")),
                    num(item.get("initial_score")),
                    text(item.get("news_summary")),
                    json_text(item),
                )
            )
    conn.executemany(
        """
        INSERT OR REPLACE INTO watchlist_items
        (item_id, code, name, strategy_type, concept, initial_score, news_summary, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        watch_rows,
    )
    counts["watchlist_items"] = len(watch_rows)
    return counts


def upsert_quant_state(conn: sqlite3.Connection, source_dir: Path) -> dict[str, int]:
    state = read_json(source_dir / "quant_state.json", {})
    if not isinstance(state, dict) or not state:
        return {"paper_accounts": 0, "paper_positions": 0, "paper_trades": 0}
    as_of = text(state.get("as_of") or state.get("updated_at") or "unknown")
    positions = state.get("positions") if isinstance(state.get("positions"), list) else []
    trades = state.get("trades") if isinstance(state.get("trades"), list) else []
    conn.execute(
        """
        INSERT OR REPLACE INTO paper_accounts
        (as_of, cash, positions_count, trades_count, model_weights_json, strategy_params_json, last_calibration_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            as_of,
            num(state.get("cash")),
            len(positions),
            len(trades),
            json_text(state.get("model_weights")),
            json_text(state.get("strategy_params")),
            json_text(state.get("last_calibration")),
            json_text(state),
        ),
    )
    position_rows = []
    for idx, item in enumerate(positions):
        if not isinstance(item, dict):
            continue
        position_id = digest("position", as_of, idx, item)
        position_rows.append(
            (
                position_id,
                as_of,
                text(item.get("code")),
                text(item.get("name")),
                num(item.get("qty")),
                num(item.get("entry_price")),
                text(item.get("entry_date")),
                num(item.get("buy_score")),
                text(item.get("reason")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO paper_positions
        (position_id, as_of, code, name, qty, entry_price, entry_date, buy_score, reason, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        position_rows,
    )
    trade_rows = []
    for idx, item in enumerate(trades):
        if not isinstance(item, dict):
            continue
        trade_id = digest("trade", as_of, idx, item)
        trade_rows.append(
            (
                trade_id,
                as_of,
                text(item.get("side")),
                text(item.get("date")),
                text(item.get("code")),
                text(item.get("name")),
                num(item.get("qty")),
                num(item.get("price")),
                num(item.get("score")),
                num(item.get("amount")),
                text(item.get("reason")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO paper_trades
        (trade_id, as_of, side, date, code, name, qty, price, score, amount, reason, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        trade_rows,
    )
    return {"paper_accounts": 1, "paper_positions": len(position_rows), "paper_trades": len(trade_rows)}


def upsert_strategy_evolution(conn: sqlite3.Connection, source_dir: Path) -> dict[str, int]:
    state = read_json(source_dir / "strategy_evolution_state.json", {})
    if not isinstance(state, dict) or not state:
        return {
            "strategy_runs": 0,
            "strategy_model_metrics": 0,
            "strategy_models": 0,
            "strategy_model_records": 0,
        }
    best_model = state.get("best_model") if isinstance(state.get("best_model"), dict) else {}
    best = state.get("best") if isinstance(state.get("best"), dict) else {}
    best_source = best_model or best
    run_id = text(state.get("run_id")) or digest(
        "strategy_evolution",
        state.get("started_at"),
        state.get("finished_at") or state.get("updated_at"),
        state.get("start_date"),
        state.get("end_date"),
        state.get("mode"),
        best_source,
    )
    finished_at = text(state.get("finished_at") or state.get("updated_at"))
    conn.execute(
        """
        INSERT OR REPLACE INTO strategy_runs
        (run_id, status, started_at, finished_at, duration_ms, generations, population_size,
         start_date, end_date, applied, objective, return_pct, max_drawdown_pct, win_rate,
         closed_trades, best_params_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            text(state.get("status")),
            text(state.get("started_at")),
            finished_at,
            num(state.get("duration_ms")),
            integer(state.get("generations")),
            integer(state.get("population_size")),
            text(state.get("start_date")),
            text(state.get("end_date")),
            1 if state.get("applied") else 0,
            num(best_source.get("objective")),
            num(best_source.get("return_pct")),
            num(best_source.get("max_drawdown_pct")),
            num(best_source.get("win_rate")),
            integer(best_source.get("closed_trades")),
            json_text(best_source.get("params")),
            json_text(state),
        ),
    )
    history = state.get("history") if isinstance(state.get("history"), list) else []
    metric_rows = []
    for idx, item in enumerate(history):
        if not isinstance(item, dict):
            continue
        metric_id = digest("strategy_metric", run_id, idx, item)
        metric_rows.append(
            (
                metric_id,
                run_id,
                integer(item.get("generation")),
                num(item.get("best_objective")),
                num(item.get("best_return_pct")),
                num(item.get("best_drawdown_pct")),
                num(item.get("best_win_rate")),
                integer(item.get("population")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO strategy_model_metrics
        (metric_id, run_id, generation, best_objective, best_return_pct,
         best_drawdown_pct, best_win_rate, population, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        metric_rows,
    )
    models = state.get("models") if isinstance(state.get("models"), list) else []
    model_rows = []
    record_rows = []
    for rank, model in enumerate(models, start=1):
        if not isinstance(model, dict):
            continue
        model_id = text(model.get("id")) or digest("strategy_model", run_id, rank, model)[:24]
        model_rows.append(
            (
                model_id,
                run_id,
                text(model.get("generated_at") or finished_at),
                integer(model.get("rank"), rank),
                text(model.get("name")) or model_id,
                text(model.get("source")),
                1 if model.get("reusable", True) else 0,
                num(model.get("objective")),
                num(model.get("return_pct")),
                num(model.get("max_drawdown_pct")),
                num(model.get("sharpe_ratio")),
                num(model.get("profit_factor")),
                num(model.get("win_rate")),
                integer(model.get("closed_trades")),
                json_text(model.get("params") if isinstance(model.get("params"), dict) else {}),
                json_text(model.get("backtest") if isinstance(model.get("backtest"), dict) else {}),
                json_text(model),
            )
        )
        for record_type, records in (
            ("trade", model.get("trade_records")),
            ("delivery", model.get("delivery_records")),
            ("settlement", model.get("daily_settlements")),
        ):
            if not isinstance(records, list):
                continue
            for seq, record in enumerate(records, start=1):
                if not isinstance(record, dict):
                    continue
                record_id = digest("strategy_record", model_id, record_type, seq, record)
                qty_value = record.get("qty") if "qty" in record else record.get("quantity")
                price_value = record.get("price") if "price" in record else record.get("close")
                pnl_value = record.get("pnl_pct") if "pnl_pct" in record else record.get("return_pct")
                record_rows.append(
                    (
                        record_id,
                        model_id,
                        run_id,
                        record_type,
                        seq,
                        text(record.get("date") or record.get("trade_date") or record.get("sell_date") or record.get("buy_date")),
                        text(record.get("time") or record.get("ts") or record.get("created_at")),
                        text(record.get("side") or record.get("direction") or record.get("action")),
                        text(record.get("code")),
                        text(record.get("name")),
                        num(qty_value),
                        num(price_value),
                        num(pnl_value),
                        json_text(record),
                    )
                )
    conn.executemany(
        """
        INSERT OR REPLACE INTO strategy_models
        (model_id, run_id, generated_at, rank, name, source, reusable, objective, return_pct,
         max_drawdown_pct, sharpe_ratio, profit_factor, win_rate, closed_trades,
         params_json, backtest_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        model_rows,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO strategy_model_records
        (record_id, model_id, run_id, record_type, seq, date, time, side,
         code, name, qty, price, pnl_pct, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        record_rows,
    )
    return {
        "strategy_runs": 1,
        "strategy_model_metrics": len(metric_rows),
        "strategy_models": len(model_rows),
        "strategy_model_records": len(record_rows),
    }


def upsert_access_logs(conn: sqlite3.Connection, source_dir: Path) -> int:
    payload = read_json(source_dir / "access_logs.json", {})
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return 0
    rows = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        access_id = digest("access", idx, item.get("ts"), item.get("method"), item.get("path"), item.get("query"), item.get("ip"), item.get("status_code"))
        rows.append(
            (
                access_id,
                text(item.get("ts")),
                text(item.get("method")),
                text(item.get("path")),
                text(item.get("query")),
                integer(item.get("status_code")),
                num(item.get("duration_ms")),
                text(item.get("username")),
                text(item.get("scope")),
                text(item.get("ip")),
                text(item.get("user_agent")),
                text(item.get("referer")),
                json_text(item),
            )
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO access_logs
        (access_id, ts, method, path, query, status_code, duration_ms, username, scope, ip, user_agent, referer, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def upsert_job_logs(conn: sqlite3.Connection, source_dir: Path) -> int:
    rows = []
    for file_name in ("quant_runtime_logs.jsonl", "runtime_logs.jsonl"):
        for line_no, item in iter_jsonl(source_dir / file_name):
            ts = text(item.get("ts") or item.get("time") or item.get("created_at"))
            log_id = digest(file_name, line_no, item)
            rows.append(
                (
                    log_id,
                    file_name,
                    line_no,
                    ts,
                    text(item.get("level")),
                    text(item.get("job")),
                    text(item.get("stage")),
                    text(item.get("message")),
                    json_text(item.get("payload")),
                    json_text(item),
                )
            )
    conn.executemany(
        """
        INSERT OR REPLACE INTO job_logs
        (log_id, source_file, line_no, ts, level, job, stage, message, payload_json, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def migrate(source_dir: Path, db_path: Path) -> dict[str, int]:
    source_dir = source_dir.resolve()
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
        run_id = conn.execute(
            "INSERT INTO migration_runs (source_dir, db_path) VALUES (?, ?)",
            (str(source_dir), str(db_path)),
        ).lastrowid
        summary: dict[str, int] = {}
        with conn:
            summary["news_raw"] = upsert_news(conn, source_dir)
            summary["news_analysis"] = upsert_news_analysis(conn, source_dir)
            summary["ai_cache"] = upsert_ai_cache(conn, source_dir)
            summary["ai_usage_logs"] = upsert_ai_usage_logs(conn, source_dir)
            summary["news_events"] = upsert_events(conn, source_dir)
            summary["market_daily_bars"] = upsert_daily_bars(conn, source_dir)
            summary["market_minute_bars"] = upsert_minute_bars(conn, source_dir)
            summary["lhb_records"] = upsert_lhb(conn, source_dir)
            summary.update(upsert_market_auxiliary(conn, source_dir))
            summary.update(upsert_quant_state(conn, source_dir))
            summary.update(upsert_strategy_evolution(conn, source_dir))
            summary["access_logs"] = upsert_access_logs(conn, source_dir)
            summary["job_logs"] = upsert_job_logs(conn, source_dir)
            conn.execute(
                "UPDATE migration_runs SET finished_at = datetime('now'), summary_json = ? WHERE id = ?",
                (json_text(summary), run_id),
            )
        return {table: table_count(conn, table) for table in summary}
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate quant JSON/CSV runtime data into SQLite.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_DIR), help="Source backend/data directory.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Target SQLite database path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = Path(args.source)
    db_path = Path(args.db)
    if not source_dir.exists():
        raise SystemExit(f"source data dir not found: {source_dir}")
    summary = migrate(source_dir, db_path)
    print(f"SQLite database: {db_path.resolve()}")
    for table, count in sorted(summary.items()):
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
