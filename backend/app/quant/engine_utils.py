from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


SAMPLE_CODES = {"600001", "600002"}
SAMPLE_MARKERS = ("样例", "Fixture", "样例算力", "样例电力")

_JSON_WRITE_LOCKS: Dict[str, threading.Lock] = {}
_JSON_WRITE_LOCKS_GUARD = threading.Lock()


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "--"}:
            return default
        return float(text)
    except Exception:
        return default


def env_int(name: str, default: int, minimum: int = 0, maximum: Optional[int] = None) -> int:
    try:
        value = int(float(os.getenv(name, "") or default))
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def digits6(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(text) > 6:
        text = text[-6:]
    return text if len(text) == 6 else ""


def is_sample_code(value: Any) -> bool:
    return digits6(value) in SAMPLE_CODES


def contains_sample_marker(value: Any) -> bool:
    if isinstance(value, dict):
        if is_sample_code(value.get("code") or value.get("stock_code")):
            return True
        return any(contains_sample_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_sample_marker(item) for item in value)
    if isinstance(value, str):
        return any(marker in value for marker in SAMPLE_MARKERS)
    return False


def parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text]
    if len(text) >= 19:
        candidates.extend([text[:19], text[:10]])
    elif len(text) >= 10:
        candidates.append(text[:10])
    for candidate in candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(candidate, fmt)
            except Exception:
                pass
    return None


def item_datetime(item: Dict[str, Any]) -> Optional[datetime]:
    dt = parse_time(item.get("time_str") or item.get("analyzed_at") or item.get("date"))
    if dt:
        return dt
    ts = safe_float(item.get("timestamp"), 0)
    if ts > 0:
        try:
            return datetime.fromtimestamp(ts)
        except Exception:
            return None
    return None


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _JSON_WRITE_LOCKS_GUARD:
        lock = _JSON_WRITE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        last_error: Optional[Exception] = None
        for _ in range(5):
            try:
                tmp.replace(path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.08)
        try:
            tmp.unlink(missing_ok=True)
        finally:
            if last_error:
                raise last_error


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
