from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional


CacheGet = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
CacheSet = Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
CacheEnvInt = Callable[[str, int], int]
EnvFlag = Callable[[str, bool], bool]


class RuntimeStatusPayloadService:
    def __init__(
        self,
        *,
        app_name: Callable[[], str],
        app_version: Callable[[], str],
        data_dir: Callable[[], Path],
        default_ai_model: Callable[[], str],
        latest_news_time: Callable[[], str],
        data_date_bounds: Callable[[], Dict[str, str]],
        engine_latest_event_date: Callable[[], str],
        frontend_status: Callable[[], Dict[str, Any]],
        jobs_status: Callable[[bool], Dict[str, Any]],
        frontend_account_precompute_queue_status: Callable[[], Dict[str, Any]],
        frontend_account_precompute_async_status: Callable[[], Dict[str, Any]],
        frontend_jobs_cache_env_int: CacheEnvInt,
        cache_get: CacheGet,
        cache_set: CacheSet,
        now: Callable[[], datetime],
    ) -> None:
        self._app_name = app_name
        self._app_version = app_version
        self._data_dir = data_dir
        self._default_ai_model = default_ai_model
        self._latest_news_time = latest_news_time
        self._data_date_bounds = data_date_bounds
        self._engine_latest_event_date = engine_latest_event_date
        self._frontend_status = frontend_status
        self._jobs_status = jobs_status
        self._frontend_account_precompute_queue_status = frontend_account_precompute_queue_status
        self._frontend_account_precompute_async_status = frontend_account_precompute_async_status
        self._frontend_jobs_cache_env_int = frontend_jobs_cache_env_int
        self._cache_get = cache_get
        self._cache_set = cache_set
        self._now = now

    def _base_payload(
        self,
        *,
        data_date: str,
        latest_news_time: str,
        data_bounds: Dict[str, str],
        jobs: Dict[str, Any],
        include_data_dir: bool,
    ) -> Dict[str, Any]:
        now_cn = self._now()
        payload: Dict[str, Any] = {
            "status": "ok",
            "system": "quant",
            "app": self._app_name(),
            "version": self._app_version(),
            "backend_version": self._app_version(),
            "frontend_version": self._app_version(),
            "current_date": now_cn.strftime("%Y-%m-%d"),
            "current_time": now_cn.isoformat(timespec="seconds"),
            "latest_event_date": data_date,
            "latest_news_time": latest_news_time,
            "data_date": data_date,
            "first_data_date": data_bounds.get("first", ""),
            "latest_data_date": data_bounds.get("latest", ""),
            "data_date_bounds": data_bounds,
            "ai_model": self._default_ai_model(),
            "jobs": jobs,
        }
        if include_data_dir:
            payload["data_dir"] = str(self._data_dir())
        return payload

    def status_payload(self) -> Dict[str, Any]:
        latest_news_time = self._latest_news_time()
        data_bounds = self._data_date_bounds()
        data_date = latest_news_time[:10] if latest_news_time else self._engine_latest_event_date()
        return self._base_payload(
            data_date=data_date,
            latest_news_time=latest_news_time,
            data_bounds=data_bounds,
            jobs=self.frontend_light_jobs(self._frontend_status()),
            include_data_dir=True,
        )

    def light_status_payload(
        self,
        *,
        as_of: Optional[str] = None,
        jobs_payload: Optional[Dict[str, Any]] = None,
        include_data_dir: bool = True,
    ) -> Dict[str, Any]:
        latest_news_time = self._latest_news_time()
        data_bounds = self._data_date_bounds()
        data_date = str(as_of or "").strip() or (latest_news_time[:10] if latest_news_time else "")
        jobs = jobs_payload if isinstance(jobs_payload, dict) else {}
        return self._base_payload(
            data_date=data_date,
            latest_news_time=latest_news_time,
            data_bounds=data_bounds,
            jobs=jobs,
            include_data_dir=include_data_dir,
        )

    def frontend_light_jobs(self, jobs_payload: Dict[str, Any]) -> Dict[str, Any]:
        jobs = jobs_payload if isinstance(jobs_payload, dict) else {}
        payload = {
            "scheduler": jobs.get("scheduler", {}),
            "running": jobs.get("running", {}),
            "paused_jobs": jobs.get("paused_jobs", {}),
        }
        strategy_runtime = jobs.get("strategy_runtime")
        if isinstance(strategy_runtime, dict) and strategy_runtime:
            payload["strategy_runtime"] = strategy_runtime
        return payload

    def frontend_jobs_payload(self) -> Dict[str, Any]:
        cache_ttl = self._frontend_jobs_cache_env_int("QT_FRONT_JOBS_CACHE_TTL_SECONDS", 3)
        cache_parts = {"version": self._app_version()}
        cached = self._cache_get("front_jobs", cache_parts, cache_ttl)
        if cached:
            return self.frontend_light_jobs(cached)
        payload = self.frontend_light_jobs(self._frontend_status())
        self._cache_set("front_jobs", cache_parts, payload)
        return payload

    def jobs_status_payload(self, light: bool = True) -> Dict[str, Any]:
        payload = self._jobs_status(light)
        if isinstance(payload, dict):
            payload["frontend_account_precompute_queue"] = self._frontend_account_precompute_queue_status()
            payload["frontend_account_precompute_async"] = self._frontend_account_precompute_async_status()
        return payload


class ServerReadStatusService:
    def __init__(
        self,
        *,
        app_version: Callable[[], str],
        data_dir: Callable[[], Path],
        project_root: Callable[[], Path],
        cache_env_int: CacheEnvInt,
        cache_get: CacheGet,
        cache_set: CacheSet,
        env_flag: EnvFlag,
        latest_sqlite_news_time: Callable[[], str],
        latest_history_news_time: Callable[[], str],
        engine_first_data_date: Callable[[], str],
        engine_latest_event_date: Callable[[], str],
        now: Callable[[], datetime],
    ) -> None:
        self._app_version = app_version
        self._data_dir = data_dir
        self._project_root = project_root
        self._cache_env_int = cache_env_int
        self._cache_get = cache_get
        self._cache_set = cache_set
        self._env_flag = env_flag
        self._latest_sqlite_news_time = latest_sqlite_news_time
        self._latest_history_news_time = latest_history_news_time
        self._engine_first_data_date = engine_first_data_date
        self._engine_latest_event_date = engine_latest_event_date
        self._now = now

    def latest_news_time_uncached(self) -> str:
        try:
            latest = self._latest_sqlite_news_time()
            if latest:
                return str(latest)
        except Exception:
            pass
        try:
            return str(self._latest_history_news_time() or "")
        except Exception:
            return ""

    def latest_news_time(self) -> str:
        cache_ttl = self._cache_env_int("QT_LATEST_NEWS_TIME_CACHE_TTL_SECONDS", 5)
        cache_parts = {"version": self._app_version()}
        cached = self._cache_get("latest_news_time", cache_parts, cache_ttl)
        if cached:
            return str(cached.get("latest_news_time") or "")
        latest = self.latest_news_time_uncached()
        self._cache_set("latest_news_time", cache_parts, {"latest_news_time": latest})
        return latest

    def data_date_bounds_uncached(self) -> Dict[str, str]:
        db_path = self._data_dir() / "quant_data.sqlite3"
        first_dates: list[str] = []
        latest_dates: list[str] = []
        if db_path.exists():
            try:
                conn = sqlite3.connect(db_path)
                try:
                    for table, column in (
                        ("news_events", "date"),
                        ("news_raw", "date"),
                        ("market_daily_bars", "date"),
                        ("lhb_records", "trade_date"),
                    ):
                        try:
                            row = conn.execute(
                                f"SELECT MIN({column}), MAX({column}) FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
                            ).fetchone()
                        except Exception:
                            continue
                        first = str((row or ["", ""])[0] or "").strip()[:10]
                        latest = str((row or ["", ""])[1] or "").strip()[:10]
                        if first:
                            first_dates.append(first)
                        if latest:
                            latest_dates.append(latest)
                finally:
                    conn.close()
            except Exception:
                pass

        first_date = min(first_dates) if first_dates else ""
        latest_date = max(latest_dates) if latest_dates else ""
        allow_engine_fallback = self._env_flag("QT_DATA_DATE_ENGINE_FALLBACK_ENABLED", False)
        if allow_engine_fallback and not first_date:
            try:
                first_date = str(self._engine_first_data_date() or "").strip()[:10]
            except Exception:
                first_date = ""
        if allow_engine_fallback and not latest_date:
            try:
                latest_date = str(self._engine_latest_event_date() or "").strip()[:10]
            except Exception:
                latest_date = ""
        if not latest_date:
            latest_date = self._now().strftime("%Y-%m-%d")
        return {"first": first_date, "latest": latest_date}

    def data_date_bounds(self) -> Dict[str, str]:
        cache_ttl = self._cache_env_int("QT_DATA_DATE_CACHE_TTL_SECONDS", 10)
        cache_parts = {"version": self._app_version(), "data_dir": str(self._data_dir())}
        cached = self._cache_get("data_date_bounds", cache_parts, cache_ttl)
        if cached:
            return {"first": str(cached.get("first") or ""), "latest": str(cached.get("latest") or "")}
        payload = self._cache_set("data_date_bounds", cache_parts, self.data_date_bounds_uncached())
        return {"first": str(payload.get("first") or ""), "latest": str(payload.get("latest") or "")}

    def latest_data_date(self) -> str:
        return str(self.data_date_bounds().get("latest") or "")

    def first_data_date(self) -> str:
        return str(self.data_date_bounds().get("first") or "")

    def git_ref(self) -> Dict[str, str]:
        project_root = self._project_root()
        if not (project_root / ".git").exists():
            return {"branch": "", "commit": "", "ref": ""}
        try:
            branch = subprocess.check_output(
                ["git", "-C", str(project_root), "rev-parse", "--abbrev-ref", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            commit = subprocess.check_output(
                ["git", "-C", str(project_root), "rev-parse", "--short", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            ref = f"{branch}@{commit}" if branch or commit else ""
            return {"branch": branch, "commit": commit, "ref": ref}
        except Exception:
            return {"branch": "", "commit": "", "ref": ""}
