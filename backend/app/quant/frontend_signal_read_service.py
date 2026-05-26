from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional


class FrontendSignalReadService:
    def __init__(
        self,
        *,
        profile_context: Callable[..., Dict[str, Any]],
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        replay_start_date: Callable[[Optional[str]], Optional[str]],
        payload_read_service: Any,
        temporary_strategy_params: Callable[[Dict[str, Any]], Any],
        recommendations: Callable[..., Dict[str, Any]],
        daily_plan: Callable[..., Dict[str, Any]],
    ) -> None:
        self._profile_context = profile_context
        self._resolve_as_of = resolve_as_of
        self._replay_start_date = replay_start_date
        self._payload_read_service = payload_read_service
        self._temporary_strategy_params = temporary_strategy_params
        self._recommendations = recommendations
        self._daily_plan = daily_plan

    def compute_recommendations(
        self,
        context: Dict[str, Any],
        effective_as_of: Optional[str],
        lookback_days: int,
        top_n: int,
    ) -> Dict[str, Any]:
        with self._temporary_strategy_params(context["strategy_params"]):
            return self._recommendations(
                as_of=effective_as_of,
                lookback_days=lookback_days,
                top_n=top_n,
            )

    def compute_daily_plan(
        self,
        context: Dict[str, Any],
        effective_as_of: Optional[str],
        effective_start: Optional[str],
        limit_days: int,
    ) -> Dict[str, Any]:
        with self._temporary_strategy_params(context["strategy_params"]):
            return self._daily_plan(
                as_of=effective_as_of,
                start_date=effective_start,
                limit_days=limit_days,
            )

    def recommendations_payload(
        self,
        request: Any,
        as_of: Optional[str] = None,
        lookback_days: int = 2,
        top_n: int = 30,
        force: bool = False,
        defer: bool = True,
    ) -> Dict[str, Any]:
        context = self._profile_context(request, include_catalog=False)
        effective_as_of = self._resolve_as_of(as_of)
        return self._payload_read_service.recommendations_payload(
            context=context,
            effective_as_of=effective_as_of,
            lookback_days=lookback_days,
            top_n=top_n,
            force=force,
            defer=defer,
            compute=lambda: self.compute_recommendations(context, effective_as_of, lookback_days, top_n),
            affordable_payload=lambda payload: self._payload_read_service.affordable_payload(
                payload,
                context,
                effective_as_of,
            ),
        )

    def daily_plan_payload(
        self,
        request: Any,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        limit_days: int = 120,
        force: bool = False,
        defer: bool = True,
    ) -> Dict[str, Any]:
        context = self._profile_context(request, include_catalog=False)
        effective_as_of = self._resolve_as_of(as_of)
        effective_start = start_date or self._replay_start_date(effective_as_of)
        return self._payload_read_service.daily_plan_payload(
            context=context,
            effective_as_of=effective_as_of,
            effective_start=effective_start,
            limit_days=limit_days,
            force=force,
            defer=defer,
            compute=lambda: self.compute_daily_plan(context, effective_as_of, effective_start, limit_days),
            affordable_payload=lambda payload: self._payload_read_service.affordable_payload(
                payload,
                context,
                effective_as_of,
            ),
        )
