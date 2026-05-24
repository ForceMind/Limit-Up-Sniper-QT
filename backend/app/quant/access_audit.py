from __future__ import annotations

import ipaddress
import threading
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import Request

from app.quant.engine import DATA_DIR, read_json, safe_float, write_json


ACCESS_LOG_FILE = DATA_DIR / "access_logs.json"
BLOCKED_IP_FILE = DATA_DIR / "blocked_ips.json"
MAX_ACCESS_LOGS = 5000
_ACCESS_LOCK = threading.Lock()
_BLOCK_LOCK = threading.Lock()

SCAN_PATH_MARKERS = (
    "/.env",
    "/.git",
    "/wp-",
    "/wp/",
    "phpmyadmin",
    "adminer",
    "/cgi-bin/",
    "/boaform/",
    "/vendor/",
    "eval-stdin",
    "setup.php",
    "config.php",
    "shell",
    "thinkphp",
    "solr/admin",
)
SCAN_USER_AGENT_MARKERS = ("sqlmap", "nmap", "masscan", "zgrab", "nikto", "acunetix")


def _client_ip(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    client_host = request.client.host if request.client else ""
    return forwarded or real_ip or client_host


def client_ip_from_request(request: Request) -> str:
    return _client_ip(request)


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _clean_ip(ip: Any) -> str:
    return str(ip or "").strip().split(",")[0].strip()


def _is_local_or_private_ip(ip: str) -> bool:
    clean = _clean_ip(ip)
    if not clean:
        return True
    try:
        parsed = ipaddress.ip_address(clean)
        return parsed.is_loopback or parsed.is_private or parsed.is_unspecified or parsed.is_multicast
    except ValueError:
        return clean.lower() in {"localhost", "unknown"}


def _read_blocked_payload() -> Dict[str, Any]:
    data = read_json(BLOCKED_IP_FILE, {"items": []})
    if not isinstance(data, dict):
        data = {"items": []}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    data["items"] = [item for item in items if isinstance(item, dict) and _clean_ip(item.get("ip"))]
    return data


def _write_blocked_payload(data: Dict[str, Any]) -> None:
    data["updated_at"] = _now()
    write_json(BLOCKED_IP_FILE, data)


def blocked_ips() -> Dict[str, Any]:
    data = _read_blocked_payload()
    items = sorted(data.get("items", []), key=lambda item: str(item.get("blocked_at") or ""), reverse=True)
    return {
        "status": "ok",
        "items": items,
        "count": len(items),
        "updated_at": data.get("updated_at") or "",
    }


def is_ip_blocked(ip: Any) -> bool:
    clean = _clean_ip(ip)
    if not clean or _is_local_or_private_ip(clean):
        return False
    data = _read_blocked_payload()
    return any(_clean_ip(item.get("ip")) == clean and not item.get("disabled") for item in data.get("items", []))


def block_ip(ip: Any, reason: str = "", source: str = "manual") -> Dict[str, Any]:
    clean = _clean_ip(ip)
    if not clean:
        return {"status": "error", "message": "IP 不能为空"}
    if _is_local_or_private_ip(clean):
        return {"status": "ignored", "ip": clean, "message": "本机或内网 IP 不自动封禁，避免误封反向代理"}
    now = _now()
    with _BLOCK_LOCK:
        data = _read_blocked_payload()
        items = data.get("items") if isinstance(data.get("items"), list) else []
        for item in items:
            if _clean_ip(item.get("ip")) == clean:
                item["disabled"] = False
                item["reason"] = reason or item.get("reason") or "异常访问"
                item["source"] = source or item.get("source") or "manual"
                item["updated_at"] = now
                _write_blocked_payload({"items": items})
                return {"status": "ok", "ip": clean, "blocked": True, "item": item}
        item = {
            "ip": clean,
            "reason": reason or "异常访问",
            "source": source or "manual",
            "blocked_at": now,
            "updated_at": now,
            "disabled": False,
        }
        items.append(item)
        _write_blocked_payload({"items": items})
        return {"status": "ok", "ip": clean, "blocked": True, "item": item}


def unblock_ip(ip: Any) -> Dict[str, Any]:
    clean = _clean_ip(ip)
    if not clean:
        return {"status": "error", "message": "IP 不能为空"}
    with _BLOCK_LOCK:
        data = _read_blocked_payload()
        items = data.get("items") if isinstance(data.get("items"), list) else []
        kept = [item for item in items if _clean_ip(item.get("ip")) != clean]
        _write_blocked_payload({"items": kept})
    return {"status": "ok", "ip": clean, "blocked": False}


def _is_scan_path(path: Any) -> bool:
    text = str(path or "").lower()
    return any(marker in text for marker in SCAN_PATH_MARKERS)


def _is_scan_agent(user_agent: Any) -> bool:
    text = str(user_agent or "").lower()
    return any(marker in text for marker in SCAN_USER_AGENT_MARKERS)


def _item_suspicion(item: Dict[str, Any]) -> tuple[int, list[str]]:
    path = str(item.get("path") or "")
    status_code = int(safe_float(item.get("status_code"), 0))
    username = str(item.get("username") or "").strip()
    score = 0
    reasons: list[str] = []
    if _is_scan_path(path):
        score += 10
        reasons.append("扫描敏感路径")
    if _is_scan_agent(item.get("user_agent")):
        score += 6
        reasons.append("扫描器 User-Agent")
    if not username and status_code in {404, 405}:
        score += 3
        reasons.append("访问不存在接口或方法")
        if path.startswith("/api/"):
            score += 2
            reasons.append("异常 API 探测")
    if not username and status_code in {401, 403} and path.startswith("/api/admin"):
        score += 2
        reasons.append("未授权后台接口访问")
    if path.startswith("//") or ".." in path:
        score += 5
        reasons.append("异常路径格式")
    return score, reasons


def _auto_block_after_append(items: list[Dict[str, Any]], item: Dict[str, Any]) -> None:
    ip = _clean_ip(item.get("ip"))
    if not ip or _is_local_or_private_ip(ip) or is_ip_blocked(ip):
        return
    score, reasons = _item_suspicion(item)
    if score >= 10:
        block_ip(ip, "、".join(reasons), source="auto_scan")
        return
    recent_bad = 0
    recent_reasons: set[str] = set()
    for row in reversed(items[-80:]):
        if _clean_ip(row.get("ip")) != ip:
            continue
        row_score, row_reasons = _item_suspicion(row)
        if row_score <= 0:
            continue
        recent_bad += 1
        recent_reasons.update(row_reasons)
        if recent_bad >= 3:
            block_ip(ip, "、".join(sorted(recent_reasons)) or "短时间多次异常访问", source="auto_threshold")
            return


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
        "ts": _now(),
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
        _auto_block_after_append(data["items"], item)


def access_logs(
    limit: int = 200,
    offset: int = 0,
    username: Optional[str] = None,
    ip: Optional[str] = None,
    path: Optional[str] = None,
    status_code: Optional[int] = None,
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
    if status_code:
        rows = [item for item in rows if int(safe_float(item.get("status_code"), 0)) == int(status_code)]
    rows = list(reversed(rows))
    limit = max(1, min(int(safe_float(limit, 200)), 1000))
    offset = max(0, int(safe_float(offset, 0)))
    page_rows = rows[offset : offset + limit]
    unique_visitors = len({str(item.get("ip") or "") for item in rows if item.get("ip")})
    unique_users = len({str(item.get("username") or "") for item in rows if item.get("username")})
    return {
        "status": "ok",
        "items": page_rows,
        "count": len(rows),
        "returned": len(page_rows),
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "prev_offset": max(0, offset - limit) if offset > 0 else None,
        "unique_visitors": unique_visitors,
        "unique_users": unique_users,
        "updated_at": data.get("updated_at") or "",
    }


def access_security(limit: int = 120) -> Dict[str, Any]:
    data = read_json(ACCESS_LOG_FILE, {"items": []})
    if not isinstance(data, dict):
        data = {"items": []}
    rows = [item for item in (data.get("items") or []) if isinstance(item, dict)]
    groups: Dict[str, Dict[str, Any]] = {}
    for item in rows:
        ip = _clean_ip(item.get("ip"))
        if not ip or _is_local_or_private_ip(ip):
            continue
        group = groups.setdefault(
            ip,
            {
                "ip": ip,
                "hits": 0,
                "authenticated_hits": 0,
                "bad_hits": 0,
                "score": 0,
                "reasons": set(),
                "first_seen": str(item.get("ts") or ""),
                "last_seen": str(item.get("ts") or ""),
                "examples": [],
            },
        )
        group["hits"] += 1
        ts = str(item.get("ts") or "")
        if ts:
            group["last_seen"] = max(str(group.get("last_seen") or ""), ts)
            group["first_seen"] = min(str(group.get("first_seen") or ts), ts)
        if str(item.get("username") or "").strip() and int(safe_float(item.get("status_code"), 0)) < 400:
            group["authenticated_hits"] += 1
        score, reasons = _item_suspicion(item)
        if score > 0:
            group["bad_hits"] += 1
            group["score"] += score
            group["reasons"].update(reasons)
            if len(group["examples"]) < 6:
                group["examples"].append(
                    {
                        "ts": item.get("ts") or "",
                        "method": item.get("method") or "",
                        "path": item.get("path") or "",
                        "status_code": item.get("status_code") or 0,
                        "user_agent": item.get("user_agent") or "",
                    }
                )
    blocked = blocked_ips()
    blocked_set = {_clean_ip(item.get("ip")) for item in blocked.get("items", [])}
    suspicious = []
    for group in groups.values():
        authenticated_hits = int(group.get("authenticated_hits") or 0)
        bad_hits = int(group.get("bad_hits") or 0)
        score = int(group.get("score") or 0)
        if score <= 0:
            continue
        if authenticated_hits > 0 and score < 10 and bad_hits < 5:
            continue
        item = dict(group)
        item["reasons"] = sorted(item.get("reasons") or [])
        item["blocked"] = item["ip"] in blocked_set
        suspicious.append(item)
    suspicious.sort(key=lambda item: (bool(item.get("blocked")), int(item.get("score") or 0), str(item.get("last_seen") or "")), reverse=True)
    limit = max(1, min(int(safe_float(limit, 120)), 500))
    return {
        "status": "ok",
        "items": suspicious[:limit],
        "count": len(suspicious),
        "returned": min(limit, len(suspicious)),
        "blocked": blocked.get("items", []),
        "blocked_count": blocked.get("count", 0),
        "updated_at": data.get("updated_at") or blocked.get("updated_at") or "",
    }
