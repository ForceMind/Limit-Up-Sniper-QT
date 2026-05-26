from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict


class AdminDataCacheService:
    def __init__(
        self,
        *,
        app_version: Callable[[], str],
        data_dir: Callable[[], Path],
        cache_env_int: Callable[..., int],
        cache_get: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
        cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        memory_cache_status: Callable[[], Dict[str, Any]],
        memory_cache_clear: Callable[[], None],
        runtime_cache_status: Callable[[], Dict[str, Any]],
        runtime_cache_clear: Callable[..., Dict[str, Any]],
        database_overview: Callable[[], Dict[str, Any]],
        database_table_rows: Callable[..., Dict[str, Any]],
    ) -> None:
        self._app_version = app_version
        self._data_dir = data_dir
        self._cache_env_int = cache_env_int
        self._cache_get = cache_get
        self._cache_set = cache_set
        self._memory_cache_status = memory_cache_status
        self._memory_cache_clear = memory_cache_clear
        self._runtime_cache_status = runtime_cache_status
        self._runtime_cache_clear = runtime_cache_clear
        self._database_overview = database_overview
        self._database_table_rows = database_table_rows

    def database_tables_payload(self) -> Dict[str, Any]:
        cache_ttl = self._cache_env_int("QT_DATABASE_OVERVIEW_CACHE_TTL_SECONDS", 30, minimum=0, maximum=3600)
        cache_parts = {"version": self._app_version(), "data_dir": str(self._data_dir())}
        cached = self._cache_get("admin_database_overview", cache_parts, cache_ttl)
        if cached:
            return cached
        return self._cache_set("admin_database_overview", cache_parts, self._database_overview())

    def database_table_payload(
        self,
        table_name: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        return self._database_table_rows(table_name, limit=limit, offset=offset)

    def cache_status_payload(self) -> Dict[str, Any]:
        payload = self._runtime_cache_status()
        if isinstance(payload, dict):
            payload["memory_cache"] = self._memory_cache_status()
        return payload

    def cache_clear_payload(self, scope: str = "expired") -> Dict[str, Any]:
        scope_text = str(scope or "expired").strip().lower()
        memory_clear_scopes = {"all", "memory", "payload", "expired"}
        memory_cache_cleared = scope_text in memory_clear_scopes
        if memory_cache_cleared:
            self._memory_cache_clear()
        result = self._runtime_cache_clear(scope=scope)
        if isinstance(result, dict):
            result["memory_cache_cleared"] = memory_cache_cleared
        return result
