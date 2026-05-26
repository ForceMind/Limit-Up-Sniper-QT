from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional


NewsStablePayload = Callable[[str, bool, bool, int], Dict[str, Any]]
StrategyDailyPayload = Callable[[Mapping[str, Any], str, int, Mapping[str, Any]], Dict[str, Any]]
TradingAccountPayload = Callable[[Mapping[str, Any], str, bool], Dict[str, Any]]
RecommendationsPlanPayload = Callable[[Mapping[str, Any], str, int, int], tuple[Dict[str, Any], Dict[str, Any]]]
WarningLogger = Callable[[str, Exception], None]
CacheGet = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
CacheSet = Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_frontend_snapshot_stable_payload(
    *,
    context: Mapping[str, Any],
    effective_as_of: str,
    mobile: bool,
    light: bool,
    catalog_included: bool,
    news_limit: int,
    top_n: int,
    news_stable_payload: NewsStablePayload,
    strategy_daily_payload: StrategyDailyPayload,
    trading_account_payload: TradingAccountPayload,
    recommendations_plan_payload: Optional[RecommendationsPlanPayload] = None,
    log_warning: Optional[WarningLogger] = None,
) -> Dict[str, Any]:
    news_stable = _safe_dict(news_stable_payload(effective_as_of, bool(mobile), bool(light), int(news_limit)))
    stable: Dict[str, Any] = {
        "frontend_profile": _safe_dict(context.get("profile")),
        "followed_model": _safe_dict(context.get("followed_model")),
        "strategy_models": _safe_dict(context.get("models_payload")),
        "strategy_catalog_included": bool(catalog_included),
        **news_stable,
    }

    try:
        strategy_daily = _safe_dict(strategy_daily_payload(context, effective_as_of, int(news_limit), news_stable))
    except Exception as exc:
        if log_warning:
            log_warning("strategy_daily", exc)
        strategy_daily = {}
    if strategy_daily:
        stable["strategy_daily"] = strategy_daily

    try:
        trading_account = _safe_dict(trading_account_payload(context, effective_as_of, bool(light)))
    except Exception as exc:
        if log_warning:
            log_warning("account", exc)
        trading_account = {}
    if trading_account:
        stable["trading_account"] = trading_account

    if not light and recommendations_plan_payload:
        try:
            recommendations, daily_plan = recommendations_plan_payload(context, effective_as_of, int(top_n), 120)
        except Exception as exc:
            if log_warning:
                log_warning("recommendations_plan", exc)
            recommendations, daily_plan = {}, {}
        recommendations = _safe_dict(recommendations)
        daily_plan = _safe_dict(daily_plan)
        if recommendations:
            stable["recommendations"] = recommendations
        if daily_plan:
            stable["daily_plan"] = daily_plan

    return stable


class FrontendSnapshotReadService:
    def __init__(
        self,
        *,
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        app_version: Callable[[], str],
        cache_env_int: Callable[..., int],
        env_flag: Callable[[str, bool], bool],
        cache_get: CacheGet,
        cache_set: CacheSet,
        frontend_jobs_payload: Callable[[], Dict[str, Any]],
        light_status_payload: Callable[..., Dict[str, Any]],
        frontend_profile_context: Callable[..., Dict[str, Any]],
        frontend_payload_cache_parts: Callable[[Dict[str, Any], str, Dict[str, Any]], Dict[str, Any]],
        frontend_light_news_feed: Callable[..., Dict[str, Any]],
        safe_news_feed: Callable[..., Dict[str, Any]],
        market_sentiment: Callable[[Dict[str, Any]], Dict[str, Any]],
        strategy_daily_payload: StrategyDailyPayload,
        trading_account_payload: TradingAccountPayload,
        recommendations_plan_payload: RecommendationsPlanPayload,
        attach_account_precompute: Callable[[Dict[str, Any], Dict[str, Any], Optional[str], str], Dict[str, Any]],
        copy_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
        append_log: Callable[[str, str, str, str], None],
    ) -> None:
        self._resolve_as_of = resolve_as_of
        self._app_version = app_version
        self._cache_env_int = cache_env_int
        self._env_flag = env_flag
        self._cache_get = cache_get
        self._cache_set = cache_set
        self._frontend_jobs_payload = frontend_jobs_payload
        self._light_status_payload = light_status_payload
        self._frontend_profile_context = frontend_profile_context
        self._frontend_payload_cache_parts = frontend_payload_cache_parts
        self._frontend_light_news_feed = frontend_light_news_feed
        self._safe_news_feed = safe_news_feed
        self._market_sentiment = market_sentiment
        self._strategy_daily_payload = strategy_daily_payload
        self._trading_account_payload = trading_account_payload
        self._recommendations_plan_payload = recommendations_plan_payload
        self._attach_account_precompute = attach_account_precompute
        self._copy_payload = copy_payload
        self._append_log = append_log

    def news_stable_payload(
        self,
        effective_as_of: Optional[str],
        mobile: bool,
        light: bool,
        news_limit: int,
    ) -> Dict[str, Any]:
        cache_parts = {
            "as_of": effective_as_of,
            "mobile": bool(mobile),
            "light": bool(light),
            "news_limit": int(news_limit),
            "version": self._app_version(),
        }
        cache_ttl = self._cache_env_int("QT_FRONT_SNAPSHOT_NEWS_CACHE_TTL_SECONDS", 30, minimum=0, maximum=3600)
        cached = self._cache_get("front_snapshot_news", cache_parts, cache_ttl)
        if cached:
            return cached
        if light and self._env_flag("QT_FRONT_SNAPSHOT_LIGHT_NEWS_NO_ENGINE_FALLBACK", True):
            news_payload = self._frontend_light_news_feed(as_of=effective_as_of, limit=news_limit, fallback_latest=True)
        else:
            news_payload = self._safe_news_feed(as_of=effective_as_of, limit=news_limit, fallback_latest=True)
        return self._cache_set(
            "front_snapshot_news",
            cache_parts,
            {
                "news": news_payload,
                "market_sentiment": self._market_sentiment(news_payload),
            },
        )

    def public_snapshot_payload(
        self,
        as_of: Optional[str] = None,
        mobile: bool = False,
        light: bool = True,
    ) -> Dict[str, Any]:
        news_limit = 12 if mobile or light else 80
        light_jobs = self._frontend_jobs_payload()
        effective_as_of = self._resolve_as_of(as_of)
        cache_parts = {
            "as_of": effective_as_of,
            "mobile": bool(mobile),
            "light": bool(light),
            "news_limit": news_limit,
            "version": self._app_version(),
        }
        cache_ttl = self._cache_env_int("QT_PUBLIC_SNAPSHOT_CACHE_TTL_SECONDS", 30, minimum=0, maximum=3600)
        stable = self._cache_get("front_public_snapshot", cache_parts, cache_ttl)
        if not stable:
            stable = self._cache_set(
                "front_public_snapshot",
                cache_parts,
                self.news_stable_payload(effective_as_of, bool(mobile), bool(light), news_limit),
            )
        return {
            "status": "ok",
            "status_payload": self._light_status_payload(
                as_of=effective_as_of,
                jobs_payload=light_jobs,
                include_data_dir=False,
            ),
            "jobs": light_jobs,
            **stable,
        }

    def snapshot_payload(
        self,
        request: Any,
        as_of: Optional[str] = None,
        mobile: bool = False,
        light: bool = True,
        include_catalog: bool = False,
    ) -> Dict[str, Any]:
        news_limit = 12 if mobile or light else 80
        top_n = 12 if mobile else 30
        visible_jobs = self._frontend_jobs_payload()
        effective_as_of = self._resolve_as_of(as_of)
        catalog_included = bool(include_catalog or not light)
        context = self._frontend_profile_context(request, include_catalog=catalog_included)
        cache_parts = self._frontend_payload_cache_parts(
            context,
            "front_snapshot",
            {
                "as_of": effective_as_of,
                "mobile": bool(mobile),
                "light": bool(light),
                "news_limit": news_limit,
                "top_n": top_n,
                "include_catalog": catalog_included,
                "version": self._app_version(),
            },
        )
        cache_ttl = self._cache_env_int("QT_FRONT_SNAPSHOT_CACHE_TTL_SECONDS", 45, minimum=0, maximum=3600)
        stable = self._cache_get("front_snapshot", cache_parts, cache_ttl)
        if not stable:
            stable = build_frontend_snapshot_stable_payload(
                context=context,
                effective_as_of=effective_as_of,
                mobile=bool(mobile),
                light=bool(light),
                catalog_included=catalog_included,
                news_limit=news_limit,
                top_n=top_n,
                news_stable_payload=self.news_stable_payload,
                strategy_daily_payload=self._strategy_daily_payload,
                trading_account_payload=self._trading_account_payload,
                recommendations_plan_payload=self._recommendations_plan_payload,
                log_warning=lambda stage, exc: self._append_log(
                    "warning",
                    f"frontend snapshot {stage} failed: {exc}",
                    "frontend_snapshot",
                    stage,
                ),
            )
            stable = self._cache_set("front_snapshot", cache_parts, stable)
        if isinstance(stable.get("trading_account"), dict):
            stable = dict(stable)
            stable["trading_account"] = self._attach_account_precompute(
                self._copy_payload(stable["trading_account"]),
                context,
                effective_as_of,
                "account_runtime_missing",
            )
        return {
            "status": "ok",
            "status_payload": self._light_status_payload(
                as_of=effective_as_of,
                jobs_payload=visible_jobs,
                include_data_dir=False,
            ),
            "jobs": visible_jobs,
            **stable,
        }
