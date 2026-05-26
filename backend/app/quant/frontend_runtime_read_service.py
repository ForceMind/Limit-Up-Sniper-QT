from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional


class FrontendRuntimeReadService:
    def __init__(
        self,
        *,
        profile_context: Callable[..., Dict[str, Any]],
        env_flag: Callable[[str, bool], bool],
        strategy_account: Callable[..., Dict[str, Any]],
        trading_account_payload: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        attach_account_precompute: Callable[..., Dict[str, Any]],
        strategy_daily_payload: Callable[..., Dict[str, Any]],
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        runtime_daily_payload: Callable[[Optional[str]], Dict[str, Any]],
        news_payload: Callable[..., Dict[str, Any]],
    ) -> None:
        self._profile_context = profile_context
        self._env_flag = env_flag
        self._strategy_account = strategy_account
        self._trading_account_payload = trading_account_payload
        self._attach_account_precompute = attach_account_precompute
        self._strategy_daily_payload = strategy_daily_payload
        self._resolve_as_of = resolve_as_of
        self._runtime_daily_payload = runtime_daily_payload
        self._news_payload = news_payload

    def account_sync_compute_enabled(self) -> bool:
        return self._env_flag("QT_FRONT_ACCOUNT_SYNC_COMPUTE_ENABLED", False)

    def trading_account_payload(
        self,
        request: Any,
        as_of: Optional[str] = None,
        limit: int = 500,
        force: bool = False,
        defer: bool = True,
    ) -> Dict[str, Any]:
        context = self._profile_context(request, include_catalog=False)
        sync_compute_enabled = self.account_sync_compute_enabled()
        effective_force = bool(force and sync_compute_enabled)
        effective_defer_miss = True if not sync_compute_enabled else bool(defer and not effective_force)
        persist_on_read = bool(effective_force or self._env_flag("QT_FRONT_ACCOUNT_PERSIST_ON_READ", False))
        account = self._strategy_account(
            context,
            as_of,
            limit=limit,
            force=effective_force,
            record_period=persist_on_read,
            defer_miss=effective_defer_miss,
            persist_derived=persist_on_read,
        )
        payload = self._trading_account_payload(account, context)
        if not sync_compute_enabled:
            payload["frontend_account_sync_compute_enabled"] = False
            if force:
                payload["frontend_account_force_ignored"] = True
            if not defer:
                payload["frontend_account_defer_enforced"] = True
        if effective_force:
            return payload
        return self._attach_account_precompute(
            payload,
            context,
            as_of,
            reason="account_runtime_missing",
        )

    def strategy_daily_payload(
        self,
        request: Any,
        as_of: Optional[str] = None,
        news_limit: int = 30,
    ) -> Dict[str, Any]:
        context = self._profile_context(request, include_catalog=False)
        return self._strategy_daily_payload(
            context=context,
            as_of=as_of,
            news_limit=news_limit,
            resolve_as_of=self._resolve_as_of,
            runtime_daily_payload=self._runtime_daily_payload,
            news_payload=self._news_payload,
        )
