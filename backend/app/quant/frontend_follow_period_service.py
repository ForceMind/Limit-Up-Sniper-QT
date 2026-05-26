from __future__ import annotations

import copy
import threading
from typing import Any, Callable, Dict, Optional

from app.quant.engine_utils import safe_float


RecordFollowPeriod = Callable[..., Dict[str, Any]]


class FrontendFollowPeriodService:
    def __init__(
        self,
        *,
        env_flag: Callable[[str, bool], bool],
        append_log: Callable[[str, str, str, str, Dict[str, Any]], None],
        record_follow_period: Optional[RecordFollowPeriod] = None,
    ) -> None:
        self._env_flag = env_flag
        self._append_log = append_log
        self._record_follow_period = record_follow_period

    def configure_recorder(self, record_follow_period: RecordFollowPeriod) -> None:
        self._record_follow_period = record_follow_period

    def follow_period_reason(self, previous: Any, current: Any) -> str:
        previous = previous if isinstance(previous, dict) else {}
        current = current if isinstance(current, dict) else {}
        old_model = str(previous.get("strategy_model_id") or "")
        new_model = str(current.get("strategy_model_id") or "")
        old_cash = safe_float(previous.get("simulated_cash"), 0)
        new_cash = safe_float(current.get("simulated_cash"), old_cash)
        model_changed = bool(new_model and old_model and new_model != old_model)
        cash_changed = abs(new_cash - old_cash) >= 0.01 if old_cash > 0 else False
        if model_changed and cash_changed:
            return "profile_cash_and_strategy_changed"
        if model_changed:
            return "profile_strategy_changed"
        if cash_changed:
            return "profile_cash_changed"
        return "profile_sync"

    def record(
        self,
        record_follow_period: RecordFollowPeriod,
        username: str,
        profile: Any,
        previous_profile: Optional[Dict[str, Any]] = None,
        source: str = "",
        reason: str = "",
        created_at: Any = "",
    ) -> Dict[str, Any]:
        if not isinstance(profile, dict):
            return {"status": "invalid"}
        return record_follow_period(
            username,
            profile,
            reason=reason or "profile_sync",
            source=source or "frontend_profile",
            previous_profile=previous_profile,
            created_at=str(created_at or ""),
        )

    def record_user_follow_period(
        self,
        username: str,
        profile: Any,
        previous_profile: Optional[Dict[str, Any]] = None,
        source: str = "",
        reason: str = "",
        created_at: Any = "",
    ) -> Dict[str, Any]:
        if self._record_follow_period is None:
            raise RuntimeError("FrontendFollowPeriodService recorder is not configured")
        return self.record(
            self._record_follow_period,
            username,
            profile,
            previous_profile=previous_profile,
            source=source,
            reason=reason,
            created_at=created_at,
        )

    def queue_record(
        self,
        record_follow_period: RecordFollowPeriod,
        username: str,
        profile: Any,
        previous_profile: Optional[Dict[str, Any]] = None,
        source: str = "",
        reason: str = "",
        created_at: Any = "",
    ) -> Dict[str, Any]:
        if not self._env_flag("QT_FRONT_PROFILE_FOLLOW_PERIOD_ASYNC", True):
            return self.record(
                record_follow_period,
                username,
                profile,
                previous_profile=previous_profile,
                source=source,
                reason=reason,
                created_at=created_at,
            )
        if not isinstance(profile, dict):
            return {"status": "invalid", "async": True}
        clean_username = str(username or "").strip()
        if not clean_username:
            return {"status": "invalid", "async": True}
        profile_copy = self._copy_payload(profile)
        previous_copy = self._copy_payload(previous_profile) if isinstance(previous_profile, dict) else None
        reason_text = str(reason or "profile_sync")
        source_text = str(source or "frontend_profile")
        created_text = str(created_at or "")

        def worker() -> None:
            try:
                self.record(
                    record_follow_period,
                    clean_username,
                    profile_copy,
                    previous_profile=previous_copy,
                    source=source_text,
                    reason=reason_text,
                    created_at=created_text,
                )
            except Exception as exc:
                try:
                    self._append_log(
                        "warning",
                        f"frontend follow period async record failed: {exc}",
                        "front_profile",
                        "follow_period",
                        {"username": clean_username, "reason": reason_text, "source": source_text},
                    )
                except Exception:
                    pass

        threading.Thread(target=worker, name=f"qt-follow-period-{clean_username}", daemon=True).start()
        return {"status": "queued", "async": True, "username": clean_username, "reason": reason_text, "source": source_text}

    def queue_user_follow_period_record(
        self,
        username: str,
        profile: Any,
        previous_profile: Optional[Dict[str, Any]] = None,
        source: str = "",
        reason: str = "",
        created_at: Any = "",
    ) -> Dict[str, Any]:
        return self.queue_record(
            self.record_user_follow_period,
            username,
            profile,
            previous_profile=previous_profile,
            source=source,
            reason=reason,
            created_at=created_at,
        )

    @staticmethod
    def _copy_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return copy.deepcopy(payload)
        except Exception:
            return dict(payload)
