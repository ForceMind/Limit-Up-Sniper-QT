from __future__ import annotations

import csv
import json
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


def _summarize_kline_dir(path: Path) -> dict[str, Any]:
    files = sorted(path.glob("*.json")) if path.exists() else []
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


def _summarize_intraday_dir(path: Path) -> dict[str, Any]:
    files = sorted(path.glob("*.csv")) if path.exists() else []
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


def _print_table(rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        name = row.get("file") or row.get("dir")
        print(
            f"{name}: count={row.get('items', row.get('rows', 0))}, "
            f"files={row.get('files', '-')}, "
            f"min={row.get('min_date') or '-'}, max={row.get('max_date') or '-'}, "
            f"size={row.get('size_bytes', '-')}"
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
    rows = [
        _summarize_file(data_dir / "news_history.json"),
        _summarize_file(data_dir / "news_analysis_records.json"),
        _summarize_file(data_dir / "quant_events_cache.json"),
        _summarize_file(data_dir / "strategy_evolution_state.json"),
        _summarize_file(data_dir / "access_logs.json"),
        _summarize_kline_dir(data_dir / "kline_day_cache"),
        _summarize_intraday_dir(data_dir / "kline_cache"),
    ]
    _print_table(rows)
    news = rows[0]
    if not news.get("min_date") or str(news["min_date"]) > "2026-03-01":
        print("WARNING: news_history.json does not cover 2026-03-01. Historical replay from March is incomplete.")


if __name__ == "__main__":
    main()
