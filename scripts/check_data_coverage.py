from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "backend" / "data"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "records", "events"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _date_from_item(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("date", "time", "time_str", "ts", "created_at", "updated_at", "analyzed_at"):
        value = str(item.get(key) or "").strip()
        if len(value) >= 10:
            return value[:10]
    return ""


def _summarize_file(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    items = _items(payload)
    dates = sorted(date for date in (_date_from_item(item) for item in items) if date)
    return {
        "file": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "items": len(items),
        "min_date": dates[0] if dates else "",
        "max_date": dates[-1] if dates else "",
    }


def _summarize_kline_dir(path: Path, inspect_rows: bool = True) -> dict[str, Any]:
    files = sorted(path.glob("*.json")) if path.exists() else []
    if not inspect_rows:
        return {
            "dir": str(path),
            "files": len(files),
            "rows": "-",
            "min_date": "",
            "max_date": "",
            "size_bytes": sum(file_path.stat().st_size for file_path in files if file_path.exists()),
        }
    row_count = 0
    dates: list[str] = []
    for file_path in files:
        rows = _items(_read_json(file_path))
        row_count += len(rows)
        if rows:
            for item in (rows[0], rows[-1]):
                date = _date_from_item(item)
                if date:
                    dates.append(date)
    dates.sort()
    return {
        "dir": str(path),
        "files": len(files),
        "rows": row_count,
        "min_date": dates[0] if dates else "",
        "max_date": dates[-1] if dates else "",
    }


def _summarize_intraday_dir(path: Path, inspect_rows: bool = True) -> dict[str, Any]:
    files = sorted(path.glob("*.csv")) if path.exists() else []
    if not inspect_rows:
        return {
            "dir": str(path),
            "files": len(files),
            "rows": "-",
            "min_date": "",
            "max_date": "",
            "size_bytes": sum(file_path.stat().st_size for file_path in files if file_path.exists()),
        }
    row_count = 0
    dates: list[str] = []
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        except Exception:
            rows = []
        row_count += len(rows)
        if rows:
            for item in (rows[0], rows[-1]):
                date = _date_from_item(item)
                if date:
                    dates.append(date)
    dates.sort()
    return {
        "dir": str(path),
        "files": len(files),
        "rows": row_count,
        "min_date": dates[0] if dates else "",
        "max_date": dates[-1] if dates else "",
    }


def _summarize_sqlite_table(db_path: Path, table: str, date_column: str, code_column: str = "") -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": f"{db_path}:{table}",
        "exists": db_path.exists(),
        "size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "items": 0,
        "codes": "-",
        "min_date": "",
        "max_date": "",
    }
    if not db_path.exists():
        return summary
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS count, MIN({date_column}) AS min_date, MAX({date_column}) AS max_date FROM {table}"
            ).fetchone()
            if row:
                summary["items"] = int(row[0] or 0)
                summary["min_date"] = str(row[1] or "")[:10]
                summary["max_date"] = str(row[2] or "")[:10]
            if code_column:
                code_row = conn.execute(
                    f"SELECT COUNT(DISTINCT {code_column}) FROM {table} WHERE {code_column} IS NOT NULL AND {code_column} != ''"
                ).fetchone()
                summary["codes"] = int((code_row or [0])[0] or 0)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        summary["error"] = str(exc)
    return summary


def _print_table(rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        name = row.get("name") or row.get("file") or row.get("dir")
        error = f", error={row.get('error')}" if row.get("error") else ""
        print(
            f"{name}: count={row.get('items', row.get('rows', 0))}, "
            f"files={row.get('files', '-')}, "
            f"codes={row.get('codes', '-')}, "
            f"min={row.get('min_date') or '-'}, max={row.get('max_date') or '-'}, "
            f"size={row.get('size_bytes', '-')}{error}"
        )


def _data_dir_from_args(argv: list[str]) -> Path:
    if len(argv) <= 1:
        return DEFAULT_DATA_DIR
    if argv[1] in {"-h", "--help"}:
        print("Usage: python scripts/check_data_coverage.py [backend/data path]")
        raise SystemExit(0)
    return Path(argv[1]).resolve()


def main() -> None:
    data_dir = _data_dir_from_args(sys.argv)
    db_path = data_dir / "quant_data.sqlite3"
    sqlite_available = db_path.exists()
    rows = [
        _summarize_file(data_dir / "news_history.json"),
        _summarize_file(data_dir / "news_analysis_records.json"),
        _summarize_file(data_dir / "quant_events_cache.json"),
        _summarize_file(data_dir / "strategy_evolution_state.json"),
        _summarize_file(data_dir / "access_logs.json"),
        _summarize_kline_dir(data_dir / "kline_day_cache", inspect_rows=not sqlite_available),
        _summarize_intraday_dir(data_dir / "kline_cache", inspect_rows=not sqlite_available),
        _summarize_sqlite_table(db_path, "news_raw", "date"),
        _summarize_sqlite_table(db_path, "news_events", "date", "code"),
        _summarize_sqlite_table(db_path, "market_daily_bars", "date", "code"),
        _summarize_sqlite_table(db_path, "market_minute_bars", "date", "code"),
        _summarize_sqlite_table(db_path, "lhb_records", "trade_date", "stock_code"),
    ]
    _print_table(rows)
    news_json = rows[0]
    news_sqlite = rows[7]
    news_min_date = str(news_sqlite.get("min_date") or news_json.get("min_date") or "")
    if not news_min_date or news_min_date > "2026-03-01":
        print("WARNING: news data does not cover 2026-03-01. Historical replay from March is incomplete.")


if __name__ == "__main__":
    main()
