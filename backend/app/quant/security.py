from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from app.quant.engine import DATA_DIR, DEFAULT_AI_MODEL, read_json, safe_float, write_json


AUTH_FILE = DATA_DIR / "auth.json"
CONFIG_FILE = DATA_DIR / "config.json"
PBKDF2_ITERATIONS = 200_000
TOKEN_TTL_SECONDS = int(safe_float(os.getenv("QT_AUTH_TOKEN_TTL_SECONDS"), 12 * 60 * 60))
DEBUG_KEY_HEADER = "x-qt-debug-key"
DEFAULT_FRONTEND_SIMULATED_CASH = 10_000.0
DEFAULT_FRONTEND_PROFILE = {
    "simulated_cash": DEFAULT_FRONTEND_SIMULATED_CASH,
    "strategy_model_id": "capital_10000",
}
DEFAULT_ADMIN_ENTRY_PREFIX = "/admin-"
ADMIN_ENTRY_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_auth() -> Dict[str, Any]:
    payload = read_json(AUTH_FILE, {})
    return payload if isinstance(payload, dict) else {}


def _save_auth(payload: Dict[str, Any]) -> None:
    write_json(AUTH_FILE, payload)


def _hash_password(password: str) -> Dict[str, Any]:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt,
        "hash": digest,
    }


def _verify_password(password: str, record: Dict[str, Any]) -> bool:
    try:
        iterations = int(record.get("iterations") or PBKDF2_ITERATIONS)
        salt = str(record.get("salt") or "")
        expected = str(record.get("hash") or "")
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password).encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return bool(expected) and hmac.compare_digest(digest, expected)
    except Exception:
        return False


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64_decode(text: str) -> bytes:
    padded = text + ("=" * (-len(text) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _token_secret(auth: Optional[Dict[str, Any]] = None) -> str:
    payload = auth if auth is not None else _load_auth()
    secret = str(payload.get("token_secret") or "").strip()
    if secret:
        return secret
    secret = secrets.token_urlsafe(32)
    payload["token_secret"] = secret
    payload.setdefault("updated_at", _now_iso())
    _save_auth(payload)
    return secret


def _scope_satisfies(actual: str, required: str) -> bool:
    return actual == required or (actual == "admin" and required == "frontend")


def create_token(scope: str, username: str) -> str:
    auth = _load_auth()
    body = {
        "scope": str(scope),
        "sub": str(username),
        "exp": int(time.time()) + max(TOKEN_TTL_SECONDS, 600),
    }
    body_b64 = _b64_encode(
        __import__("json").dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(_token_secret(auth).encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{body_b64}.{_b64_encode(signature)}"


def verify_token(token: str, required_scope: str) -> Dict[str, Any]:
    token = str(token or "").strip()
    if "." not in token:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    body_b64, signature_b64 = token.split(".", 1)
    auth = _load_auth()
    expected = hmac.new(_token_secret(auth).encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64_decode(signature_b64)
        payload = __import__("json").loads(_b64_decode(body_b64).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="missing or invalid token") from exc
    if not hmac.compare_digest(actual, expected):
        raise HTTPException(status_code=401, detail="missing or invalid token")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="token expired")
    scope = str(payload.get("scope") or "")
    if not _scope_satisfies(scope, required_scope):
        raise HTTPException(status_code=403, detail="insufficient permission")
    if scope == "frontend":
        record = _frontend_user_record(auth, str(payload.get("sub") or ""))
        if not record:
            raise HTTPException(status_code=401, detail="frontend user not found")
        if record.get("disabled"):
            raise HTTPException(status_code=403, detail="frontend user is disabled")
    return payload


def _debug_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _debug_key_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def debug_auth_status() -> Dict[str, Any]:
    raw_key = str(os.getenv("QT_DEBUG_API_KEY") or "").strip()
    hash_key = str(os.getenv("QT_DEBUG_API_KEY_SHA256") or "").strip().lower()
    enabled = _debug_env_bool("QT_DEBUG_API_ENABLED", False)
    write_allowed = _debug_env_bool("QT_DEBUG_API_ALLOW_WRITE", False)
    return {
        "status": "ok",
        "enabled": bool(enabled and (raw_key or hash_key)),
        "enabled_flag": enabled,
        "key_configured": bool(raw_key or hash_key),
        "key_source": "env_raw" if raw_key else ("env_sha256" if hash_key else ""),
        "write_allowed": write_allowed,
        "header": DEBUG_KEY_HEADER,
        "subject": os.getenv("QT_DEBUG_API_SUBJECT", "codex-debug"),
    }


def verify_debug_request(request: Request, required_scope: str) -> Optional[Dict[str, Any]]:
    supplied = str(request.headers.get(DEBUG_KEY_HEADER) or "").strip()
    if not supplied:
        return None
    status = debug_auth_status()
    if not status["enabled"]:
        raise HTTPException(status_code=403, detail="debug api is disabled")
    raw_key = str(os.getenv("QT_DEBUG_API_KEY") or "").strip()
    hash_key = str(os.getenv("QT_DEBUG_API_KEY_SHA256") or "").strip().lower()
    matched = False
    if raw_key and hmac.compare_digest(supplied, raw_key):
        matched = True
    if hash_key and hmac.compare_digest(_debug_key_hash(supplied), hash_key):
        matched = True
    if not matched:
        raise HTTPException(status_code=401, detail="debug key is invalid")
    method = str(request.method or "GET").upper()
    if method not in {"GET", "HEAD", "OPTIONS"} and not status["write_allowed"]:
        raise HTTPException(status_code=403, detail="debug api write access is disabled")
    if required_scope not in {"admin", "frontend"}:
        raise HTTPException(status_code=403, detail="debug api scope is not allowed")
    return {
        "scope": "admin",
        "sub": str(status.get("subject") or "codex-debug"),
        "debug": True,
        "write_allowed": bool(status.get("write_allowed")),
    }


def auth_status() -> Dict[str, Any]:
    payload = _load_auth()
    users = payload.get("users") if isinstance(payload.get("users"), dict) else {}
    admin = users.get("admin") if isinstance(users.get("admin"), dict) else {}
    frontend = users.get("frontend") if isinstance(users.get("frontend"), dict) else {}
    frontend_users = users.get("frontend_users") if isinstance(users.get("frontend_users"), dict) else {}
    frontend_names = [
        str(record.get("username") or username)
        for username, record in frontend_users.items()
        if isinstance(record, dict) and (record.get("username") or username)
    ]
    if frontend.get("username"):
        frontend_names.append(str(frontend.get("username") or ""))
    admin_configured = bool(admin.get("username") and admin.get("password"))
    frontend_configured = bool(frontend_users) or bool(frontend.get("username") and frontend.get("password"))
    return {
        "status": "ok",
        "setup_required": not admin_configured,
        "admin_configured": admin_configured,
        "frontend_configured": frontend_configured,
        "admin_username": str(admin.get("username") or ""),
        "frontend_username": frontend_names[0] if frontend_names else "",
        "frontend_user_count": len(frontend_names),
        "frontend_usernames": frontend_names[:20],
        "token_ttl_seconds": max(TOKEN_TTL_SECONDS, 600),
    }


def _clean_username(value: Any) -> str:
    return str(value or "").strip()


def _clean_password(value: Any) -> str:
    return str(value or "")


def _normalize_frontend_profile(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    cash = safe_float(raw.get("simulated_cash"), DEFAULT_FRONTEND_SIMULATED_CASH)
    cash = max(10_000.0, min(10_000_000.0, cash))
    model_id = str(raw.get("strategy_model_id") or DEFAULT_FRONTEND_PROFILE["strategy_model_id"]).strip() or DEFAULT_FRONTEND_PROFILE["strategy_model_id"]
    follow_started_at = str(raw.get("follow_started_at") or raw.get("created_at") or "").strip()
    follow_start_date = str(raw.get("follow_start_date") or follow_started_at[:10] or "").strip()[:10]
    return {
        "simulated_cash": round(cash, 2),
        "strategy_model_id": model_id[:120],
        "follow_started_at": follow_started_at,
        "follow_start_date": follow_start_date,
    }


def _request_ip(request: Optional[Request]) -> str:
    if request is None:
        return ""
    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    real_ip = str(request.headers.get("x-real-ip") or "").strip()
    client_host = request.client.host if request.client else ""
    return forwarded or real_ip or client_host


def setup_auth(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not auth_status()["setup_required"]:
        raise HTTPException(status_code=409, detail="auth is already configured")
    admin_username = _clean_username(payload.get("admin_username") or payload.get("username") or "admin")
    admin_password = _clean_password(payload.get("admin_password") or payload.get("password"))
    frontend_username = _clean_username(payload.get("frontend_username") or "trader")
    frontend_password = _clean_password(payload.get("frontend_password"))
    if len(admin_username) < 3:
        raise HTTPException(status_code=400, detail="username must be at least 3 characters")
    if len(admin_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    existing = _load_auth()
    existing_users = existing.get("users") if isinstance(existing.get("users"), dict) else {}
    frontend_users = existing_users.get("frontend_users") if isinstance(existing_users.get("frontend_users"), dict) else {}
    auth = {
        "version": 2,
        "created_at": existing.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "token_secret": existing.get("token_secret") or secrets.token_urlsafe(32),
        "users": {
            "admin": {"username": admin_username, "password": _hash_password(admin_password)},
            "frontend_users": frontend_users,
        },
    }
    if frontend_password:
        if len(frontend_username) < 3 or len(frontend_password) < 6:
            raise HTTPException(status_code=400, detail="frontend username or password is too short")
        created_at = _now_iso()
        auth["users"]["frontend_users"][frontend_username] = {
            "username": frontend_username,
            "password": _hash_password(frontend_password),
            "created_at": created_at,
            "last_login_at": "",
            "login_count": 0,
            "profile": _normalize_frontend_profile({"follow_started_at": created_at}),
        }
    _save_auth(auth)
    return {
        "status": "ok",
        "setup_required": False,
        "admin_username": admin_username,
        "frontend_username": frontend_username if frontend_password else "",
        "token": create_token("admin", admin_username),
        "scope": "admin",
    }


def _frontend_user_record(auth: Dict[str, Any], username: str) -> Optional[Dict[str, Any]]:
    users = auth.get("users") if isinstance(auth.get("users"), dict) else {}
    frontend_users = users.get("frontend_users") if isinstance(users.get("frontend_users"), dict) else {}
    record = frontend_users.get(username)
    if isinstance(record, dict):
        return record
    legacy = users.get("frontend") if isinstance(users.get("frontend"), dict) else {}
    if username == str(legacy.get("username") or ""):
        return legacy
    return None


def _ensure_frontend_users(auth: Dict[str, Any]) -> Dict[str, Any]:
    users = auth.get("users") if isinstance(auth.get("users"), dict) else {}
    auth["users"] = users
    frontend_users = users.get("frontend_users") if isinstance(users.get("frontend_users"), dict) else {}
    users["frontend_users"] = frontend_users
    legacy = users.get("frontend") if isinstance(users.get("frontend"), dict) else {}
    legacy_username = str(legacy.get("username") or "").strip()
    if legacy_username and legacy_username not in frontend_users:
        frontend_users[legacy_username] = dict(legacy)
        frontend_users[legacy_username]["username"] = legacy_username
    return frontend_users


def _public_frontend_user(username: str, record: Dict[str, Any], source: str = "frontend_users") -> Dict[str, Any]:
    profile = _normalize_frontend_profile(record.get("profile") if isinstance(record.get("profile"), dict) else None)
    return {
        "username": str(record.get("username") or username),
        "created_at": str(record.get("created_at") or ""),
        "last_login_at": str(record.get("last_login_at") or ""),
        "login_count": int(safe_float(record.get("login_count"), 0)),
        "failed_login_count": int(safe_float(record.get("failed_login_count"), 0)),
        "last_failed_login_at": str(record.get("last_failed_login_at") or ""),
        "registered_ip": str(record.get("registered_ip") or ""),
        "registered_user_agent": str(record.get("registered_user_agent") or ""),
        "last_login_ip": str(record.get("last_login_ip") or ""),
        "last_login_user_agent": str(record.get("last_login_user_agent") or ""),
        "profile": profile,
        "profile_updated_at": str(record.get("profile_updated_at") or ""),
        "disabled": bool(record.get("disabled")),
        "disabled_at": str(record.get("disabled_at") or ""),
        "disabled_reason": str(record.get("disabled_reason") or ""),
        "password_updated_at": str(record.get("password_updated_at") or ""),
        "has_password": bool(record.get("password")),
        "source": source,
    }


def register_frontend_user(payload: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    username = _clean_username(payload.get("username"))
    password = _clean_password(payload.get("password"))
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="username must be at least 3 characters")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="password must be at least 6 characters")
    auth = _load_auth()
    auth.setdefault("version", 2)
    auth.setdefault("created_at", _now_iso())
    auth.setdefault("token_secret", secrets.token_urlsafe(32))
    frontend_users = _ensure_frontend_users(auth)
    if _frontend_user_record(auth, username):
        raise HTTPException(status_code=409, detail="username already exists")
    created_at = _now_iso()
    profile_payload = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    frontend_users[username] = {
        "username": username,
        "password": _hash_password(password),
        "created_at": created_at,
        "last_login_at": created_at,
        "login_count": 1,
        "registered_ip": _request_ip(request) if request else "",
        "registered_user_agent": str((request.headers.get("user-agent") if request else "") or "")[:500],
        "last_login_ip": _request_ip(request) if request else "",
        "last_login_user_agent": str((request.headers.get("user-agent") if request else "") or "")[:500],
        "profile": _normalize_frontend_profile({**profile_payload, "follow_started_at": created_at}),
    }
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {
        "status": "ok",
        "scope": "frontend",
        "username": username,
        "token": create_token("frontend", username),
        "token_ttl_seconds": max(TOKEN_TTL_SECONDS, 600),
    }


def frontend_user_summary() -> Dict[str, Any]:
    auth = _load_auth()
    frontend_users = _ensure_frontend_users(auth)
    items = []
    for username, record in frontend_users.items():
        if not isinstance(record, dict):
            continue
        items.append(_public_frontend_user(username, record))
    items.sort(key=lambda item: item.get("last_login_at") or item.get("created_at") or "", reverse=True)
    disabled_count = sum(1 for item in items if item.get("disabled"))
    return {
        "status": "ok",
        "items": items,
        "count": len(items),
        "active_count": len(items) - disabled_count,
        "disabled_count": disabled_count,
    }


def admin_create_frontend_user(payload: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    username = _clean_username(payload.get("username"))
    password = _clean_password(payload.get("password"))
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="username must be at least 3 characters")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="password must be at least 6 characters")
    auth = _load_auth()
    auth.setdefault("version", 2)
    auth.setdefault("created_at", _now_iso())
    auth.setdefault("token_secret", secrets.token_urlsafe(32))
    frontend_users = _ensure_frontend_users(auth)
    if _frontend_user_record(auth, username):
        raise HTTPException(status_code=409, detail="username already exists")
    created_at = _now_iso()
    profile_payload = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
    frontend_users[username] = {
        "username": username,
        "password": _hash_password(password),
        "created_at": created_at,
        "created_by": "admin",
        "last_login_at": "",
        "login_count": 0,
        "registered_ip": _request_ip(request) if request else "",
        "registered_user_agent": str((request.headers.get("user-agent") if request else "") or "")[:500],
        "profile": _normalize_frontend_profile({**profile_payload, "follow_started_at": created_at}),
    }
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {"status": "ok", "user": _public_frontend_user(username, frontend_users[username])}


def admin_update_frontend_user(username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    username = _clean_username(username)
    auth = _load_auth()
    frontend_users = _ensure_frontend_users(auth)
    record = _frontend_user_record(auth, username)
    if not record:
        raise HTTPException(status_code=404, detail="frontend user not found")
    updates = payload if isinstance(payload, dict) else {}
    current = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    profile_payload = updates.get("profile") if isinstance(updates.get("profile"), dict) else updates
    merged = {**current, **profile_payload}
    old_model = str(current.get("strategy_model_id") or "")
    new_model = str(merged.get("strategy_model_id") or old_model)
    old_cash = safe_float(current.get("simulated_cash"), DEFAULT_FRONTEND_SIMULATED_CASH)
    new_cash = safe_float(merged.get("simulated_cash"), old_cash)
    if (new_model and new_model != old_model) or abs(new_cash - old_cash) >= 0.01:
        follow_started_at = _now_iso()
        merged["follow_started_at"] = follow_started_at
        merged["follow_start_date"] = follow_started_at[:10]
    if not str(merged.get("follow_started_at") or "").strip():
        merged["follow_started_at"] = str(record.get("created_at") or _now_iso())
    record["profile"] = _normalize_frontend_profile(merged)
    record["profile_updated_at"] = _now_iso()
    if username not in frontend_users:
        frontend_users[username] = record
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {"status": "ok", "user": _public_frontend_user(username, record)}


def admin_reset_frontend_user_password(username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    username = _clean_username(username)
    password = _clean_password((payload or {}).get("password"))
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="password must be at least 6 characters")
    auth = _load_auth()
    record = _frontend_user_record(auth, username)
    if not record:
        raise HTTPException(status_code=404, detail="frontend user not found")
    record["password"] = _hash_password(password)
    record["password_updated_at"] = _now_iso()
    record["failed_login_count"] = 0
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {"status": "ok", "user": _public_frontend_user(username, record)}


def admin_set_frontend_user_disabled(username: str, disabled: bool, reason: str = "") -> Dict[str, Any]:
    username = _clean_username(username)
    auth = _load_auth()
    record = _frontend_user_record(auth, username)
    if not record:
        raise HTTPException(status_code=404, detail="frontend user not found")
    record["disabled"] = bool(disabled)
    if disabled:
        record["disabled_at"] = _now_iso()
        record["disabled_reason"] = str(reason or "后台封禁").strip()[:200]
    else:
        record["disabled_at"] = ""
        record["disabled_reason"] = ""
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {"status": "ok", "user": _public_frontend_user(username, record)}


def admin_delete_frontend_user(username: str) -> Dict[str, Any]:
    username = _clean_username(username)
    auth = _load_auth()
    frontend_users = _ensure_frontend_users(auth)
    if username not in frontend_users:
        raise HTTPException(status_code=404, detail="frontend user not found")
    frontend_users.pop(username, None)
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {"status": "ok", "deleted": username}


def frontend_user_profile(username: str) -> Dict[str, Any]:
    username = _clean_username(username)
    auth = _load_auth()
    record = _frontend_user_record(auth, username)
    if not record:
        raise HTTPException(status_code=404, detail="frontend user not found")
    profile = _normalize_frontend_profile(record.get("profile") if isinstance(record.get("profile"), dict) else None)
    if record.get("profile") != profile:
        record["profile"] = profile
        auth["updated_at"] = _now_iso()
        _save_auth(auth)
    return {
        "status": "ok",
        "username": username,
        "created_at": str(record.get("created_at") or ""),
        "profile_updated_at": str(record.get("profile_updated_at") or ""),
        "profile": profile,
    }


def update_frontend_user_profile(username: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    username = _clean_username(username)
    auth = _load_auth()
    record = _frontend_user_record(auth, username)
    if not record:
        raise HTTPException(status_code=404, detail="frontend user not found")
    current = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    updates = payload if isinstance(payload, dict) else {}
    merged = {**current, **updates}
    old_model = str(current.get("strategy_model_id") or "")
    new_model = str(merged.get("strategy_model_id") or old_model)
    old_cash = safe_float(current.get("simulated_cash"), DEFAULT_FRONTEND_SIMULATED_CASH)
    new_cash = safe_float(merged.get("simulated_cash"), old_cash)
    if (new_model and new_model != old_model) or abs(new_cash - old_cash) >= 0.01:
        follow_started_at = _now_iso()
        merged["follow_started_at"] = follow_started_at
        merged["follow_start_date"] = follow_started_at[:10]
    if not str(merged.get("follow_started_at") or "").strip():
        merged["follow_started_at"] = str(record.get("created_at") or _now_iso())
    profile = _normalize_frontend_profile(merged)
    record["profile"] = profile
    record["profile_updated_at"] = _now_iso()
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {"status": "ok", "username": username, "profile": profile}


def login(payload: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    scope = str(payload.get("scope") or "frontend").strip().lower()
    if scope not in {"admin", "frontend"}:
        raise HTTPException(status_code=400, detail="invalid login scope")
    status = auth_status()
    if scope == "admin" and status["setup_required"]:
        raise HTTPException(status_code=401, detail="setup required")
    username = _clean_username(payload.get("username"))
    password = _clean_password(payload.get("password"))
    auth = _load_auth()
    users = auth.get("users") if isinstance(auth.get("users"), dict) else {}
    if scope == "admin":
        record = users.get("admin") if isinstance(users.get("admin"), dict) else {}
    else:
        record = _frontend_user_record(auth, username) or {}
    if username != str(record.get("username") or "") or not _verify_password(password, record.get("password") or {}):
        if scope == "frontend" and record:
            record["failed_login_count"] = int(safe_float(record.get("failed_login_count"), 0)) + 1
            record["last_failed_login_at"] = _now_iso()
            record["last_failed_login_ip"] = _request_ip(request)
            auth["updated_at"] = _now_iso()
            _save_auth(auth)
        raise HTTPException(status_code=401, detail="username or password is incorrect")
    if scope == "frontend" and record.get("disabled"):
        raise HTTPException(status_code=403, detail="frontend user is disabled")
    record["last_login_at"] = _now_iso()
    record["login_count"] = int(safe_float(record.get("login_count"), 0)) + 1
    if request is not None:
        record["last_login_ip"] = _request_ip(request)
        record["last_login_user_agent"] = str(request.headers.get("user-agent") or "")[:500]
    if scope == "frontend":
        record["failed_login_count"] = 0
    auth["updated_at"] = _now_iso()
    _save_auth(auth)
    return {
        "status": "ok",
        "scope": scope,
        "username": username,
        "token": create_token(scope, username),
        "token_ttl_seconds": max(TOKEN_TTL_SECONDS, 600),
    }


def require_request_scope(request: Request, required_scope: str) -> Dict[str, Any]:
    status = auth_status()
    if required_scope == "admin" and status["setup_required"]:
        raise HTTPException(status_code=401, detail="setup required")
    debug_payload = verify_debug_request(request, required_scope)
    if debug_payload:
        return debug_payload
    authorization = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    token = token or str(request.headers.get("x-qt-token") or "").strip()
    return verify_token(token, required_scope)


def required_scope_for_api(path: str, method: str) -> Optional[str]:
    if str(method).upper() == "OPTIONS":
        return None
    if path.startswith("/api/auth/"):
        return None
    if not path.startswith("/api/"):
        return None
    if path == "/api/version":
        return None
    if path.startswith("/api/debug"):
        return "admin"
    if str(method).upper() == "GET" and (
        path == "/api/front/public_snapshot"
        or path.startswith("/api/quant/news")
    ):
        return None
    if path.startswith("/api/front/"):
        return "frontend"
    if path == "/api/status":
        return "admin"
    if path.startswith(("/api/admin", "/api/jobs", "/api/data", "/api/config", "/api/ai", "/api/notifications")):
        return "admin"
    if path.startswith("/api/quant/evolution"):
        return "admin"
    if str(method).upper() != "GET":
        return "admin"
    return "frontend"


def _read_config() -> Dict[str, Any]:
    payload = read_json(CONFIG_FILE, {})
    return payload if isinstance(payload, dict) else {}


def _save_config(payload: Dict[str, Any]) -> None:
    write_json(CONFIG_FILE, payload)


def _first_env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:3]}***{text[-4:]}"


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def normalize_admin_entry_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="admin entry path is required")
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/") or "/"
    lowered = path.lower()
    if lowered in {"/admin", "/api", "/static", "/index.html"} or lowered.startswith(("/api/", "/static/")):
        raise HTTPException(status_code=400, detail="admin entry path conflicts with a reserved route")
    if "/" in path[1:]:
        raise HTTPException(status_code=400, detail="admin entry path must be a single path segment")
    if not ADMIN_ENTRY_PATH_PATTERN.match(path):
        raise HTTPException(status_code=400, detail="admin entry path must be 6-64 letters, numbers, '-' or '_'")
    return path


def _generate_admin_entry_path() -> str:
    return DEFAULT_ADMIN_ENTRY_PREFIX + secrets.token_hex(4)


def ensure_admin_entry_path() -> str:
    cfg = _read_config()
    security_cfg = dict(cfg.get("security_config") if isinstance(cfg.get("security_config"), dict) else {})
    raw_path = security_cfg.get("admin_entry_path") or cfg.get("admin_entry_path")
    try:
        path = normalize_admin_entry_path(raw_path)
    except HTTPException:
        path = _generate_admin_entry_path()
    if security_cfg.get("admin_entry_path") != path:
        security_cfg["admin_entry_path"] = path
        cfg["security_config"] = security_cfg
        cfg["updated_at"] = _now_iso()
        _save_config(cfg)
    return path


def _source(env_value: Optional[str], config_value: Any) -> str:
    if env_value is not None and str(env_value).strip():
        return "env"
    if str(config_value or "").strip():
        return "config"
    return ""


def runtime_config_status() -> Dict[str, Any]:
    cfg = _read_config()
    api_keys = cfg.get("api_keys") if isinstance(cfg.get("api_keys"), dict) else {}
    ai_cfg = cfg.get("ai_cost_config") if isinstance(cfg.get("ai_cost_config"), dict) else {}
    default_ai = ai_cfg.get("default") if isinstance(ai_cfg.get("default"), dict) else {}
    data_cfg = cfg.get("data_provider_config") if isinstance(cfg.get("data_provider_config"), dict) else {}
    email_cfg = cfg.get("email_config") if isinstance(cfg.get("email_config"), dict) else {}
    security_cfg = cfg.get("security_config") if isinstance(cfg.get("security_config"), dict) else {}
    admin_entry = ensure_admin_entry_path()

    deepseek_env = _first_env("DEEPSEEK_API_KEY")
    deepseek_key = deepseek_env or str(api_keys.get("deepseek") or "").strip()
    model_env = _first_env("DEEPSEEK_MODEL")
    model = model_env or str(default_ai.get("model") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL

    biying_key_env = _first_env("BIYING_LICENSE_KEY")
    biying_key = biying_key_env or str(data_cfg.get("biying_license_key") or "").strip()
    biying_endpoint_env = _first_env("BIYING_ENDPOINT")
    biying_endpoint = biying_endpoint_env or str(data_cfg.get("biying_endpoint") or "https://api.biyingapi.com").strip()
    biying_enabled_env = os.getenv("BIYING_ENABLED")
    biying_enabled = _env_bool("BIYING_ENABLED", bool(data_cfg.get("biying_enabled")) or bool(biying_key))

    smtp_server_env = _first_env("SMTP_SERVER", "EMAIL_SMTP_SERVER")
    smtp_user_env = _first_env("SMTP_USER", "EMAIL_SMTP_USER")
    smtp_password_env = _first_env("SMTP_PASSWORD", "EMAIL_SMTP_PASSWORD")
    email_to_env = _first_env("EMAIL_TO", "RECIPIENT_EMAIL")
    smtp_server = smtp_server_env or str(email_cfg.get("smtp_server") or "").strip()
    smtp_user = smtp_user_env or str(email_cfg.get("smtp_user") or "").strip()
    smtp_password = smtp_password_env or str(email_cfg.get("smtp_password") or "").strip()
    email_to = email_to_env or str(email_cfg.get("recipient_email") or "").strip()
    email_enabled = _env_bool("EMAIL_ENABLED", bool(email_cfg.get("enabled")))

    return {
        "status": "ok",
        "config_exists": CONFIG_FILE.exists(),
        "config_file": str(CONFIG_FILE),
        "auth": auth_status(),
        "security": {
            "admin_entry_path": admin_entry,
            "admin_entry_source": "config" if security_cfg.get("admin_entry_path") else "generated",
            "debug_api": debug_auth_status(),
        },
        "deepseek": {
            "configured": bool(deepseek_key),
            "api_key_masked": _mask_secret(deepseek_key),
            "api_key_source": _source(deepseek_env, api_keys.get("deepseek")),
            "model": model,
            "model_source": _source(model_env, default_ai.get("model")) or "default",
        },
        "biying": {
            "enabled": bool(biying_enabled and biying_key),
            "configured": bool(biying_key),
            "license_key_masked": _mask_secret(biying_key),
            "license_key_source": _source(biying_key_env, data_cfg.get("biying_license_key")),
            "endpoint": biying_endpoint,
            "endpoint_source": _source(biying_endpoint_env, data_cfg.get("biying_endpoint")) or "default",
            "minute_limit": int(safe_float(_first_env("BIYING_MINUTE_LIMIT") or data_cfg.get("biying_minute_limit"), 3000)),
            "enabled_source": "env" if biying_enabled_env is not None else ("config" if "biying_enabled" in data_cfg else ""),
        },
        "email": {
            "enabled": bool(email_enabled and smtp_server and smtp_user and smtp_password and email_to),
            "configured": bool(smtp_server and smtp_user and smtp_password and email_to),
            "smtp_server": smtp_server,
            "smtp_user": smtp_user,
            "recipient_email": email_to,
            "smtp_password_masked": _mask_secret(smtp_password),
            "smtp_password_source": _source(smtp_password_env, email_cfg.get("smtp_password")),
            "enabled_source": "env" if os.getenv("EMAIL_ENABLED") is not None else ("config" if "enabled" in email_cfg else ""),
        },
    }


def runtime_config_form() -> Dict[str, Any]:
    cfg = _read_config()
    email_cfg = cfg.get("email_config") if isinstance(cfg.get("email_config"), dict) else {}
    api_keys = cfg.get("api_keys") if isinstance(cfg.get("api_keys"), dict) else {}
    ai_cfg = cfg.get("ai_cost_config") if isinstance(cfg.get("ai_cost_config"), dict) else {}
    default_ai = ai_cfg.get("default") if isinstance(ai_cfg.get("default"), dict) else {}
    data_cfg = cfg.get("data_provider_config") if isinstance(cfg.get("data_provider_config"), dict) else {}
    admin_entry = ensure_admin_entry_path()
    return {
        "status": "ok",
        "form": {
            "admin_entry_path": admin_entry,
            "deepseek_api_key": "",
            "deepseek_model": _first_env("DEEPSEEK_MODEL") or str(default_ai.get("model") or DEFAULT_AI_MODEL),
            "biying_enabled": _env_bool("BIYING_ENABLED", bool(data_cfg.get("biying_enabled"))),
            "biying_license_key": "",
            "biying_endpoint": _first_env("BIYING_ENDPOINT") or str(data_cfg.get("biying_endpoint") or "https://api.biyingapi.com"),
            "biying_minute_limit": int(safe_float(_first_env("BIYING_MINUTE_LIMIT") or data_cfg.get("biying_minute_limit"), 3000)),
            "email_enabled": _env_bool("EMAIL_ENABLED", bool(email_cfg.get("enabled"))),
            "smtp_server": _first_env("SMTP_SERVER", "EMAIL_SMTP_SERVER") or str(email_cfg.get("smtp_server") or ""),
            "smtp_port": int(safe_float(_first_env("SMTP_PORT", "EMAIL_SMTP_PORT") or email_cfg.get("smtp_port"), 465)),
            "smtp_user": _first_env("SMTP_USER", "EMAIL_SMTP_USER") or str(email_cfg.get("smtp_user") or ""),
            "smtp_password": "",
            "email_to": _first_env("EMAIL_TO", "RECIPIENT_EMAIL") or str(email_cfg.get("recipient_email") or ""),
            "smtp_use_ssl": _env_bool("SMTP_USE_SSL", bool(email_cfg.get("smtp_use_ssl", True))),
        },
        "secrets": {
            "deepseek_api_key": bool(_first_env("DEEPSEEK_API_KEY") or api_keys.get("deepseek")),
            "biying_license_key": bool(_first_env("BIYING_LICENSE_KEY") or data_cfg.get("biying_license_key")),
            "smtp_password": bool(_first_env("SMTP_PASSWORD", "EMAIL_SMTP_PASSWORD") or email_cfg.get("smtp_password")),
        },
        "sources": runtime_config_status(),
    }


def update_runtime_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _read_config()
    email_cfg = dict(cfg.get("email_config") if isinstance(cfg.get("email_config"), dict) else {})
    api_keys = dict(cfg.get("api_keys") if isinstance(cfg.get("api_keys"), dict) else {})
    ai_cfg = dict(cfg.get("ai_cost_config") if isinstance(cfg.get("ai_cost_config"), dict) else {})
    default_ai = dict(ai_cfg.get("default") if isinstance(ai_cfg.get("default"), dict) else {})
    data_cfg = dict(cfg.get("data_provider_config") if isinstance(cfg.get("data_provider_config"), dict) else {})
    security_cfg = dict(cfg.get("security_config") if isinstance(cfg.get("security_config"), dict) else {})

    if "admin_entry_path" in payload:
        security_cfg["admin_entry_path"] = normalize_admin_entry_path(payload.get("admin_entry_path"))

    if str(payload.get("deepseek_api_key") or "").strip():
        api_keys["deepseek"] = str(payload.get("deepseek_api_key") or "").strip()
    if "deepseek_model" in payload:
        default_ai["provider"] = "deepseek"
        default_ai["model"] = str(payload.get("deepseek_model") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL

    if "biying_enabled" in payload:
        data_cfg["biying_enabled"] = _bool_value(payload.get("biying_enabled"))
    if str(payload.get("biying_license_key") or "").strip():
        data_cfg["biying_license_key"] = str(payload.get("biying_license_key") or "").strip()
    if "biying_endpoint" in payload:
        data_cfg["biying_endpoint"] = str(payload.get("biying_endpoint") or "https://api.biyingapi.com").strip() or "https://api.biyingapi.com"
    if "biying_minute_limit" in payload:
        data_cfg["biying_minute_limit"] = max(1, int(safe_float(payload.get("biying_minute_limit"), 3000)))

    if "email_enabled" in payload:
        email_cfg["enabled"] = _bool_value(payload.get("email_enabled"))
    if "smtp_server" in payload:
        email_cfg["smtp_server"] = str(payload.get("smtp_server") or "").strip()
    if "smtp_port" in payload:
        email_cfg["smtp_port"] = max(1, int(safe_float(payload.get("smtp_port"), 465)))
    if "smtp_user" in payload:
        email_cfg["smtp_user"] = str(payload.get("smtp_user") or "").strip()
    if str(payload.get("smtp_password") or "").strip():
        email_cfg["smtp_password"] = str(payload.get("smtp_password") or "").strip()
    if "email_to" in payload:
        email_cfg["recipient_email"] = str(payload.get("email_to") or "").strip()
    if "smtp_use_ssl" in payload:
        email_cfg["smtp_use_ssl"] = _bool_value(payload.get("smtp_use_ssl"))

    ai_cfg["default"] = default_ai
    cfg["api_keys"] = api_keys
    cfg["ai_cost_config"] = ai_cfg
    cfg["data_provider_config"] = data_cfg
    cfg["security_config"] = security_cfg
    cfg["email_config"] = email_cfg
    cfg["updated_at"] = _now_iso()
    _save_config(cfg)
    return runtime_config_form()
