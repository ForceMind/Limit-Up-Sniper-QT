from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import Request

from app.quant.engine import DATA_DIR, read_json, safe_float, write_json


ACCESS_LOG_FILE = DATA_DIR / "access_logs.json"
MAX_ACCESS_LOGS = 5000
_ACCESS_LOCK = threading.Lock()


def _client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    client_host = request.client.host if request.client else ""
    return forwarded or real_ip or client_host


def record_access(
    request: Request,
    status_code: int,
    duration_ms: float,
    auth_payload: Optional[Dict[str, Any]] = None,
) -> None:
    path = request.url.path
    if path.startswith("/static/"):
        return
    payload = auth_payload if isinstance(auth_payload, dict) else {}
    item = {
        "ts": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "method": request.method,
        "path": path,
        "query": str(request.url.query or "")[:1000],
        "status_code": int(status_code or 0),
        "duration_ms": round(float(duration_ms or 0), 2),
        "username": str(payload.get("sub") or ""),
        "scope": str(payload.get("scope") or "public"),
        "ip": _client_ip(request),
        "user_agent": str(request.headers.get("user-agent") or "")[:500],
        "referer": str(request.headers.get("referer") or "")[:500],
    }
    with _ACCESS_LOCK:
        data = read_json(ACCESS_LOG_FILE, {"items": []})
        if not isinstance(data, dict):
            data = {"items": []}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        items.append(item)
        data["items"] = items[-MAX_ACCESS_LOGS:]
        data["updated_at"] = item["ts"]
        write_json(ACCESS_LOG_FILE, data)


def access_logs(
    limit: int = 200,
    username: Optional[str] = None,
    ip: Optional[str] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    data = read_json(ACCESS_LOG_FILE, {"items": []})
    if not isinstance(data, dict):
        data = {"items": []}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    rows = [item for item in items if isinstance(item, dict)]
    if username:
        rows = [item for item in rows if username.lower() in str(item.get("username") or "").lower()]
    if ip:
        rows = [item for item in rows if ip in str(item.get("ip") or "")]
    if path:
        rows = [item for item in rows if path.lower() in str(item.get("path") or "").lower()]
    rows = list(reversed(rows))
    limit = max(1, min(int(safe_float(limit, 200)), 1000))
    unique_visitors = len({str(item.get("ip") or "") for item in rows if item.get("ip")})
    unique_users = len({str(item.get("username") or "") for item in rows if item.get("username")})
    return {
        "status": "ok",
        "items": rows[:limit],
        "count": len(rows),
        "returned": min(limit, len(rows)),
        "unique_visitors": unique_visitors,
        "unique_users": unique_users,
        "updated_at": data.get("updated_at") or "",
    }
