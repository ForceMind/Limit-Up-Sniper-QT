from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from app.quant.engine import DATA_DIR, item_datetime, read_json, short_hash, write_json


NEWS_HISTORY_FILE = DATA_DIR / "news_history.json"
NEWS_FETCH_STATE_FILE = DATA_DIR / "news_fetch_state.json"
CLS_API_URL = "https://www.cls.cn/nodeapi/telegraphList"
CLS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.cls.cn/telegraph",
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _news_key(item: Dict[str, Any]) -> str:
    timestamp = _safe_int(item.get("timestamp"), 0)
    source = str(item.get("source") or "").strip()
    text = str(item.get("text") or "").strip()
    explicit_id = str(item.get("id") or "").strip()
    if explicit_id:
        return explicit_id
    return short_hash(f"{timestamp}|{source}|{text[:240]}")


def _normalize_news_item(raw: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    timestamp = _safe_int(raw.get("ctime") or raw.get("time") or raw.get("timestamp"), 0)
    if timestamp <= 0:
        timestamp = int(time.time())
    title = str(raw.get("title") or "").strip()
    content = str(raw.get("content") or raw.get("brief") or raw.get("text") or "").strip()
    text = f"[{title}] {content}" if title and content and title not in content else (title or content)
    text = " ".join(text.split())
    if not text:
        return None
    dt = datetime.fromtimestamp(timestamp)
    item = {
        "id": str(raw.get("id") or raw.get("telegraph_id") or short_hash(f"{timestamp}|{source}|{text[:240]}")),
        "timestamp": timestamp,
        "time_str": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "text": text,
        "source": source,
    }
    url = str(raw.get("url") or raw.get("shareurl") or "").strip()
    if url:
        item["url"] = url
    return item


class NewsFetcher:
    def __init__(self) -> None:
        self.history_file = NEWS_HISTORY_FILE
        self.state_file = NEWS_FETCH_STATE_FILE

    def state(self) -> Dict[str, Any]:
        state = read_json(self.state_file, {})
        return state if isinstance(state, dict) else {}

    def history(self) -> List[Dict[str, Any]]:
        payload = read_json(self.history_file, [])
        return payload if isinstance(payload, list) else []

    def latest_history_time(self) -> str:
        latest = None
        for item in self.history():
            if not isinstance(item, dict):
                continue
            dt = item_datetime(item)
            if dt and (latest is None or dt > latest):
                latest = dt
        return latest.strftime("%Y-%m-%d %H:%M:%S") if latest else ""

    def fetch_cls(self, hours: int = 12, pages: int = 5, page_size: int = 20) -> List[Dict[str, Any]]:
        hours = max(1, min(int(hours or 12), 168))
        pages = max(1, min(int(pages or 5), 30))
        page_size = max(10, min(int(page_size or 20), 100))
        start_ts = int(time.time()) - hours * 3600
        last_time = int(time.time())
        out: List[Dict[str, Any]] = []
        session = requests.Session()

        for _ in range(pages):
            resp = session.get(
                CLS_API_URL,
                headers=CLS_HEADERS,
                params={"rn": page_size, "last_time": last_time},
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data") if isinstance(payload, dict) else {}
            rows = data.get("roll_data") if isinstance(data, dict) else []
            if not isinstance(rows, list) or not rows:
                break

            should_stop = False
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                item_ts = _safe_int(raw.get("ctime") or raw.get("time") or raw.get("timestamp"), 0)
                if item_ts and item_ts < start_ts:
                    should_stop = True
                    continue
                item = _normalize_news_item(raw, "CLS")
                if item:
                    out.append(item)
            last_row_ts = min(
                (_safe_int(row.get("ctime") or row.get("time"), last_time) for row in rows if isinstance(row, dict)),
                default=0,
            )
            if should_stop or last_row_ts <= 0 or last_row_ts >= last_time:
                break
            last_time = last_row_ts
            time.sleep(0.15)
        return out

    def merge_news(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        history = [item for item in self.history() if isinstance(item, dict)]
        existing = {_news_key(item) for item in history}
        inserted = 0
        updated_items = list(history)
        for item in items:
            if not isinstance(item, dict):
                continue
            key = _news_key(item)
            if not key or key in existing:
                continue
            updated_items.append(item)
            existing.add(key)
            inserted += 1

        updated_items.sort(key=lambda item: _safe_int(item.get("timestamp"), 0), reverse=True)
        write_json(self.history_file, updated_items[:50000])
        return {
            "input": len(items),
            "inserted": inserted,
            "total": len(updated_items),
            "latest_time": self.latest_history_time(),
        }

    def run(self, hours: int = 12, pages: int = 5, page_size: int = 20) -> Dict[str, Any]:
        started_at = datetime.now().isoformat(timespec="seconds")
        fetched = self.fetch_cls(hours=hours, pages=pages, page_size=page_size)
        merge = self.merge_news(fetched)
        result = {
            "status": "ok",
            "source": "CLS",
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "fetched": len(fetched),
            **merge,
        }
        write_json(self.state_file, result)
        return result

    def status(self) -> Dict[str, Any]:
        state = self.state()
        history = self.history()
        dates = {}
        for item in history:
            if not isinstance(item, dict):
                continue
            dt = item_datetime(item)
            if dt:
                date = dt.strftime("%Y-%m-%d")
                dates[date] = dates.get(date, 0) + 1
        return {
            "status": "ok",
            "history_count": len(history),
            "latest_time": self.latest_history_time(),
            "recent_dates": dict(sorted(dates.items(), reverse=True)[:10]),
            "last_fetch": state,
        }


news_fetcher = NewsFetcher()
