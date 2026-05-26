from __future__ import annotations

from typing import Any, Callable, Dict, Optional


Payload0 = Callable[[], Dict[str, Any]]
Payload1 = Callable[..., Dict[str, Any]]
CacheGet = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
CacheSet = Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
EnvFlag = Callable[[str, bool], bool]


class AdminSnapshotService:
    def __init__(
        self,
        *,
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        app_version: Callable[[], str],
        cache_env_int: Callable[..., int],
        env_flag: EnvFlag,
        cache_get: CacheGet,
        cache_set: CacheSet,
        jobs_status_payload: Callable[[bool], Dict[str, Any]],
        status_payload: Payload0,
        light_status_payload: Callable[..., Dict[str, Any]],
        safe_news_feed: Payload1,
        full_news_feed: Payload1,
        strategy_models_payload: Callable[..., Dict[str, Any]],
        admin_model_signal_feed: Payload1,
        strategy_runtime_overview_payload: Payload1,
        light_dashboard_payload: Payload1,
        dashboard_payload: Payload1,
        frontend_user_summary: Payload0,
        admin_frontend_user_summary: Payload0,
        trading_account_payload: Payload1,
        data_coverage_payload: Payload1,
        market_sentiment: Callable[[Dict[str, Any]], Dict[str, Any]],
        biying_status: Payload0,
        lhb_status: Payload0,
        notification_status: Payload0,
        evolution_status: Payload0,
        ai_usage_summary: Payload0,
        ai_failures: Payload1,
        ai_records_feed: Payload1,
        access_logs: Payload1,
        strategy_params: Payload0,
        strategy_source: Payload0,
        append_log: Callable[[str, str, str, str], None],
    ) -> None:
        self._resolve_as_of = resolve_as_of
        self._app_version = app_version
        self._cache_env_int = cache_env_int
        self._env_flag = env_flag
        self._cache_get = cache_get
        self._cache_set = cache_set
        self._jobs_status_payload = jobs_status_payload
        self._status_payload = status_payload
        self._light_status_payload = light_status_payload
        self._safe_news_feed = safe_news_feed
        self._full_news_feed = full_news_feed
        self._strategy_models_payload = strategy_models_payload
        self._admin_model_signal_feed = admin_model_signal_feed
        self._strategy_runtime_overview_payload = strategy_runtime_overview_payload
        self._light_dashboard_payload = light_dashboard_payload
        self._dashboard_payload = dashboard_payload
        self._frontend_user_summary = frontend_user_summary
        self._admin_frontend_user_summary = admin_frontend_user_summary
        self._trading_account_payload = trading_account_payload
        self._data_coverage_payload = data_coverage_payload
        self._market_sentiment = market_sentiment
        self._biying_status = biying_status
        self._lhb_status = lhb_status
        self._notification_status = notification_status
        self._evolution_status = evolution_status
        self._ai_usage_summary = ai_usage_summary
        self._ai_failures = ai_failures
        self._ai_records_feed = ai_records_feed
        self._access_logs = access_logs
        self._strategy_params = strategy_params
        self._strategy_source = strategy_source
        self._append_log = append_log

    def _dashboard_error_payload(self, effective_as_of: Optional[str], error: Exception) -> Dict[str, Any]:
        return {
            "status": "ok",
            "as_of": effective_as_of,
            "strategy_params": self._strategy_params(),
            "strategy_source": self._strategy_source(),
            "recommendations": {"status": "error", "items": [], "latest_events": [], "error": str(error)},
            "timeline": {},
        }

    def _light_stable_payload(self, effective_as_of: Optional[str]) -> Dict[str, Any]:
        cache_parts = {"as_of": effective_as_of, "version": self._app_version()}
        cache_ttl = self._cache_env_int("QT_ADMIN_SNAPSHOT_CACHE_TTL_SECONDS", 20, minimum=0, maximum=3600)
        stable = self._cache_get("admin_snapshot_light", cache_parts, cache_ttl)
        if stable:
            return stable
        news_payload = self._safe_news_feed(as_of=effective_as_of, limit=60, fallback_latest=True)
        models_payload = self._strategy_models_payload(include_catalog=True)
        model_signals = self._admin_model_signal_feed(
            effective_as_of,
            models_payload=models_payload,
            limit_models=24,
            limit_per_model=12,
        )
        strategy_runtime_overview = self._strategy_runtime_overview_payload(
            effective_as_of,
            models_payload=models_payload,
            signal_feed=model_signals,
        )
        try:
            dashboard = self._light_dashboard_payload(
                effective_as_of,
                news_payload=news_payload,
                model_signals=model_signals,
            )
        except Exception as exc:
            self._append_log("warning", f"admin light snapshot dashboard failed: {exc}", "admin_snapshot", "dashboard")
            dashboard = self._dashboard_error_payload(effective_as_of, exc)
        return self._cache_set(
            "admin_snapshot_light",
            cache_parts,
            {
                "strategy_models": models_payload,
                "frontend_users": self._frontend_user_summary(),
                "dashboard": dashboard,
                "model_signals": model_signals,
                "strategy_runtime_overview": strategy_runtime_overview,
                "news": news_payload,
                "market_sentiment": self._market_sentiment(news_payload),
            },
        )

    def _light_payload(self, effective_as_of: Optional[str]) -> Dict[str, Any]:
        jobs_payload = self._jobs_status_payload(True)
        stable = self._light_stable_payload(effective_as_of)
        return {
            "status": "ok",
            "status_payload": self._light_status_payload(as_of=effective_as_of, jobs_payload=jobs_payload),
            "jobs": jobs_payload,
            "biying": self._biying_status(),
            "lhb": self._lhb_status(),
            "notification_status": self._notification_status(),
            "evolution_status": self._evolution_status(),
            **stable,
        }

    def _full_payload(self, effective_as_of: Optional[str]) -> Dict[str, Any]:
        models_payload = self._strategy_models_payload(include_catalog=True)
        news_payload = self._full_news_feed(as_of=effective_as_of, limit=120, fallback_latest=True)
        return {
            "status": "ok",
            "status_payload": self._status_payload(),
            "jobs": self._jobs_status_payload(True),
            "biying": self._biying_status(),
            "lhb": self._lhb_status(),
            "ai_usage": self._ai_usage_summary(),
            "notification_status": self._notification_status(),
            "evolution_status": self._evolution_status(),
            "strategy_models": models_payload,
            "access_logs": self._access_logs(limit=120),
            "frontend_users": self._admin_frontend_user_summary(),
            "dashboard": self._dashboard_payload(as_of=effective_as_of, include_heavy=False),
            "trading_account": self._trading_account_payload(as_of=effective_as_of, limit=1000),
            "model_signals": self._admin_model_signal_feed(
                effective_as_of,
                models_payload=models_payload,
                limit_models=32,
                limit_per_model=20,
            ),
            "strategy_runtime_overview": self._strategy_runtime_overview_payload(
                effective_as_of,
                models_payload=models_payload,
            ),
            "news": news_payload,
            "coverage": self._data_coverage_payload(
                as_of=effective_as_of,
                top_n=100,
                defer=self._env_flag("QT_DATA_COVERAGE_DEFER_MISSES", True),
            ),
            "ai_failures": self._ai_failures(limit=40),
            "ai_records": self._ai_records_feed(limit=80),
        }

    def payload(self, as_of: Optional[str] = None, light: bool = True) -> Dict[str, Any]:
        effective_as_of = self._resolve_as_of(as_of)
        if light:
            return self._light_payload(effective_as_of)
        return self._full_payload(effective_as_of)
