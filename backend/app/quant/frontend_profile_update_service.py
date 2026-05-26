from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


class FrontendProfileUpdateService:
    def __init__(self) -> None:
        self._request_username: Optional[Callable[[Any], str]] = None
        self._load_profile: Optional[Callable[[str], Dict[str, Any]]] = None
        self._resolve_updates: Optional[Callable[..., tuple[Dict[str, Any], Dict[str, Any]]]] = None
        self._strategy_models_payload: Optional[Callable[..., Dict[str, Any]]] = None
        self._model_lookup: Optional[Callable[..., Dict[str, Any]]] = None
        self._save_profile: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None
        self._profile_context: Optional[Callable[..., Dict[str, Any]]] = None
        self._follow_period_reason: Optional[Callable[[Any, Any], str]] = None
        self._queue_follow_period: Optional[Callable[..., Dict[str, Any]]] = None
        self._queue_account_precompute: Optional[Callable[..., Dict[str, Any]]] = None

    def configure_route(
        self,
        *,
        request_username: Callable[[Any], str],
        load_profile: Callable[[str], Dict[str, Any]],
        resolve_updates: Callable[..., tuple[Dict[str, Any], Dict[str, Any]]],
        strategy_models_payload: Callable[..., Dict[str, Any]],
        model_lookup: Callable[..., Dict[str, Any]],
        save_profile: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        profile_context: Callable[..., Dict[str, Any]],
        follow_period_reason: Callable[[Any, Any], str],
        queue_follow_period: Callable[..., Dict[str, Any]],
        queue_account_precompute: Callable[..., Dict[str, Any]],
    ) -> None:
        self._request_username = request_username
        self._load_profile = load_profile
        self._resolve_updates = resolve_updates
        self._strategy_models_payload = strategy_models_payload
        self._model_lookup = model_lookup
        self._save_profile = save_profile
        self._profile_context = profile_context
        self._follow_period_reason = follow_period_reason
        self._queue_follow_period = queue_follow_period
        self._queue_account_precompute = queue_account_precompute

    def update_profile_payload(
        self,
        request: Any,
        payload: Dict[str, Any],
        include_catalog: bool,
    ) -> Dict[str, Any]:
        if (
            self._request_username is None
            or self._load_profile is None
            or self._resolve_updates is None
            or self._strategy_models_payload is None
            or self._model_lookup is None
            or self._save_profile is None
            or self._profile_context is None
            or self._follow_period_reason is None
            or self._queue_follow_period is None
            or self._queue_account_precompute is None
        ):
            raise RuntimeError("FrontendProfileUpdateService route dependencies are not configured")
        username = self._request_username(request)
        return self.update_profile(
            username=username,
            payload=payload,
            include_catalog=include_catalog,
            load_profile=self._load_profile,
            resolve_updates=self._resolve_updates,
            strategy_models_payload=self._strategy_models_payload,
            model_lookup=self._model_lookup,
            save_profile=self._save_profile,
            profile_context=lambda **kwargs: self._profile_context(request, **kwargs),
            follow_period_reason=self._follow_period_reason,
            queue_follow_period=self._queue_follow_period,
            queue_account_precompute=self._queue_account_precompute,
        )

    def update_profile(
        self,
        *,
        username: str,
        payload: Dict[str, Any],
        include_catalog: bool,
        load_profile: Callable[[str], Dict[str, Any]],
        resolve_updates: Callable[..., tuple[Dict[str, Any], Dict[str, Any]]],
        strategy_models_payload: Callable[..., Dict[str, Any]],
        model_lookup: Callable[..., Dict[str, Any]],
        save_profile: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        profile_context: Callable[..., Dict[str, Any]],
        follow_period_reason: Callable[[Any, Any], str],
        queue_follow_period: Callable[..., Dict[str, Any]],
        queue_account_precompute: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        started = time.time()
        last_stage_at = started
        profile_trace: list[Dict[str, Any]] = []

        def mark_stage(stage: str) -> None:
            nonlocal last_stage_at
            now = time.time()
            profile_trace.append(
                {
                    "stage": stage,
                    "elapsed_ms": int((now - started) * 1000),
                    "duration_ms": int((now - last_stage_at) * 1000),
                }
            )
            last_stage_at = now

        previous = {}
        try:
            previous_payload = load_profile(username)
            previous = previous_payload.get("profile") if isinstance(previous_payload.get("profile"), dict) else {}
        except Exception:
            previous = {}
        mark_stage("load_previous_profile")

        updates, resolved_model = resolve_updates(
            dict(payload) if isinstance(payload, dict) else {},
            previous,
            include_catalog,
            strategy_models_payload,
            model_lookup,
        )
        mark_stage("resolve_updates")

        result = save_profile(username, updates)
        mark_stage("save_profile")

        context = profile_context(
            include_catalog=include_catalog,
            fallback_catalog_on_missing=True,
            profile_payload=result,
            resolved_model=resolved_model,
        )
        mark_stage("build_profile_context")

        follow_reason = follow_period_reason(previous, context.get("profile"))
        follow_period_record = queue_follow_period(
            username,
            context.get("profile"),
            previous_profile=previous,
            source="front_profile",
            reason=follow_reason,
            created_at=context.get("created_at"),
        )
        mark_stage("queue_follow_period")

        account_precompute = queue_account_precompute(
            username,
            reason=follow_reason,
            start_worker=False,
            async_enqueue=True,
        )
        mark_stage("queue_account_precompute")

        elapsed_ms = int((time.time() - started) * 1000)
        slow_stage = max(profile_trace, key=lambda item: int(item.get("duration_ms") or 0), default={})
        response = {
            **result,
            "profile": context["profile"],
            "followed_model": context["followed_model"],
            "strategy_params": context["strategy_params"],
            "account_cache_cleared": False,
            "account_cache_scope": "profile_keyed",
            "account_precompute": account_precompute,
            "account_precompute_queued": bool(account_precompute.get("queued")),
            "follow_period_record": follow_period_record,
            "profile_catalog_included": bool(include_catalog),
            "profile_update_elapsed_ms": elapsed_ms,
            "profile_update_trace": profile_trace,
            "profile_update_slow_stage": slow_stage,
        }
        if include_catalog:
            response["strategy_models"] = context["models_payload"]
        return response
