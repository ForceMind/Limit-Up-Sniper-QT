from __future__ import annotations

import csv
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests

from app.quant.engine import quant_engine
from app.quant.engine_utils import digits6, read_json, safe_float
from app.quant.quant_paths import DATA_DIR, KLINE_MIN_DIR


CONFIG_FILE = DATA_DIR / "config.json"
SYNC_STATE_FILE = DATA_DIR / "biying_intraday_sync_state.json"
CSV_FIELDS = ["time", "open", "close", "high", "low", "volume", "amount"]


class BiyingSyncError(RuntimeError):
    pass


@dataclass
class SyncTarget:
    code: str
    name: str = ""


def _now_cn() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _normalize_date(value: Optional[str]) -> Tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        text = quant_engine.latest_event_date() or _now_cn().strftime("%Y-%m-%d")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 8:
        raise BiyingSyncError(f"invalid date: {value}")
    ymd8 = digits[:8]
    return ymd8, f"{ymd8[:4]}-{ymd8[4:6]}-{ymd8[6:8]}"


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


def _normalize_biying_time(raw_value: Any, target_date: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 14:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
    if len(digits) == 12:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}:00"
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(digits) == 6 and ":" not in text:
        return f"{target_date} {digits[:2]}:{digits[2:4]}:{digits[4:6]}"
    if len(digits) == 4 and ":" not in text:
        return f"{target_date} {digits[:2]}:{digits[2:4]}:00"
    if len(text) == 8 and text.count(":") == 2:
        return f"{target_date} {text}"
    if len(text) == 5 and text.count(":") == 1:
        return f"{target_date} {text}:00"
    return text


def _parse_kline_rows(payload: Any, target_date: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in _extract_rows(payload):
        raw_time = _first_value(
            row,
            ["time", "datetime", "date", "trade_time", "t", "d", "dt", "交易时间", "时间", "日期", "day"],
            "",
        )
        ts = _normalize_biying_time(raw_time, target_date)
        if not ts or not ts.startswith(target_date):
            continue
        open_price = safe_float(_first_value(row, ["open", "open_price", "o", "op", "开盘", "开盘价"], 0))
        close_price = safe_float(_first_value(row, ["close", "latest", "price", "c", "p", "收盘", "收盘价"], 0))
        high_price = safe_float(_first_value(row, ["high", "h", "hp", "最高", "最高价"], close_price))
        low_price = safe_float(_first_value(row, ["low", "l", "lp", "最低", "最低价"], close_price))
        volume = safe_float(_first_value(row, ["volume", "vol", "v", "tv", "pv", "成交量", "成交总量"], 0))
        amount = safe_float(_first_value(row, ["amount", "turnover_amount", "a", "cje", "成交额", "成交金额"], 0))
        if close_price <= 0:
            continue
        out.append(
            {
                "time": ts,
                "open": open_price if open_price > 0 else close_price,
                "close": close_price,
                "high": high_price if high_price > 0 else close_price,
                "low": low_price if low_price > 0 else close_price,
                "volume": volume,
                "amount": amount,
            }
        )
    by_time = {str(row["time"]): row for row in out if row.get("time")}
    return [by_time[key] for key in sorted(by_time.keys())]


def _normalize_symbol(code: str) -> str:
    clean_code = digits6(code)
    if not clean_code:
        return ""
    if clean_code.startswith("6"):
        return f"{clean_code}.SH"
    if clean_code.startswith(("0", "3")):
        return f"{clean_code}.SZ"
    if clean_code.startswith(("8", "4", "9")):
        return f"{clean_code}.BJ"
    return f"{clean_code}.SZ"


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> int:
    KLINE_MIN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    tmp.replace(path)
    return len(rows)


class BiyingMinuteSync:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._minute_key = ""
        self._minute_count = 0
        self._last_request_ts = 0.0

    def config(self) -> Dict[str, Any]:
        payload = read_json(CONFIG_FILE, {})
        cfg = payload.get("data_provider_config") if isinstance(payload, dict) else {}
        if not isinstance(cfg, dict):
            cfg = {}

        env_key = os.getenv("BIYING_LICENSE_KEY")
        env_endpoint = os.getenv("BIYING_ENDPOINT")
        env_limit = os.getenv("BIYING_MINUTE_LIMIT")
        env_enabled = os.getenv("BIYING_ENABLED")

        key = str(env_key if env_key is not None else cfg.get("biying_license_key", "") or "").strip()
        endpoint = str(env_endpoint if env_endpoint is not None else cfg.get("biying_endpoint", "") or "").strip()
        endpoint = endpoint or "https://api.biyingapi.com"
        minute_limit_value = env_limit if env_limit is not None else cfg.get("biying_minute_limit")
        minute_limit = int(safe_float(minute_limit_value, 3000))
        if env_enabled is None:
            enabled = bool(cfg.get("biying_enabled")) or bool(key)
        else:
            enabled = str(env_enabled).strip().lower() not in {"0", "false", "no", "off", ""}
        return {
            "enabled": bool(enabled and key),
            "license_key": key,
            "endpoint": endpoint,
            "minute_limit": max(1, min(minute_limit, 100000)),
        }

    def status(self) -> Dict[str, Any]:
        cfg = self.config()
        cache_dates: Dict[str, int] = {}
        if KLINE_MIN_DIR.exists():
            for path in KLINE_MIN_DIR.glob("*.csv"):
                name = path.stem
                parts = name.rsplit("_", 1)
                if len(parts) == 2 and digits6(parts[0]) and len(parts[1]) == 10:
                    cache_dates[parts[1]] = cache_dates.get(parts[1], 0) + 1
        state = read_json(SYNC_STATE_FILE, {})
        if isinstance(state, dict):
            state = {key: value for key, value in state.items() if key != "results"}
        return {
            "enabled": cfg["enabled"],
            "endpoint": self._safe_endpoint_label(cfg["endpoint"]),
            "cache_dates": dict(sorted(cache_dates.items())),
            "last_sync": state if isinstance(state, dict) else {},
        }

    def select_targets(
        self,
        date: str,
        source: str = "events",
        max_codes: int = 200,
        explicit_codes: Optional[str] = None,
    ) -> List[SyncTarget]:
        max_codes = max(1, min(int(max_codes or 200), 2000))
        explicit = self._parse_codes(explicit_codes)
        if explicit:
            return [SyncTarget(code=code, name=quant_engine.universe.name(code)) for code in explicit[:max_codes]]

        source = str(source or "events").strip().lower()
        targets: List[SyncTarget] = []
        seen = set()

        def add(code: Any, name: Any = "") -> None:
            clean = digits6(code)
            if not clean or clean in seen or not quant_engine.universe.is_tradeable_a_share(clean):
                return
            seen.add(clean)
            targets.append(SyncTarget(code=clean, name=str(name or quant_engine.universe.name(clean)).strip()))

        if source == "recommendations":
            rec = quant_engine.recommendations(as_of=date, lookback_days=2, top_n=max_codes)
            for item in rec.get("items", []):
                add(item.get("code"), item.get("name"))
        elif source == "stock_list":
            for code, name in sorted(quant_engine.universe.code_to_name.items()):
                add(code, name)
                if len(targets) >= max_codes:
                    break
        else:
            events = [event for event in quant_engine.events() if event.date == date]
            if source == "all_events" and not events:
                events = [event for event in quant_engine.events() if event.date <= date]
            best_by_code: Dict[str, Any] = {}
            for event in events:
                old = best_by_code.get(event.code)
                if old is None or event.impact_score > old.impact_score:
                    best_by_code[event.code] = event
            ranked = sorted(best_by_code.values(), key=lambda event: event.impact_score, reverse=True)
            for event in ranked:
                add(event.code, event.name)
                if len(targets) >= max_codes:
                    break

        return targets[:max_codes]

    def sync_intraday(
        self,
        date: Optional[str] = None,
        source: str = "events",
        max_codes: int = 200,
        codes: Optional[str] = None,
        force: bool = False,
        include_latest: bool = True,
    ) -> Dict[str, Any]:
        ymd8, ymd10 = _normalize_date(date)
        targets = self.select_targets(ymd10, source=source, max_codes=max_codes, explicit_codes=codes)
        started_at = _now_cn().isoformat(timespec="seconds")
        results = []
        counters = {"fetched": 0, "skipped": 0, "empty": 0, "failed": 0, "rows_written": 0}
        for target in targets:
            result = self.fetch_intraday(target.code, ymd8, ymd10, force=force, include_latest=include_latest)
            result["name"] = target.name
            results.append(result)
            status = result.get("status", "failed")
            if status in counters:
                counters[status] += 1
            counters["rows_written"] += int(result.get("rows", 0) or 0) if status == "fetched" else 0

        if counters["fetched"] > 0:
            quant_engine.clear_intraday_cache()

        payload = {
            "status": "ok",
            "date": ymd10,
            "source": source,
            "requested": len(targets),
            **counters,
            "started_at": started_at,
            "finished_at": _now_cn().isoformat(timespec="seconds"),
            "results": results,
        }
        self._write_state(payload)
        return payload

    def fetch_intraday(
        self,
        code: str,
        ymd8: str,
        ymd10: str,
        force: bool = False,
        include_latest: bool = True,
    ) -> Dict[str, Any]:
        clean_code = digits6(code)
        symbol = _normalize_symbol(clean_code)
        path = KLINE_MIN_DIR / f"{clean_code}_{ymd10}.csv"
        is_today = ymd8 == _now_cn().strftime("%Y%m%d")
        should_refresh_existing = bool(include_latest and is_today)
        if path.exists() and not force and not should_refresh_existing:
            return {"code": clean_code, "date": ymd10, "status": "skipped", "file": str(path), "rows": self._count_csv_rows(path)}
        if not clean_code or not symbol:
            return {"code": clean_code, "date": ymd10, "status": "failed", "error": "invalid_code"}

        cfg = self.config()
        history_path = f"/hsstock/history/{symbol}/5/n/{cfg['license_key']}"
        try:
            payload = self._request_json(history_path, params={"st": ymd8, "et": ymd8, "lt": 360}, timeout=8)
            rows = _parse_kline_rows(payload, ymd10)
            if not rows:
                payload = self._request_json(
                    history_path,
                    params={"st": f"{ymd8}000000", "et": f"{ymd8}235959", "lt": 360},
                    timeout=8,
                )
                rows = _parse_kline_rows(payload, ymd10)
            if include_latest and ymd8 == _now_cn().strftime("%Y%m%d"):
                latest_path = f"/hsstock/latest/{symbol}/5/n/{cfg['license_key']}"
                try:
                    latest_payload = self._request_json(latest_path, params={"lt": 5}, timeout=6)
                    latest_rows = _parse_kline_rows(latest_payload, ymd10)
                    by_time = {row["time"]: row for row in rows}
                    for row in latest_rows:
                        by_time[row["time"]] = row
                    rows = [by_time[key] for key in sorted(by_time.keys())]
                except Exception:
                    pass
            if not rows:
                return {"code": clean_code, "date": ymd10, "status": "empty", "file": str(path), "rows": 0}
            written = _write_csv(path, rows)
            return {"code": clean_code, "date": ymd10, "status": "fetched", "file": str(path), "rows": written}
        except Exception as exc:
            return {"code": clean_code, "date": ymd10, "status": "failed", "file": str(path), "error": str(exc)[:220]}

    def _request_json(self, path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 8) -> Any:
        cfg = self.config()
        if not cfg["enabled"]:
            raise BiyingSyncError("biying is not enabled or license key is missing")
        self._reserve_quota(cfg)
        self._throttle()

        base_url = self._base_url(cfg["endpoint"])
        req_path = "/" + str(path or "").lstrip("/")
        urls = [f"{base_url}{req_path}"]
        if base_url.startswith("https://api.biyingapi.com"):
            urls.append(f"http://api.biyingapi.com{req_path}")

        last_error = ""
        for url in urls:
            try:
                with requests.Session() as session:
                    session.trust_env = False
                    resp = session.get(
                        url,
                        params=params or {},
                        timeout=timeout,
                        headers={"User-Agent": "Quant-Agent/0.1", "Accept": "application/json,text/plain,*/*"},
                    )
                if resp.status_code != 200:
                    last_error = f"status={resp.status_code}"
                    continue
                text = (resp.text or "").strip()
                if not text:
                    last_error = "empty_response"
                    continue
                try:
                    return resp.json()
                except Exception:
                    return json.loads(text)
            except Exception as exc:
                last_error = str(exc)
        raise BiyingSyncError(last_error or "request_failed")

    def _reserve_quota(self, cfg: Dict[str, Any]) -> None:
        minute_key = _now_cn().strftime("%Y%m%d%H%M")
        minute_limit = int(cfg.get("minute_limit", 3000) or 3000)
        with self._lock:
            if self._minute_key != minute_key:
                self._minute_key = minute_key
                self._minute_count = 0
            if self._minute_count + 1 > minute_limit:
                raise BiyingSyncError("biying minute quota exceeded")
            self._minute_count += 1

    def _throttle(self) -> None:
        with self._lock:
            now_ts = time.time()
            wait_s = 0.03 - (now_ts - self._last_request_ts)
            if wait_s > 0:
                time.sleep(wait_s)
            self._last_request_ts = time.time()

    def _base_url(self, endpoint: str) -> str:
        endpoint = str(endpoint or "").strip() or "https://api.biyingapi.com"
        parsed = urlsplit(endpoint)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return "https://api.biyingapi.com"

    def _safe_endpoint_label(self, endpoint: str) -> str:
        parsed = urlsplit(str(endpoint or ""))
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://api.biyingapi.com"

    def _parse_codes(self, codes: Optional[str]) -> List[str]:
        if not codes:
            return []
        out = []
        seen = set()
        for part in str(codes).replace("，", ",").replace(";", ",").split(","):
            code = digits6(part)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    def _count_csv_rows(self, path: Path) -> int:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                return max(0, sum(1 for _ in f) - 1)
        except Exception:
            return 0

    def _write_state(self, payload: Dict[str, Any]) -> None:
        SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SYNC_STATE_FILE.with_suffix(SYNC_STATE_FILE.suffix + ".tmp")
        compact = dict(payload)
        compact["results"] = payload.get("results", [])[:500]
        tmp.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SYNC_STATE_FILE)


biying_minute_sync = BiyingMinuteSync()
