from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional


CacheEnvInt = Callable[..., int]
EnvFlag = Callable[[str, bool], bool]
LoadPayloadCache = Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]
SavePayloadCache = Callable[[str, Dict[str, Any], Dict[str, Any], int], None]
AppendLog = Callable[[str, str, str, str, Dict[str, Any]], None]
LatestPrice = Callable[[str, Optional[str]], Dict[str, Any]]


class FrontendPayloadReadService:
    def __init__(
        self,
        *,
        safe_float: Callable[[Any, float], float],
        followed_model_version: Callable[[Dict[str, Any]], str],
        cache_env_int: CacheEnvInt,
        env_flag: EnvFlag,
        replay_start_date: Callable[[Optional[str]], Optional[str]],
        latest_price: LatestPrice,
        load_payload_cache: LoadPayloadCache,
        save_payload_cache: SavePayloadCache,
        run_frontend_payload_precompute: Callable[..., Dict[str, Any]],
        append_log: AppendLog,
    ) -> None:
        self._safe_float = safe_float
        self._followed_model_version = followed_model_version
        self._cache_env_int = cache_env_int
        self._env_flag = env_flag
        self._replay_start_date = replay_start_date
        self._latest_price = latest_price
        self._load_payload_cache = load_payload_cache
        self._save_payload_cache = save_payload_cache
        self._run_frontend_payload_precompute = run_frontend_payload_precompute
        self._append_log = append_log

    def cache_parts(self, context: Dict[str, Any], payload_type: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
        return {
            "payload_type": payload_type,
            "strategy_model_id": str(profile.get("strategy_model_id") or ""),
            "simulated_cash": round(self._safe_float(profile.get("simulated_cash"), 0), 2),
            "follow_start_date": str(profile.get("follow_start_date") or ""),
            "follow_started_at": str(profile.get("follow_started_at") or ""),
            "model_version": self._followed_model_version(context),
            "strategy_params": context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {},
            **extra,
        }

    def cache_ttl(self, name: str, default: int) -> int:
        return self._cache_env_int(name, default, minimum=0, maximum=86400)

    def precompute_enabled(self) -> bool:
        return self._env_flag("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", False)

    def auto_precompute_on_miss(self) -> bool:
        return self.precompute_enabled() and self._env_flag("QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS", False)

    def sync_compute_enabled(self) -> bool:
        return self._env_flag("QT_FRONT_PAYLOAD_SYNC_COMPUTE_ENABLED", False)

    def deferred_job_response_state(
        self,
        job_result: Dict[str, Any],
        default_message: str,
        cache_state: str = "miss_deferred",
    ) -> tuple[str, str, str]:
        result = job_result if isinstance(job_result, dict) else {}
        status = str(result.get("status") or "").strip().lower()
        message = str(result.get("message") or default_message)
        if status == "busy":
            return "busy", "busy", message
        if status == "paused":
            return "paused", "paused", message
        if status == "disabled":
            return "pending", "disabled", message
        if status in {"error", "failed"}:
            return "error", "error", message
        if status == "running" and not (result.get("background") or result.get("process_pid")):
            return "running", "running", message
        return "pending", cache_state, default_message

    def queue_precompute(
        self,
        context: Dict[str, Any],
        effective_as_of: Optional[str],
        lookback_days: int = 2,
        top_n: int = 30,
        limit_days: int = 30,
        force: bool = False,
    ) -> Dict[str, Any]:
        username = str(context.get("username") or "").strip()
        auto_on_miss = self.auto_precompute_on_miss()
        if not auto_on_miss:
            return {
                "status": "disabled",
                "job": "frontend_payload_precompute",
                "background": False,
                "process": False,
                "queued": False,
                "message": "缓存未命中自动预计算已关闭，请在后台手动预计算前台缓存。",
                "frontend_payload_precompute_enabled": self.precompute_enabled(),
                "frontend_payload_auto_precompute_on_miss": auto_on_miss,
            }
        try:
            return self._run_frontend_payload_precompute(
                as_of=effective_as_of,
                usernames=[username] if username else None,
                limit_users=1
                if username
                else self._cache_env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS", 8, minimum=1, maximum=500),
                force=force,
                background=True,
                process=self._env_flag("QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED", True),
                lookback_days=lookback_days,
                top_n=top_n,
                limit_days=limit_days,
                max_seconds=self._cache_env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS", 20, minimum=0, maximum=86400),
            )
        except Exception as exc:
            self._append_log(
                "warning",
                f"前台推荐和日计划预计算排队失败：{exc}",
                "frontend_payload_precompute",
                "queue",
                {},
            )
            return {"status": "error", "message": str(exc)}

    def pending_payload(
        self,
        payload_type: str,
        effective_as_of: Optional[str],
        job_result: Dict[str, Any],
        **extra: Any,
    ) -> Dict[str, Any]:
        default_message = "缓存未命中，后台正在预计算，请稍后刷新。"
        status, cache_state, message = self.deferred_job_response_state(
            job_result,
            default_message,
            cache_state="queued",
        )
        result = job_result if isinstance(job_result, dict) else {}
        payload: Dict[str, Any] = {
            "status": status,
            "as_of": effective_as_of,
            "frontend_payload_cache": cache_state,
            "frontend_payload_job": {
                "status": result.get("status"),
                "job": result.get("job"),
                "background": bool(result.get("background") or result.get("process")),
                "message": result.get("message"),
                "queued": bool(result.get("queued", status == "pending" and cache_state == "queued")),
                "frontend_payload_precompute_enabled": result.get("frontend_payload_precompute_enabled"),
                "frontend_payload_auto_precompute_on_miss": result.get("frontend_payload_auto_precompute_on_miss"),
            },
            "message": message,
            **extra,
        }
        if payload_type == "front_recommendations":
            payload.setdefault("items", [])
        if payload_type == "front_daily_plan":
            payload.setdefault("buy_list", [])
            payload.setdefault("sell_list", [])
            payload.setdefault("hold_list", [])
        return payload

    def cached_recommendations_and_plan(
        self,
        context: Dict[str, Any],
        effective_as_of: Optional[str],
        top_n: int,
        limit_days: int,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        rec_ttl = self.cache_ttl("QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS", 1800)
        plan_ttl = self.cache_ttl("QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS", 1800)
        effective_start = self._replay_start_date(effective_as_of)
        rec_parts = self.cache_parts(
            context,
            "front_recommendations",
            {"as_of": effective_as_of, "lookback_days": 2, "top_n": top_n},
        )
        plan_parts = self.cache_parts(
            context,
            "front_daily_plan",
            {"as_of": effective_as_of, "start_date": effective_start, "limit_days": limit_days},
        )
        recommendations = self._load_payload_cache("front_recommendations", rec_parts, rec_ttl)
        daily_plan = self._load_payload_cache("front_daily_plan", plan_parts, plan_ttl)
        if recommendations and daily_plan:
            return recommendations, daily_plan
        job_result = self.queue_precompute(
            context,
            effective_as_of,
            lookback_days=2,
            top_n=top_n,
            limit_days=limit_days,
        )
        if not recommendations:
            recommendations = self.pending_payload(
                "front_recommendations",
                effective_as_of,
                job_result,
                lookback_days=2,
                top_n=top_n,
            )
        if not daily_plan:
            daily_plan = self.pending_payload(
                "front_daily_plan",
                effective_as_of,
                job_result,
                start_date=effective_start,
                limit_days=limit_days,
            )
        return recommendations, daily_plan

    def affordable_payload(
        self,
        payload: Dict[str, Any],
        context: Dict[str, Any],
        as_of: Optional[str],
    ) -> Dict[str, Any]:
        profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
        params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
        cash = self._safe_float(profile.get("simulated_cash"), params.get("account_initial_cash", 0))
        max_positions = max(1.0, self._safe_float(params.get("max_positions"), 1))
        position_cash = min(
            self._safe_float(params.get("paper_position_value"), cash),
            cash / max_positions if max_positions else cash,
        )
        if cash <= 0:
            return payload

        def enrich(item: Dict[str, Any]) -> Dict[str, Any]:
            row = dict(item)
            code = str(row.get("code") or "").strip()
            price = self._safe_float(row.get("price") or row.get("current") or row.get("close"), 0)
            if price <= 0 and code:
                latest = self._latest_price(code, as_of)
                price = self._safe_float((latest or {}).get("close"), 0)
            lot_amount = price * 100 if price > 0 else 0.0
            max_qty = math.floor(position_cash / price / 100) * 100 if price > 0 else 0
            affordable = bool(price > 0 and max_qty >= 100 and lot_amount <= cash)
            row["estimated_price"] = round(price, 3) if price > 0 else 0.0
            row["min_lot_amount"] = round(lot_amount, 2)
            row["max_buy_qty"] = int(max_qty)
            row["affordable"] = affordable
            if not affordable:
                row["capital_note"] = "insufficient simulated cash for one lot" if price > 0 else "missing price for lot estimate"
            elif cash <= 50_000:
                row["capital_note"] = "small cash account can buy one lot"
            return row

        next_payload = dict(payload)
        for key in ("items", "buy_list"):
            values = payload.get(key)
            if isinstance(values, list):
                enriched = [enrich(item) if isinstance(item, dict) else item for item in values]
                if cash <= 50_000:
                    enriched.sort(
                        key=lambda item: (
                            0 if isinstance(item, dict) and item.get("affordable") else 1,
                            -self._safe_float(item.get("buy_score"), 0) if isinstance(item, dict) else 0,
                        )
                    )
                next_payload[key] = enriched
        next_payload["capital_filter"] = {
            "simulated_cash": round(cash, 2),
            "position_cash": round(position_cash, 2),
            "max_positions": int(max_positions),
            "small_cash_mode": cash <= 50_000,
        }
        if isinstance(params, dict):
            current_params = next_payload.get("strategy_params") if isinstance(next_payload.get("strategy_params"), dict) else {}
            next_payload["strategy_params"] = {**current_params, **params}
        return next_payload

    def recommendations_payload(
        self,
        *,
        context: Dict[str, Any],
        effective_as_of: Optional[str],
        lookback_days: int,
        top_n: int,
        force: bool,
        defer: bool,
        compute: Callable[[], Dict[str, Any]],
        affordable_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        cache_parts = self.cache_parts(
            context,
            "front_recommendations",
            {
                "as_of": effective_as_of,
                "lookback_days": lookback_days,
                "top_n": top_n,
            },
        )
        ttl = self.cache_ttl("QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS", 1800)
        cached = None if force else self._load_payload_cache("front_recommendations", cache_parts, ttl)
        if cached:
            return cached
        if (defer and not force) or not self.sync_compute_enabled():
            job_result = self.queue_precompute(
                context,
                effective_as_of,
                lookback_days=lookback_days,
                top_n=top_n,
                limit_days=120,
            )
            return self.pending_payload(
                "front_recommendations",
                effective_as_of,
                job_result,
                lookback_days=lookback_days,
                top_n=top_n,
            )
        payload = affordable_payload(compute())
        payload["frontend_payload_cache"] = "miss"
        self._save_payload_cache("front_recommendations", cache_parts, payload, ttl)
        return payload

    def daily_plan_payload(
        self,
        *,
        context: Dict[str, Any],
        effective_as_of: Optional[str],
        effective_start: Optional[str],
        limit_days: int,
        force: bool,
        defer: bool,
        compute: Callable[[], Dict[str, Any]],
        affordable_payload: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        cache_parts = self.cache_parts(
            context,
            "front_daily_plan",
            {
                "as_of": effective_as_of,
                "start_date": effective_start,
                "limit_days": limit_days,
            },
        )
        ttl = self.cache_ttl("QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS", 1800)
        cached = None if force else self._load_payload_cache("front_daily_plan", cache_parts, ttl)
        if cached:
            return cached
        if (defer and not force) or not self.sync_compute_enabled():
            job_result = self.queue_precompute(
                context,
                effective_as_of,
                lookback_days=2,
                top_n=30,
                limit_days=limit_days,
            )
            return self.pending_payload(
                "front_daily_plan",
                effective_as_of,
                job_result,
                start_date=effective_start,
                limit_days=limit_days,
            )
        payload = affordable_payload(compute())
        payload["frontend_payload_cache"] = "miss"
        self._save_payload_cache("front_daily_plan", cache_parts, payload, ttl)
        return payload
