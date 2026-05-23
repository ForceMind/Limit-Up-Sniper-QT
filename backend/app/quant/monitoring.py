from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.quant.ai_analyzer import ai_analyzer
from app.quant.biying_sync import biying_minute_sync
from app.quant.engine import KLINE_DAY_DIR, KLINE_MIN_DIR, contains_sample_marker, digits6, quant_engine
from app.quant.news_fetcher import news_fetcher


def _count_csv_rows(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return max(0, sum(1 for _ in csv.reader(f)) - 1)
    except Exception:
        return 0


def _ratio(covered: int, total: int) -> float:
    return round(covered / total, 4) if total > 0 else 0.0


def _minute_cache_dates() -> Dict[str, int]:
    out: Dict[str, int] = {}
    if not KLINE_MIN_DIR.exists():
        return out
    for path in KLINE_MIN_DIR.glob("*.csv"):
        parts = path.stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        code, date = parts
        if digits6(code) and len(date) == 10:
            out[date] = out.get(date, 0) + 1
    return dict(sorted(out.items(), reverse=True))


def _target_codes(as_of: str, top_n: int) -> List[Dict[str, Any]]:
    seen = set()
    rows: List[Dict[str, Any]] = []

    def add(code: Any, source: str, score: float = 0.0) -> None:
        clean = digits6(code)
        if not clean or clean in seen or contains_sample_marker({"code": clean}) or not quant_engine.universe.is_tradeable_a_share(clean):
            return
        seen.add(clean)
        rows.append(
            {
                "code": clean,
                "name": quant_engine.universe.name(clean),
                "source": source,
                "score": round(float(score or 0), 2),
            }
        )

    recs = quant_engine.recommendations(as_of=as_of, lookback_days=2, top_n=top_n)
    for item in recs.get("items", []):
        add(item.get("code"), "recommendation", item.get("buy_score", 0))

    account = quant_engine.trading_account(as_of=as_of, limit=200)
    for item in account.get("positions", []):
        add(item.get("code"), "position", item.get("unrealized_pnl_pct", 0))

    events = [event for event in quant_engine.events() if event.date <= as_of]
    events.sort(key=lambda event: (event.date, event.impact_score, event.timestamp), reverse=True)
    for event in events:
        if len(rows) >= top_n:
            break
        add(event.code, "event", event.impact_score)

    return rows[:top_n]


def data_coverage(as_of: Optional[str] = None, top_n: int = 80) -> Dict[str, Any]:
    as_of = str(as_of or quant_engine.latest_event_date()).strip()
    top_n = max(1, min(int(top_n or 80), 300))
    targets = _target_codes(as_of=as_of, top_n=top_n)

    daily_covered = 0
    minute_covered = 0
    target_rows = []
    for target in targets:
        code = target["code"]
        minute_path = KLINE_MIN_DIR / f"{code}_{as_of}.csv"
        daily_rows = [row for row in quant_engine.load_kline(code) if str(row.get("date") or "") <= as_of]
        minute_rows = quant_engine.load_intraday_bars(code, as_of)
        daily_ok = bool(daily_rows)
        minute_ok = bool(minute_rows)
        if daily_ok:
            daily_covered += 1
        if minute_ok:
            minute_covered += 1
        target_rows.append(
            {
                **target,
                "daily_kline": daily_ok,
                "minute_kline": minute_ok,
                "minute_rows": len(minute_rows) if minute_rows else (_count_csv_rows(minute_path) if minute_path.exists() else 0),
            }
        )

    news_state = news_fetcher.status()
    ai_state = ai_analyzer.status()
    minute_dates = _minute_cache_dates()
    day_files = list(KLINE_DAY_DIR.glob("*.json")) if KLINE_DAY_DIR.exists() else []
    minute_files = list(KLINE_MIN_DIR.glob("*.csv")) if KLINE_MIN_DIR.exists() else []
    events = quant_engine.events()
    lhb_summary = quant_engine.lhb_summary(end_date=as_of, recent_limit=20)
    event_dates: Dict[str, int] = {}
    for event in events:
        event_dates[event.date] = event_dates.get(event.date, 0) + 1

    return {
        "status": "ok",
        "as_of": as_of,
        "summary": {
            "stock_universe": len(quant_engine.universe.code_to_name),
            "news_count": news_state.get("history_count", 0),
            "ai_records": ai_state.get("records", 0),
            "events": len(events),
            "day_kline_files": len(day_files),
            "minute_kline_files": len(minute_files),
            "target_count": len(targets),
            "lhb_rows": lhb_summary.get("rows", 0),
            "lhb_stock_count": lhb_summary.get("stock_count", 0),
            "latest_lhb_date": lhb_summary.get("latest_date", ""),
        },
        "daily_coverage": {
            "covered": daily_covered,
            "missing": max(0, len(targets) - daily_covered),
            "ratio": _ratio(daily_covered, len(targets)),
        },
        "minute_coverage": {
            "date": as_of,
            "covered": minute_covered,
            "missing": max(0, len(targets) - minute_covered),
            "ratio": _ratio(minute_covered, len(targets)),
        },
        "minute_cache_dates": minute_dates,
        "recent_event_dates": dict(sorted(event_dates.items(), reverse=True)[:20]),
        "news": {
            "latest_time": news_state.get("latest_time", ""),
            "recent_dates": news_state.get("recent_dates", {}),
        },
        "ai": ai_state,
        "biying": biying_minute_sync.status(),
        "lhb": {
            "rows": lhb_summary.get("rows", 0),
            "stock_count": lhb_summary.get("stock_count", 0),
            "latest_date": lhb_summary.get("latest_date", ""),
            "recent_dates": lhb_summary.get("recent_dates", []),
        },
        "targets": target_rows,
    }


def ai_usage_summary() -> Dict[str, Any]:
    records = ai_analyzer.records()
    total_prompt = 0
    total_completion = 0
    total_tokens = 0
    by_model: Dict[str, Dict[str, Any]] = {}
    by_source: Dict[str, int] = {}
    error_count = 0
    fallback_count = 0

    for record in records:
        if not isinstance(record, dict):
            continue
        model = str(record.get("model") or "unknown")
        source = str(record.get("analysis_source") or "unknown")
        usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
        prompt = int(float(usage.get("prompt_tokens") or 0))
        completion = int(float(usage.get("completion_tokens") or 0))
        tokens = int(float(usage.get("total_tokens") or prompt + completion or 0))
        total_prompt += prompt
        total_completion += completion
        total_tokens += tokens
        by_source[source] = by_source.get(source, 0) + 1
        if source == "fallback":
            fallback_count += 1
        if str(record.get("error") or "").strip():
            error_count += 1
        model_row = by_model.setdefault(
            model,
            {"records": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        model_row["records"] += 1
        model_row["prompt_tokens"] += prompt
        model_row["completion_tokens"] += completion
        model_row["total_tokens"] += tokens

    return {
        "status": "ok",
        "records": len(records),
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "fallback_records": fallback_count,
        "error_records": error_count,
        "by_model": by_model,
        "by_source": by_source,
    }


def ai_records_feed(limit: int = 100, code: Optional[str] = None, source: Optional[str] = None) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    code = digits6(code or "")
    source = str(source or "").strip()
    rows = []
    for record in ai_analyzer.records():
        if not isinstance(record, dict):
            continue
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        stocks = result.get("stocks") if isinstance(result.get("stocks"), list) else []
        if code and not any(digits6(item.get("code")) == code for item in stocks if isinstance(item, dict)):
            continue
        if source and str(record.get("analysis_source") or "") != source:
            continue
        rows.append(_compact_ai_record(record))
        if len(rows) >= limit:
            break
    return {"status": "ok", "items": rows, "count": len(rows)}


def ai_failures(limit: int = 100) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 100), 500))
    rows = []
    for record in ai_analyzer.records():
        if not isinstance(record, dict):
            continue
        source = str(record.get("analysis_source") or "")
        error = str(record.get("error") or "").strip()
        if not error and source != "fallback":
            continue
        rows.append(_compact_ai_record(record))
        if len(rows) >= limit:
            break
    return {"status": "ok", "items": rows, "count": len(rows)}


def _compact_ai_record(record: Dict[str, Any]) -> Dict[str, Any]:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    stocks = result.get("stocks") if isinstance(result.get("stocks"), list) else []
    news_items = record.get("news_items") if isinstance(record.get("news_items"), list) else []
    return {
        "record_key": record.get("record_key", ""),
        "analyzed_at": record.get("analyzed_at", ""),
        "provider": record.get("provider", ""),
        "model": record.get("model", ""),
        "analysis_source": record.get("analysis_source", ""),
        "error": record.get("error", ""),
        "usage": record.get("usage") if isinstance(record.get("usage"), dict) else {},
        "summary": result.get("summary", ""),
        "stocks": [
            {
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "event_type": item.get("event_type", ""),
                "sentiment": item.get("sentiment", ""),
                "impact_score": item.get("impact_score", 0),
                "score": item.get("score", 0),
                "strategy": item.get("strategy", ""),
                "reason": str(item.get("reason") or "")[:180],
            }
            for item in stocks[:20]
            if isinstance(item, dict)
        ],
        "news_items": [
            {
                "id": item.get("id", ""),
                "time_str": item.get("time_str", ""),
                "source": item.get("source", ""),
                "text": str(item.get("text") or "")[:220],
            }
            for item in news_items[:5]
            if isinstance(item, dict)
        ],
    }
