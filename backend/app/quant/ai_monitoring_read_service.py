from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class AiMonitoringReadService:
    def __init__(
        self,
        *,
        app_version: Callable[[], str],
        cache_env_int: Callable[..., int],
        cache_get: Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]],
        cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        usage_summary: Callable[[], Dict[str, Any]],
        records_feed: Callable[..., Dict[str, Any]],
        failures_feed: Callable[..., Dict[str, Any]],
    ) -> None:
        self._app_version = app_version
        self._cache_env_int = cache_env_int
        self._cache_get = cache_get
        self._cache_set = cache_set
        self._usage_summary = usage_summary
        self._records_feed = records_feed
        self._failures_feed = failures_feed

    def cache_ttl(self) -> int:
        return self._cache_env_int("QT_AI_STATUS_CACHE_TTL_SECONDS", 60, minimum=0, maximum=3600)

    def usage_payload(self) -> Dict[str, Any]:
        ttl = self.cache_ttl()
        parts = {"version": self._app_version()}
        cached = self._cache_get("ai_usage", parts, ttl)
        if cached:
            return cached
        return self._cache_set("ai_usage", parts, self._usage_summary())

    def records_payload(
        self,
        limit: int,
        code: Optional[str],
        source: Optional[str],
    ) -> Dict[str, Any]:
        ttl = self.cache_ttl()
        clean_limit = int(limit or 100)
        parts = {
            "limit": clean_limit,
            "code": code or "",
            "source": source or "",
            "version": self._app_version(),
        }
        cached = self._cache_get("ai_records", parts, ttl)
        if cached:
            return cached
        return self._cache_set(
            "ai_records",
            parts,
            self._records_feed(limit=limit, code=code, source=source),
        )

    def failures_payload(self, limit: int) -> Dict[str, Any]:
        ttl = self.cache_ttl()
        clean_limit = int(limit or 100)
        parts = {"limit": clean_limit, "version": self._app_version()}
        cached = self._cache_get("ai_failures", parts, ttl)
        if cached:
            return cached
        return self._cache_set("ai_failures", parts, self._failures_feed(limit=limit))
