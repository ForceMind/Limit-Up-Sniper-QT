from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional


class FrontendDateService:
    def __init__(
        self,
        *,
        latest_data_date: Callable[[], str],
        first_data_date: Callable[[], str],
        account_replay_days: Callable[[], int],
    ) -> None:
        self._latest_data_date = latest_data_date
        self._first_data_date = first_data_date
        self._account_replay_days = account_replay_days

    def account_as_of(self, as_of: Optional[str]) -> Optional[str]:
        latest = str(self._latest_data_date() or "").strip()
        requested = str(as_of or "").strip()
        if requested and latest and requested > latest:
            return latest
        return requested or latest or None

    def replay_start_date(self, end_date: Optional[str]) -> Optional[str]:
        first = str(self._first_data_date() or "").strip()
        if not end_date:
            return first or None
        try:
            replay_days = max(0, int(self._account_replay_days() or 0))
            start = datetime.strptime(end_date[:10], "%Y-%m-%d") - timedelta(days=replay_days)
            start_text = start.strftime("%Y-%m-%d")
            return max(first, start_text) if first else start_text
        except Exception:
            return first or None

    def follow_start_date(self, context: Dict[str, Any], end_date: Optional[str]) -> Optional[str]:
        profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
        candidates = [
            str(profile.get("follow_start_date") or "").strip()[:10],
            str(profile.get("follow_started_at") or "").strip()[:10],
            str(context.get("created_at") or "").strip()[:10],
        ]
        first = str(self._first_data_date() or "").strip()
        latest = str(end_date or self._latest_data_date() or "").strip()[:10]
        start = next((item for item in candidates if item), "")
        if not start:
            start = latest or first
        if first and start < first:
            start = first
        if latest and start > latest:
            start = latest
        return start or first or latest or None
