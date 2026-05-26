from __future__ import annotations

import csv
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.quant.engine_utils import digits6, parse_time, read_json, safe_float
from app.quant.event_models import NewsEvent
from app.quant.quant_paths import KLINE_DAY_DIR, KLINE_MIN_DIR, QUANT_DB_FILE


SQLiteRows = Callable[..., List[Dict[str, Any]]]
DailyKlineLoader = Callable[[str], List[Dict[str, Any]]]
IntradayLoader = Callable[[str, str], List[Dict[str, Any]]]
EventsLoader = Callable[[], Iterable[NewsEvent]]


def read_daily_kline(
    sqlite_rows: SQLiteRows,
    code: str,
    *,
    read_legacy_json_cache: bool = False,
    kline_day_dir: Path = KLINE_DAY_DIR,
) -> List[Dict[str, Any]]:
    code = digits6(code)
    if not code:
        return []
    clean_rows: List[Dict[str, Any]] = []

    def add_row(row: Dict[str, Any]) -> None:
        if not isinstance(row, dict):
            return
        date = str(row.get("date") or "").strip()[:10]
        if not date:
            return
        close = safe_float(row.get("close"), 0)
        open_price = safe_float(row.get("open"), close)
        if close <= 0:
            return
        clean_rows.append(
            {
                "date": date,
                "open": open_price if open_price > 0 else close,
                "close": close,
                "high": safe_float(row.get("high"), close),
                "low": safe_float(row.get("low"), close),
                "volume": safe_float(row.get("volume"), 0),
                "amount": safe_float(row.get("amount"), 0),
            }
        )

    for row in sqlite_rows(
        """
        SELECT date, open, close, high, low, volume, amount
        FROM market_daily_bars
        WHERE code = ?
        ORDER BY date
        """,
        (code,),
    ):
        add_row(row)
    if not clean_rows or read_legacy_json_cache:
        payload = read_json(kline_day_dir / f"{code}.json", [])
        rows = payload if isinstance(payload, list) else []
        for row in rows:
            add_row(row)
    by_date = {row["date"]: row for row in clean_rows}
    return [by_date[key] for key in sorted(by_date.keys())]


def read_intraday_bars(
    sqlite_rows: SQLiteRows,
    code: str,
    date: str,
    *,
    kline_min_dir: Path = KLINE_MIN_DIR,
) -> List[Dict[str, Any]]:
    code = digits6(code)
    date = str(date or "").strip()[:10]
    if not code or not date:
        return []
    path = kline_min_dir / f"{code}_{date}.csv"
    bars: List[Dict[str, Any]] = []

    def add_bar(row: Dict[str, Any]) -> None:
        dt = parse_time(row.get("time"))
        if not dt:
            return
        open_price = safe_float(row.get("open"), 0)
        close_price = safe_float(row.get("close"), 0)
        if open_price <= 0 or close_price <= 0:
            return
        bars.append(
            {
                "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "date": dt.strftime("%Y-%m-%d"),
                "dt": dt,
                "open": open_price,
                "close": close_price,
                "high": safe_float(row.get("high"), max(open_price, close_price)),
                "low": safe_float(row.get("low"), min(open_price, close_price)),
                "volume": safe_float(row.get("volume"), 0),
                "amount": safe_float(row.get("amount"), 0),
            }
        )

    if path.exists():
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    add_bar(row)
        except Exception:
            bars = []

    for row in sqlite_rows(
        """
        SELECT time, date, open, close, high, low, volume, amount
        FROM market_minute_bars
        WHERE code = ? AND date = ?
        ORDER BY time
        """,
        (code, date),
    ):
        add_bar(row)

    by_time = {row["time"]: row for row in bars}
    return [by_time[key] for key in sorted(by_time.keys())]


def read_available_intraday_dates(
    sqlite_rows: SQLiteRows,
    codes: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    kline_min_dir: Path = KLINE_MIN_DIR,
) -> Dict[str, set]:
    out: Dict[str, set] = {}
    code_set = {digits6(code) for code in (codes or []) if digits6(code)}
    if kline_min_dir.exists():
        for path in kline_min_dir.glob("*.csv"):
            match = re.match(r"^(\d{6})_(\d{4}-\d{2}-\d{2})\.csv$", path.name)
            if not match:
                continue
            if code_set and match.group(1) not in code_set:
                continue
            if start_date and match.group(2) < start_date:
                continue
            if end_date and match.group(2) > end_date:
                continue
            out.setdefault(match.group(2), set()).add(match.group(1))
    query = "SELECT DISTINCT date, code FROM market_minute_bars WHERE date IS NOT NULL AND code IS NOT NULL"
    params: List[Any] = []
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    code_chunks = [sorted(code_set)[idx : idx + 400] for idx in range(0, len(code_set), 400)] if code_set else [[]]
    for chunk in code_chunks:
        chunk_query = query
        chunk_params = list(params)
        if chunk:
            placeholders = ",".join("?" for _ in chunk)
            chunk_query += f" AND code IN ({placeholders})"
            chunk_params.extend(chunk)
        for row in sqlite_rows(chunk_query, tuple(chunk_params)):
            date = str(row.get("date") or "").strip()[:10]
            code = digits6(row.get("code"))
            if date and code:
                out.setdefault(date, set()).add(code)
    return out


def first_data_date(
    sqlite_rows: SQLiteRows,
    cached_events: Iterable[NewsEvent],
    load_events: EventsLoader,
    *,
    kline_day_dir: Path = KLINE_DAY_DIR,
    fallback_date: str = "2026-03-01",
) -> str:
    dates = set()
    for query in (
        "SELECT MIN(date) AS date FROM news_events WHERE date IS NOT NULL",
        "SELECT MIN(date) AS date FROM news_raw WHERE date IS NOT NULL",
        "SELECT MIN(date) AS date FROM market_daily_bars WHERE date IS NOT NULL",
        "SELECT MIN(trade_date) AS date FROM lhb_records WHERE trade_date IS NOT NULL",
    ):
        for row in sqlite_rows(query):
            date = str(row.get("date") or "").strip()[:10]
            if date:
                dates.add(date)
    for event in cached_events:
        if event.date:
            dates.add(event.date)
    if not dates:
        try:
            for event in load_events():
                if event.date:
                    dates.add(event.date)
        except Exception:
            pass
    if kline_day_dir.exists():
        for path in kline_day_dir.glob("*.json"):
            payload = read_json(path, [])
            if isinstance(payload, list):
                for row in payload[:5]:
                    date = str((row or {}).get("date") or "").strip()[:10] if isinstance(row, dict) else ""
                    if date:
                        dates.add(date)
                        break
    return min(dates) if dates else fallback_date


def latest_price(
    code: str,
    *,
    load_intraday_bars: IntradayLoader,
    load_kline: DailyKlineLoader,
    as_of: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    code = digits6(code)
    if as_of:
        bars = load_intraday_bars(code, as_of)
        if bars:
            latest_bar = bars[-1]
            return {
                "date": latest_bar.get("date", as_of),
                "time": latest_bar.get("time", ""),
                "open": latest_bar.get("open", 0),
                "close": latest_bar.get("close", 0),
                "high": latest_bar.get("high", 0),
                "low": latest_bar.get("low", 0),
                "volume": latest_bar.get("volume", 0),
                "source": "intraday",
            }
    rows = load_kline(code)
    if as_of:
        rows = [row for row in rows if row["date"] <= as_of]
    if not rows:
        return None
    return {**rows[-1], "source": "daily"}


def future_return_from_rows(
    rows: List[Dict[str, Any]],
    event_date: str,
    hold_days: int = 3,
) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    start_idx = None
    for idx, row in enumerate(rows):
        if str(row.get("date") or "") > event_date:
            start_idx = idx
            break
    if start_idx is None:
        return None
    exit_idx = start_idx + max(1, int(hold_days or 1)) - 1
    if exit_idx >= len(rows):
        return None
    entry = rows[start_idx]
    exit_row = rows[exit_idx]
    entry_price = safe_float(entry.get("open") or entry.get("close"), 0)
    exit_price = safe_float(exit_row.get("close"), 0)
    if entry_price <= 0 or exit_price <= 0:
        return None
    return {
        "entry_date": entry["date"],
        "exit_date": exit_row["date"],
        "entry_price": round(entry_price, 3),
        "exit_price": round(exit_price, 3),
        "return_pct": round((exit_price / entry_price - 1) * 100, 3),
    }


def all_trading_dates_for_codes(
    sqlite_rows: SQLiteRows,
    load_kline: DailyKlineLoader,
    available_intraday_dates: Callable[[List[str], Optional[str], Optional[str]], Dict[str, set]],
    codes: List[str],
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_file: Path = QUANT_DB_FILE,
) -> List[str]:
    clean_codes = sorted({digits6(code) for code in codes if digits6(code)})
    dates = set()
    if clean_codes and db_file.exists():
        for table, date_column in (("market_daily_bars", "date"), ("market_minute_bars", "date")):
            for offset in range(0, len(clean_codes), 400):
                chunk = clean_codes[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                query = f"SELECT DISTINCT {date_column} AS date FROM {table} WHERE code IN ({placeholders}) AND {date_column} IS NOT NULL"
                params: List[Any] = list(chunk)
                if start_date:
                    query += f" AND {date_column} >= ?"
                    params.append(start_date)
                if end_date:
                    query += f" AND {date_column} <= ?"
                    params.append(end_date)
                for row in sqlite_rows(query, tuple(params)):
                    date = str(row.get("date") or "").strip()[:10]
                    if date:
                        dates.add(date)
    if not dates:
        for code in clean_codes:
            for row in load_kline(code):
                date = str(row.get("date") or "").strip()[:10]
                if not date:
                    continue
                if start_date and date < start_date:
                    continue
                if end_date and date > end_date:
                    continue
                dates.add(date)
    available_intraday = available_intraday_dates(clean_codes, start_date, end_date)
    code_set = set(clean_codes)
    for date, date_codes in available_intraday.items():
        if code_set.intersection(date_codes):
            dates.add(date)
    return sorted(dates)
