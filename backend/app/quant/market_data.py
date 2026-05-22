from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.quant.biying_sync import BiyingSyncError, _normalize_symbol, biying_minute_sync
from app.quant.engine import KLINE_DAY_DIR, digits6, read_json, safe_float, write_json


KLINE_FIELDS = ["date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"]
MAX_DAILY_SYNC_CODES = 5000


def _normalize_date(value: Optional[str]) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        return ""
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _ymd8(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _date_range_weekdays(start_date: str, end_date: str) -> int:
    try:
        start = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
        end = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
    except Exception:
        return 0
    if end < start:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def _available_end_date(end_date: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return min(_normalize_date(end_date) or today, today)


def _first_value(row: Dict[str, Any], keys: Sequence[str], default: Any = None) -> Any:
    lower_key_map = {str(key).lower(): key for key in row.keys()}
    for key in keys:
        if key in row:
            value = row.get(key)
            if value is not None and str(value).strip() not in {"", "--", "null", "None"}:
                return value
        mapped = lower_key_map.get(str(key).lower())
        if mapped is not None:
            value = row.get(mapped)
            if value is not None and str(value).strip() not in {"", "--", "null", "None"}:
                return value
    return default


def _extract_rows(payload: Any) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "result", "rows", "list", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                for sub_key in ("list", "rows", "items"):
                    sub_value = value.get(sub_key)
                    if isinstance(sub_value, list):
                        return [row for row in sub_value if isinstance(row, dict)]
        dict_values = [value for value in payload.values() if isinstance(value, dict)]
        if dict_values:
            return dict_values
        if any(key in payload for key in ("time", "datetime", "date", "close", "price", "c")):
            return [payload]
    return []


def _normalize_biying_date(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return _normalize_date(text)


def _parse_biying_daily_rows(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in _extract_rows(payload):
        raw_date = _first_value(
            row,
            [
                "time",
                "datetime",
                "date",
                "trade_time",
                "t",
                "d",
                "dt",
                "day",
                "\u4ea4\u6613\u65f6\u95f4",
                "\u65f6\u95f4",
                "\u65e5\u671f",
            ],
            "",
        )
        date = _normalize_biying_date(raw_date)
        close_price = safe_float(
            _first_value(row, ["close", "latest", "price", "c", "p", "\u6536\u76d8", "\u6536\u76d8\u4ef7"], 0)
        )
        if not date or close_price <= 0:
            continue
        open_price = safe_float(_first_value(row, ["open", "open_price", "o", "op", "\u5f00\u76d8", "\u5f00\u76d8\u4ef7"], close_price))
        high_price = safe_float(_first_value(row, ["high", "h", "hp", "\u6700\u9ad8", "\u6700\u9ad8\u4ef7"], close_price))
        low_price = safe_float(_first_value(row, ["low", "l", "lp", "\u6700\u4f4e", "\u6700\u4f4e\u4ef7"], close_price))
        volume = safe_float(_first_value(row, ["volume", "vol", "v", "tv", "pv", "\u6210\u4ea4\u91cf", "\u6210\u4ea4\u603b\u91cf"], 0))
        amount = safe_float(
            _first_value(row, ["amount", "turnover_amount", "a", "cje", "\u6210\u4ea4\u989d", "\u6210\u4ea4\u91d1\u989d"], 0)
        )
        pct_chg = safe_float(_first_value(row, ["pct_chg", "change_rate", "zdf", "\u6da8\u8dcc\u5e45"], 0))
        turnover = safe_float(_first_value(row, ["turnover", "turnover_rate", "hsl", "\u6362\u624b\u7387"], 0))
        rows.append(
            {
                "date": date,
                "open": open_price if open_price > 0 else close_price,
                "close": close_price,
                "high": high_price if high_price > 0 else close_price,
                "low": low_price if low_price > 0 else close_price,
                "volume": volume,
                "amount": amount,
                "pct_chg": pct_chg,
                "turnover": turnover,
            }
        )
    by_date = {row["date"]: row for row in rows}
    return [by_date[key] for key in sorted(by_date.keys())]


def _fetch_biying_daily(code: str, start_date: str, end_date: str, timeout: int) -> List[Dict[str, Any]]:
    symbol = _normalize_symbol(code)
    cfg = biying_minute_sync.config()
    if not symbol:
        raise BiyingSyncError("invalid stock code")
    if not cfg["enabled"]:
        raise BiyingSyncError("biying is not enabled or license key is missing")

    ymd_start = _ymd8(start_date)
    ymd_end = _ymd8(end_date)
    history_path = f"/hsstock/history/{symbol}/d/n/{cfg['license_key']}"
    payload = biying_minute_sync._request_json(
        history_path,
        params={"st": ymd_start, "et": ymd_end, "lt": 10000},
        timeout=timeout,
    )
    rows = _parse_biying_daily_rows(payload)
    if not rows:
        payload = biying_minute_sync._request_json(
            history_path,
            params={"st": f"{ymd_start}000000", "et": f"{ymd_end}235959", "lt": 10000},
            timeout=timeout,
        )
        rows = _parse_biying_daily_rows(payload)
    return [row for row in rows if start_date <= row["date"] <= end_date]


def _read_cached_rows(code: str) -> List[Dict[str, Any]]:
    path = KLINE_DAY_DIR / f"{digits6(code)}.json"
    payload = read_json(path, [])
    rows: List[Dict[str, Any]] = []
    if not isinstance(payload, list):
        return rows
    for row in payload:
        if not isinstance(row, dict):
            continue
        date = _normalize_date(row.get("date"))
        close_price = safe_float(row.get("close"), 0)
        if not date or close_price <= 0:
            continue
        rows.append({**row, "date": date, "close": close_price})
    rows.sort(key=lambda item: item["date"])
    return rows


def _needs_fetch(rows: List[Dict[str, Any]], start_date: str, end_date: str) -> bool:
    if not rows:
        return True
    dates = {str(row.get("date") or "")[:10] for row in rows}
    in_range = [date for date in dates if start_date <= date <= end_date]
    if not in_range:
        return True
    expected_weekdays = _date_range_weekdays(start_date, end_date)
    if expected_weekdays >= 10 and len(in_range) < max(2, int(expected_weekdays * 0.55)):
        return True
    return min(in_range) > start_date or max(in_range) < end_date


def sync_daily_kline(
    code: str,
    start_date: str,
    end_date: str,
    force: bool = False,
    timeout: int = 12,
) -> Dict[str, Any]:
    code = digits6(code)
    start_date = _normalize_date(start_date)
    end_date = _normalize_date(end_date)
    if not code or not start_date or not end_date:
        return {"status": "error", "code": code, "source": "biying", "message": "\u80a1\u7968\u4ee3\u7801\u6216\u65e5\u671f\u65e0\u6548"}
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    fetch_end_date = _available_end_date(end_date)

    cached = _read_cached_rows(code)
    if not force and not _needs_fetch(cached, start_date, fetch_end_date):
        return {
            "status": "skipped",
            "code": code,
            "source": "biying",
            "message": "\u65e5K\u5df2\u8986\u76d6\u76ee\u6807\u533a\u95f4",
            "requested_end_date": end_date,
            "end_date": fetch_end_date,
            "rows_total": len(cached),
            "added_rows": 0,
            "updated_rows": 0,
        }

    try:
        fetched_rows = _fetch_biying_daily(code, start_date=start_date, end_date=fetch_end_date, timeout=timeout)
    except Exception as exc:
        return {
            "status": "failed",
            "code": code,
            "source": "biying",
            "message": "\u5fc5\u76c8\u65e5K\u62c9\u53d6\u5931\u8d25",
            "error": str(exc)[:220],
            "rows_total": len(cached),
            "added_rows": 0,
            "updated_rows": 0,
        }
    if not fetched_rows:
        return {
            "status": "empty",
            "code": code,
            "source": "biying",
            "message": "\u5fc5\u76c8\u6ca1\u6709\u8fd4\u56de\u65e5K\u6570\u636e",
            "rows_total": len(cached),
            "added_rows": 0,
            "updated_rows": 0,
        }

    merged = {str(row.get("date") or "")[:10]: dict(row) for row in cached if row.get("date")}
    before = len(merged)
    updated = 0
    for row in fetched_rows:
        date = row["date"]
        if date in merged and merged[date] != row:
            updated += 1
        merged[date] = row
    rows = [merged[key] for key in sorted(merged.keys())]
    KLINE_DAY_DIR.mkdir(parents=True, exist_ok=True)
    write_json(KLINE_DAY_DIR / f"{code}.json", rows)
    return {
        "status": "ok",
        "code": code,
        "source": "biying",
        "start_date": start_date,
        "end_date": fetch_end_date,
        "requested_end_date": end_date,
        "fetched_rows": len(fetched_rows),
        "rows_total": len(rows),
        "added_rows": max(0, len(merged) - before),
        "updated_rows": updated,
    }


def sync_daily_for_codes(
    codes: Iterable[str],
    start_date: str,
    end_date: str,
    max_codes: int = 300,
    force: bool = False,
    pause_seconds: float = 0.05,
) -> Dict[str, Any]:
    seen = set()
    selected: List[str] = []
    for code in codes:
        clean = digits6(code)
        if clean and clean not in seen:
            seen.add(clean)
            selected.append(clean)
        if len(selected) >= max(1, min(int(max_codes or 300), MAX_DAILY_SYNC_CODES)):
            break

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    fetched = 0
    added_rows = 0
    updated_rows = 0
    for index, code in enumerate(selected):
        try:
            result = sync_daily_kline(code, start_date=start_date, end_date=end_date, force=force)
            results.append(result)
            status = str(result.get("status") or "")
            if status == "ok":
                fetched += 1
            if status in {"error", "failed"}:
                errors.append({"code": code, "error": result.get("error") or result.get("message") or status})
            added_rows += int(result.get("added_rows") or 0)
            updated_rows += int(result.get("updated_rows") or 0)
        except Exception as exc:
            errors.append({"code": code, "error": str(exc)})
        if pause_seconds > 0 and index < len(selected) - 1:
            time.sleep(pause_seconds)

    return {
        "status": "partial" if errors else "ok",
        "source": "biying",
        "start_date": _normalize_date(start_date),
        "end_date": _normalize_date(end_date),
        "requested": len(selected),
        "fetched": fetched,
        "added_rows": added_rows,
        "updated_rows": updated_rows,
        "errors": errors[:20],
        "results": results[:80],
    }
