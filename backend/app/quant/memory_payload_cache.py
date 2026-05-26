from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any, Callable, Dict, Optional


def copy_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return copy.deepcopy(payload)
    except Exception:
        return dict(payload)


class MemoryPayloadCache:
    def __init__(self, *, max_rows: int = 256, clock: Callable[[], float] = time.time) -> None:
        self.max_rows = max(1, int(max_rows or 1))
        self.rows: Dict[str, tuple[float, Dict[str, Any]]] = {}
        self._clock = clock

    def key(self, payload_type: str, parts: Dict[str, Any]) -> str:
        text = json.dumps({"type": payload_type, "parts": parts}, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        return f"{payload_type}:{digest}"

    def get(self, payload_type: str, parts: Dict[str, Any], ttl_seconds: int) -> Optional[Dict[str, Any]]:
        ttl_seconds = max(0, int(ttl_seconds or 0))
        if ttl_seconds <= 0:
            return None
        key = self.key(payload_type, parts)
        cached = self.rows.get(key)
        if not cached:
            return None
        created_at, payload = cached
        if self._clock() - created_at > ttl_seconds:
            self.rows.pop(key, None)
            return None
        result = copy_payload(payload)
        result["server_cache"] = "hit"
        result["server_cache_type"] = payload_type
        return result

    def set(self, payload_type: str, parts: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        if len(self.rows) >= self.max_rows:
            oldest = sorted(self.rows.items(), key=lambda item: item[1][0])[: max(1, self.max_rows // 8)]
            for key, _item in oldest:
                self.rows.pop(key, None)
        key = self.key(payload_type, parts)
        clean = copy_payload(payload)
        clean.pop("server_cache", None)
        clean.pop("server_cache_type", None)
        self.rows[key] = (self._clock(), clean)
        result = copy_payload(payload)
        result["server_cache"] = "miss"
        result["server_cache_type"] = payload_type
        return result

    def clear(self, payload_types: Optional[Any] = None) -> None:
        if payload_types is None:
            self.rows.clear()
            return
        if isinstance(payload_types, str):
            target_types = {payload_types}
        else:
            try:
                target_types = {str(item) for item in payload_types if str(item)}
            except Exception:
                target_types = {str(payload_types)}
        if not target_types:
            return
        prefixes = tuple(f"{item}:" for item in target_types)
        for key in list(self.rows.keys()):
            if str(key).startswith(prefixes):
                self.rows.pop(key, None)

    def status(self) -> Dict[str, int]:
        return {
            "row_count": len(self.rows),
            "max_rows": self.max_rows,
        }
