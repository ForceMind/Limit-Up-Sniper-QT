from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.quant.engine_utils import digits6, is_sample_code, read_json, safe_float, short_hash
from app.quant.quant_paths import DATA_DIR, LHB_HISTORY_FILE, QUANT_DB_FILE


SQLiteRows = Callable[..., List[Dict[str, Any]]]
StockName = Callable[[str], str]


def event_source_mtime_key(
    *,
    data_dir: Path = DATA_DIR,
    lhb_history_file: Path = LHB_HISTORY_FILE,
    db_file: Path = QUANT_DB_FILE,
) -> str:
    files = [
        data_dir / "news_history.json",
        data_dir / "news_analysis_records.json",
        data_dir / "biying_stock_list.json",
        lhb_history_file,
        db_file,
    ]
    parts = []
    for path in files:
        try:
            parts.append(f"{path.name}:{path.stat().st_mtime_ns}")
        except Exception:
            parts.append(f"{path.name}:0")
    return "|".join(parts)


def read_news_history(sqlite_rows: SQLiteRows, *, data_dir: Path = DATA_DIR) -> List[Dict[str, Any]]:
    payload = read_json(data_dir / "news_history.json", [])
    rows: List[Dict[str, Any]] = []
    seen = set()

    def add(item: Dict[str, Any]) -> None:
        if not isinstance(item, dict):
            return
        key = str(item.get("id") or item.get("url") or item.get("timestamp") or item.get("text") or "")
        if key and key in seen:
            return
        if key:
            seen.add(key)
        rows.append(item)

    if isinstance(payload, list):
        for item in payload:
            add(item)

    db_rows = sqlite_rows(
        """
        SELECT id, date, timestamp, time_str, source, url, text, raw_json
        FROM news_raw
        ORDER BY COALESCE(timestamp, 0) DESC, date DESC
        LIMIT 50000
        """
    )
    for row in db_rows:
        raw_payload: Dict[str, Any] = {}
        try:
            raw = json.loads(str(row.get("raw_json") or "{}"))
            raw_payload = raw if isinstance(raw, dict) else {}
        except Exception:
            raw_payload = {}
        item = {**raw_payload}
        item.update(
            {
                "id": str(row.get("id") or raw_payload.get("id") or short_hash(str(row.get("text") or ""))),
                "date": str(row.get("date") or raw_payload.get("date") or "")[:10],
                "timestamp": int(safe_float(row.get("timestamp"), safe_float(raw_payload.get("timestamp"), 0))),
                "time_str": str(row.get("time_str") or raw_payload.get("time_str") or ""),
                "source": str(row.get("source") or raw_payload.get("source") or ""),
                "url": str(row.get("url") or raw_payload.get("url") or ""),
                "text": str(row.get("text") or raw_payload.get("text") or ""),
            }
        )
        add(item)
    return rows


def read_analysis_records(*, data_dir: Path = DATA_DIR) -> List[Dict[str, Any]]:
    payload = read_json(data_dir / "news_analysis_records.json", [])
    return payload if isinstance(payload, list) else []


def read_lhb_records(
    sqlite_rows: SQLiteRows,
    stock_name: StockName,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10000,
    history_file: Path = LHB_HISTORY_FILE,
) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 10000), 200000))
    rows: List[Dict[str, Any]] = []
    seen = set()

    def add(row: Dict[str, Any]) -> None:
        if not isinstance(row, dict):
            return
        date = str(row.get("trade_date") or row.get("date") or "").strip()[:10]
        code = digits6(row.get("stock_code") or row.get("code"))
        if not date or not code or is_sample_code(code):
            return
        if start_date and date < start_date:
            return
        if end_date and date > end_date:
            return
        item = {
            "trade_date": date,
            "stock_code": code,
            "stock_name": str(row.get("stock_name") or row.get("name") or stock_name(code)).strip(),
            "buyer_seat_name": str(row.get("buyer_seat_name") or row.get("seat_name") or "").strip(),
            "buy_amount": safe_float(row.get("buy_amount"), 0),
            "sell_amount": safe_float(row.get("sell_amount"), 0),
            "hot_money": str(row.get("hot_money") or "").strip(),
        }
        key = (
            item["trade_date"],
            item["stock_code"],
            item["buyer_seat_name"],
            round(item["buy_amount"], 2),
            round(item["sell_amount"], 2),
        )
        if key in seen:
            return
        seen.add(key)
        rows.append(item)

    db_rows = sqlite_rows(
        """
        SELECT trade_date, stock_code, stock_name, buyer_seat_name, buy_amount, sell_amount, hot_money
        FROM lhb_records
        WHERE (? = '' OR trade_date >= ?) AND (? = '' OR trade_date <= ?)
        ORDER BY trade_date DESC, stock_code, buy_amount DESC
        LIMIT ?
        """,
        (start_date or "", start_date or "", end_date or "", end_date or "", limit),
    )
    for row in db_rows:
        add(row)

    if history_file.exists():
        for encoding in ("utf-8-sig", "gb18030"):
            before_count = len(rows)
            try:
                with history_file.open("r", encoding=encoding, newline="") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        add(row)
                break
            except UnicodeDecodeError:
                rows = rows[:before_count]
                continue
            except Exception:
                break
    rows.sort(key=lambda item: (item["trade_date"], item["stock_code"], item["buy_amount"]), reverse=True)
    return rows[:limit]


def lhb_summary_payload(
    sqlite_rows: SQLiteRows,
    load_lhb_records: Callable[..., List[Dict[str, Any]]],
    *,
    end_date: Optional[str] = None,
    recent_limit: int = 20,
    db_file: Path = QUANT_DB_FILE,
) -> Dict[str, Any]:
    end_date = str(end_date or "").strip()[:10]
    recent_limit = max(1, min(int(recent_limit or 20), 120))
    if db_file.exists():
        rows = sqlite_rows(
            """
            SELECT COUNT(*) AS rows_count,
                   COUNT(DISTINCT stock_code) AS stock_count,
                   MAX(trade_date) AS latest_date
            FROM lhb_records
            WHERE (? = '' OR trade_date <= ?)
            """,
            (end_date, end_date),
        )
        recent_rows = sqlite_rows(
            """
            SELECT trade_date, COUNT(*) AS rows_count
            FROM lhb_records
            WHERE (? = '' OR trade_date <= ?)
            GROUP BY trade_date
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (end_date, end_date, recent_limit),
        )
        row = rows[0] if rows else {}
        return {
            "rows": int(safe_float(row.get("rows_count"), 0)),
            "stock_count": int(safe_float(row.get("stock_count"), 0)),
            "latest_date": str(row.get("latest_date") or ""),
            "recent_dates": [str(item.get("trade_date") or "") for item in recent_rows if item.get("trade_date")],
        }
    records = load_lhb_records(end_date=end_date or None, limit=200000)
    dates = sorted({row.get("trade_date", "") for row in records if row.get("trade_date")}, reverse=True)
    return {
        "rows": len(records),
        "stock_count": len({row.get("stock_code") for row in records}),
        "latest_date": dates[0] if dates else "",
        "recent_dates": dates[:recent_limit],
    }
