from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class AdminAccessPayloadError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AdminAccessService:
    def __init__(
        self,
        *,
        access_logs: Callable[..., Dict[str, Any]],
        access_security: Callable[..., Dict[str, Any]],
        block_ip: Callable[..., Dict[str, Any]],
        unblock_ip: Callable[[str], Dict[str, Any]],
    ) -> None:
        self._access_logs = access_logs
        self._access_security = access_security
        self._block_ip = block_ip
        self._unblock_ip = unblock_ip

    def logs_payload(
        self,
        limit: int = 220,
        offset: int = 0,
        username: Optional[str] = None,
        ip: Optional[str] = None,
        path: Optional[str] = None,
        status_code: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._access_logs(
            limit=limit,
            offset=offset,
            username=username,
            ip=ip,
            path=path,
            status_code=status_code,
        )

    def security_payload(self, limit: int = 120) -> Dict[str, Any]:
        return self._access_security(limit=limit)

    def block_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ip = self._required_ip(payload)
        reason = str((payload or {}).get("reason") or "manual admin block").strip()
        result = self._block_ip(ip, reason=reason, source="manual")
        result["security"] = self._access_security(limit=120)
        return result

    def unblock_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        ip = self._required_ip(payload)
        result = self._unblock_ip(ip)
        result["security"] = self._access_security(limit=120)
        return result

    def block_all_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        limit = self._safe_int((payload or {}).get("limit"), 500)
        summary = self._access_security(limit=max(1, min(limit, 500)))
        blocked = []
        skipped = []
        for item in summary.get("items", []):
            if not isinstance(item, dict) or item.get("blocked"):
                continue
            ip = str(item.get("ip") or "").strip()
            if not ip:
                continue
            reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
            reason = "; ".join(str(reason) for reason in reasons if str(reason).strip()) or "manual bulk block suspicious access"
            result = self._block_ip(ip, reason=reason, source="manual_bulk")
            if result.get("blocked"):
                blocked.append(ip)
            else:
                skipped.append({"ip": ip, "result": result})
        return {
            "status": "ok",
            "blocked": blocked,
            "blocked_count": len(blocked),
            "skipped": skipped,
            "skipped_count": len(skipped),
            "security": self._access_security(limit=120),
        }

    @staticmethod
    def _required_ip(payload: Dict[str, Any]) -> str:
        ip = str((payload or {}).get("ip") or "").strip()
        if not ip:
            raise AdminAccessPayloadError("ip is required")
        return ip

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(float(value if value is not None else default))
        except Exception:
            return default
