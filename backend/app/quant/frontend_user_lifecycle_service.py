from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class AdminFrontendUserService:
    def __init__(
        self,
        *,
        lifecycle: "FrontendUserLifecycleService",
        list_users: Callable[[], Dict[str, Any]],
        create_user: Callable[[Dict[str, Any], Any], Dict[str, Any]],
        update_user: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        reset_password: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        set_disabled: Callable[..., Dict[str, Any]],
        delete_user: Callable[[str], Dict[str, Any]],
        load_profile: Callable[[str], Dict[str, Any]],
        clear_account_cache: Callable[[], None],
        clear_memory_cache: Callable[[], None],
        record_follow_period: Callable[..., Dict[str, Any]],
        follow_period_reason: Callable[[Any, Any], str],
        queue_account_precompute: Callable[..., Dict[str, Any]],
        user_follow_diagnostics: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
        enrich_user: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._list_users = list_users
        self._create_user = create_user
        self._update_user = update_user
        self._reset_password = reset_password
        self._set_disabled = set_disabled
        self._delete_user = delete_user
        self._load_profile = load_profile
        self._clear_account_cache = clear_account_cache
        self._clear_memory_cache = clear_memory_cache
        self._record_follow_period = record_follow_period
        self._follow_period_reason = follow_period_reason
        self._queue_account_precompute = queue_account_precompute
        self._user_follow_diagnostics = user_follow_diagnostics
        self._external_enrich_user = enrich_user

    def list_users_payload(self) -> Dict[str, Any]:
        payload = self._list_users()
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        enriched = [self.enrich_user(item) for item in items if isinstance(item, dict)]
        next_payload = dict(payload)
        next_payload["items"] = enriched
        next_payload["account_snapshot_count"] = sum(
            1
            for item in enriched
            if (item.get("account_diagnostic") or {}).get("account_snapshot")
        )
        return next_payload

    def enrich_user(self, user: Dict[str, Any]) -> Dict[str, Any]:
        if self._external_enrich_user is not None:
            return self._external_enrich_user(user)
        if self._user_follow_diagnostics is None:
            return dict(user)
        return self._lifecycle.user_with_diagnostics(
            user,
            record_follow_period=self._record_follow_period,
            user_follow_diagnostics=self._user_follow_diagnostics,
        )

    def create_user_payload(self, request: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = self._create_user(payload, request)
        return self._lifecycle.after_admin_create(
            result,
            clear_memory_cache=self._clear_memory_cache,
            record_follow_period=self._record_follow_period,
            enrich_user=self.enrich_user,
            queue_account_precompute=self._queue_account_precompute,
        )

    def update_user_payload(self, username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._lifecycle.admin_update(
            username,
            payload,
            load_profile=self._load_profile,
            update_user=self._update_user,
            clear_account_cache=self._clear_account_cache,
            clear_memory_cache=self._clear_memory_cache,
            record_follow_period=self._record_follow_period,
            follow_period_reason=self._follow_period_reason,
            enrich_user=self.enrich_user,
            queue_account_precompute=self._queue_account_precompute,
        )

    def reset_password_payload(self, username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._lifecycle.admin_reset_password(
            username,
            payload,
            reset_password=self._reset_password,
            clear_memory_cache=self._clear_memory_cache,
        )

    def ban_user_payload(self, username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._lifecycle.admin_set_disabled(
            username,
            payload,
            disabled=True,
            set_disabled=self._set_disabled,
            clear_memory_cache=self._clear_memory_cache,
        )

    def unban_user_payload(self, username: str) -> Dict[str, Any]:
        return self._lifecycle.admin_set_disabled(
            username,
            {},
            disabled=False,
            set_disabled=self._set_disabled,
            clear_memory_cache=self._clear_memory_cache,
        )

    def delete_user_payload(self, username: str) -> Dict[str, Any]:
        return self._lifecycle.admin_delete(
            username,
            delete_user=self._delete_user,
            clear_memory_cache=self._clear_memory_cache,
        )


class FrontendUserLifecycleService:
    def user_with_diagnostics(
        self,
        item: Dict[str, Any],
        *,
        record_follow_period: Callable[..., Dict[str, Any]],
        user_follow_diagnostics: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        row = dict(item)
        username = str(row.get("username") or "").strip()
        profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
        if username and profile:
            period = record_follow_period(
                username,
                profile,
                source="admin_user_summary",
                reason="admin_summary_sync",
                created_at=row.get("created_at"),
            )
            diagnostic = user_follow_diagnostics(username, profile)
            if period.get("status") == "ok" and not diagnostic.get("current_period"):
                diagnostic["current_period"] = period
            row["follow_period"] = diagnostic.get("current_period") or period
            row["account_diagnostic"] = diagnostic
        return row

    def after_front_register(
        self,
        result: Dict[str, Any],
        *,
        load_profile: Callable[[str], Dict[str, Any]],
        record_follow_period: Callable[..., Dict[str, Any]],
        queue_account_precompute: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        username = str(result.get("username") or "").strip()
        if not username:
            return result
        try:
            profile_payload = load_profile(username)
            record_follow_period(
                username,
                profile_payload.get("profile") if isinstance(profile_payload, dict) else {},
                source="front_register",
                reason="register",
                created_at=(profile_payload or {}).get("created_at") if isinstance(profile_payload, dict) else "",
            )
            result["account_precompute"] = queue_account_precompute(
                username,
                reason="register",
                start_worker=False,
                async_enqueue=True,
            )
        except Exception:
            pass
        return result

    def after_admin_create(
        self,
        result: Dict[str, Any],
        *,
        clear_memory_cache: Callable[[], None],
        record_follow_period: Callable[..., Dict[str, Any]],
        enrich_user: Callable[[Dict[str, Any]], Dict[str, Any]],
        queue_account_precompute: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        clear_memory_cache()
        user = result.get("user") if isinstance(result.get("user"), dict) else {}
        if not user:
            return result
        record_follow_period(
            user.get("username"),
            user.get("profile"),
            source="admin_create_user",
            reason="admin_create_user",
            created_at=user.get("created_at"),
        )
        result["user"] = enrich_user(user)
        result["account_precompute"] = self._safe_queue_account(
            queue_account_precompute,
            user.get("username"),
            reason="register",
        )
        result["account_precompute_queued"] = bool((result.get("account_precompute") or {}).get("queued"))
        return result

    def admin_update(
        self,
        username: str,
        payload: Dict[str, Any],
        *,
        load_profile: Callable[[str], Dict[str, Any]],
        update_user: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        clear_account_cache: Callable[[], None],
        clear_memory_cache: Callable[[], None],
        record_follow_period: Callable[..., Dict[str, Any]],
        follow_period_reason: Callable[[Any, Any], str],
        enrich_user: Callable[[Dict[str, Any]], Dict[str, Any]],
        queue_account_precompute: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        previous = {}
        try:
            previous_payload = load_profile(username)
            previous = previous_payload.get("profile") if isinstance(previous_payload.get("profile"), dict) else {}
        except Exception:
            previous = {}
        result = update_user(username, payload)
        clear_account_cache()
        clear_memory_cache()
        user = result.get("user") if isinstance(result.get("user"), dict) else {}
        if not user:
            return result

        reason = follow_period_reason(previous, user.get("profile"))
        record_follow_period(
            user.get("username"),
            user.get("profile"),
            previous_profile=previous,
            source="admin_update_user",
            reason=reason,
            created_at=user.get("created_at"),
        )
        result["user"] = enrich_user(user)
        result["account_precompute"] = self._safe_queue_account(
            queue_account_precompute,
            user.get("username"),
            reason=reason,
        )
        result["account_precompute_queued"] = bool((result.get("account_precompute") or {}).get("queued"))
        return result

    def admin_reset_password(
        self,
        username: str,
        payload: Dict[str, Any],
        *,
        reset_password: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        clear_memory_cache: Callable[[], None],
    ) -> Dict[str, Any]:
        result = reset_password(username, payload)
        clear_memory_cache()
        return result

    def admin_set_disabled(
        self,
        username: str,
        payload: Dict[str, Any],
        *,
        disabled: bool,
        set_disabled: Callable[..., Dict[str, Any]],
        clear_memory_cache: Callable[[], None],
    ) -> Dict[str, Any]:
        reason = str((payload or {}).get("reason") or "")
        result = set_disabled(username, disabled, reason) if disabled else set_disabled(username, disabled)
        clear_memory_cache()
        return result

    def admin_delete(
        self,
        username: str,
        *,
        delete_user: Callable[[str], Dict[str, Any]],
        clear_memory_cache: Callable[[], None],
    ) -> Dict[str, Any]:
        result = delete_user(username)
        clear_memory_cache()
        return result

    @staticmethod
    def _safe_queue_account(
        queue_account_precompute: Callable[..., Dict[str, Any]],
        username: Any,
        *,
        reason: str,
    ) -> Dict[str, Any]:
        try:
            return queue_account_precompute(
                str(username or ""),
                reason=reason,
                start_worker=False,
                async_enqueue=True,
            )
        except Exception as exc:
            return {"status": "error", "queued": False, "reason": reason, "message": str(exc)}
