from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from app.quant.engine_utils import safe_float


CacheLoad = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
CacheSave = Callable[[str, Dict[str, Any], Dict[str, Any], int], Any]
CoverageBuilder = Callable[..., Dict[str, Any]]
DeferredJobState = Callable[[Dict[str, Any], str], tuple[str, str, str]]
EnvFlag = Callable[[str, bool], bool]
MemoryCacheGet = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
MemoryCacheSet = Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
ResolveAsOf = Callable[[Optional[str]], Optional[str]]


class DataCoverageService:
    def __init__(
        self,
        *,
        data_coverage: CoverageBuilder,
        job_manager: Any,
        load_payload_cache: CacheLoad,
        save_payload_cache: CacheSave,
        memory_cache_get: MemoryCacheGet,
        memory_cache_set: MemoryCacheSet,
        cache_env_int: Callable[..., int],
        env_flag: EnvFlag,
        resolve_as_of: ResolveAsOf,
        deferred_job_response_state: DeferredJobState,
        app_version: Callable[[], str],
    ) -> None:
        self._data_coverage = data_coverage
        self._job_manager = job_manager
        self._load_payload_cache = load_payload_cache
        self._save_payload_cache = save_payload_cache
        self._memory_cache_get = memory_cache_get
        self._memory_cache_set = memory_cache_set
        self._cache_env_int = cache_env_int
        self._env_flag = env_flag
        self._resolve_as_of = resolve_as_of
        self._deferred_job_response_state = deferred_job_response_state
        self._app_version = app_version

    @staticmethod
    def top_n(top_n: int) -> int:
        return max(1, min(int(top_n or 80), 300))

    def cache_parts(self, effective_as_of: Optional[str], top_n: int) -> Dict[str, Any]:
        return {
            "as_of": effective_as_of,
            "top_n": self.top_n(top_n),
            "version": self._app_version(),
        }

    def cache_ttl(self) -> int:
        return self._cache_env_int("QT_DATA_COVERAGE_CACHE_TTL_SECONDS", 60, minimum=0, maximum=3600)

    @classmethod
    def compact_result(cls, payload: Dict[str, Any], top_n: int) -> Dict[str, Any]:
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        daily = payload.get("daily_coverage") if isinstance(payload.get("daily_coverage"), dict) else {}
        minute = payload.get("minute_coverage") if isinstance(payload.get("minute_coverage"), dict) else {}
        lhb = payload.get("lhb") if isinstance(payload.get("lhb"), dict) else {}
        return {
            "status": payload.get("status") or "ok",
            "job": "data_coverage",
            "as_of": payload.get("as_of") or "",
            "top_n": cls.top_n(top_n),
            "target_count": int(safe_float(summary.get("target_count"), 0)),
            "daily_ratio": safe_float(daily.get("ratio"), 0),
            "minute_ratio": safe_float(minute.get("ratio"), 0),
            "lhb_rows": int(safe_float(lhb.get("rows"), safe_float(summary.get("lhb_rows"), 0))),
            "latest_lhb_date": lhb.get("latest_date") or summary.get("latest_lhb_date") or "",
            "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        }

    def compute_cached(self, effective_as_of: Optional[str], top_n: int) -> Dict[str, Any]:
        clean_top_n = self.top_n(top_n)
        cache_parts = self.cache_parts(effective_as_of, clean_top_n)
        payload = self._data_coverage(as_of=effective_as_of, top_n=clean_top_n)
        if isinstance(payload, dict):
            payload["data_coverage_cache"] = "refresh"
            payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            self._save_payload_cache("data_coverage", cache_parts, payload, self.cache_ttl())
            return self._memory_cache_set("data_coverage", cache_parts, payload)
        return payload

    def queue_precompute(self, effective_as_of: Optional[str], top_n: int, process: bool = True) -> Dict[str, Any]:
        clean_top_n = self.top_n(top_n)
        payload = {"as_of": effective_as_of, "top_n": clean_top_n}
        if process:
            return self._job_manager.run_job_process(
                "data_coverage",
                payload=payload,
                message="数据覆盖率统计已转入独立进程运行",
            )

        def execute() -> Dict[str, Any]:
            result = self.compute_cached(effective_as_of, clean_top_n)
            return self.compact_result(result if isinstance(result, dict) else {}, clean_top_n)

        return self._job_manager.run_job_background(
            "data_coverage",
            execute,
            payload=payload,
            message="数据覆盖率统计已转入后台运行",
        )

    def pending(self, effective_as_of: Optional[str], top_n: int, job_result: Dict[str, Any]) -> Dict[str, Any]:
        clean_top_n = self.top_n(top_n)
        status, cache_state, message = self._deferred_job_response_state(
            job_result,
            "数据覆盖率统计正在后台生成，请稍后刷新。",
        )
        return {
            "status": status,
            "as_of": effective_as_of,
            "top_n": clean_top_n,
            "data_coverage_cache": cache_state,
            "message": message,
            "summary": {"target_count": 0},
            "daily_coverage": {"covered": 0, "missing": 0, "ratio": 0},
            "minute_coverage": {"covered": 0, "missing": 0, "ratio": 0},
            "minute_cache_dates": [],
            "recent_event_dates": {},
            "news": {},
            "ai": {},
            "biying": {},
            "lhb": {},
            "targets": [],
            "job_result": job_result,
        }

    def payload(
        self,
        *,
        as_of: Optional[str] = None,
        top_n: int = 80,
        force: bool = False,
        defer: bool = True,
        process: Optional[bool] = None,
    ) -> Dict[str, Any]:
        effective_as_of = self._resolve_as_of(as_of)
        clean_top_n = self.top_n(top_n)
        cache_parts = self.cache_parts(effective_as_of, clean_top_n)
        ttl = self.cache_ttl()
        cached = None if force else (
            self._load_payload_cache("data_coverage", cache_parts, ttl)
            or self._memory_cache_get("data_coverage", cache_parts, ttl)
        )
        if cached:
            cached["data_coverage_cache"] = "hit"
            return cached
        if defer and not force:
            use_process = self._env_flag("QT_DATA_COVERAGE_PROCESS_ENABLED", True) if process is None else bool(process)
            job_result = self.queue_precompute(effective_as_of, clean_top_n, process=use_process)
            return self.pending(effective_as_of, clean_top_n, job_result)
        return self.compute_cached(effective_as_of, clean_top_n)

    def route_payload(
        self,
        as_of: Optional[str],
        top_n: int,
        force: bool,
        defer: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self.payload(
            as_of=as_of,
            top_n=top_n,
            force=force,
            defer=defer,
            process=process,
        )
