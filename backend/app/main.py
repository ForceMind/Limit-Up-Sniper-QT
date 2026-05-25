from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import queue
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.quant.access_audit import (
    access_logs,
    access_security,
    block_ip,
    client_ip_from_request,
    is_ip_blocked,
    record_access,
    unblock_ip,
)
from app.quant.biying_sync import biying_minute_sync
from app.quant.capital_strategy import (
    DEFAULT_FRONTEND_STRATEGY_ID,
    CAPITAL_BANDS,
    apply_capital_constraints,
    capital_presets,
    recommended_strategy_id,
)
from app.quant.data_transfer import (
    DataPackageError,
    clear_sample_quant_state,
    create_safe_data_package,
    import_data_package,
    validate_data_package,
)
from app.quant.database_inspector import database_overview, database_table_rows
from app.quant.engine import DATA_DIR, DEFAULT_AI_MODEL, quant_engine, safe_float
from app.quant.evolution import strategy_evolution
from app.quant.front_profile import (
    resolve_front_profile_updates as _resolve_front_profile_updates,
    strategy_catalog_items as _strategy_catalog_items,
)
from app.quant.lhb_sync import lhb_status
from app.quant.jobs import job_manager
from app.quant.monitoring import ai_failures, ai_records_feed, ai_usage_summary, data_coverage
from app.quant.news_fetcher import news_fetcher
from app.quant.news_repository import latest_news_time as latest_sqlite_news_time
from app.quant.news_repository import lightweight_news_feed
from app.quant.notifier import trade_notifier
from app.quant.runtime_cache import env_int as cache_env_int
from app.quant.runtime_cache import clear_runtime_cache, runtime_cache_status
from app.quant.runtime_cache import load_payload_cache, save_payload_cache
from app.quant.strategy_runtime_matrix import (
    build_strategy_runtime_matrix_payload,
    clean_strategy_runtime_matrix_limit,
    strategy_runtime_catalog_items,
)
from app.routers.admin_access import build_admin_access_router
from app.routers.admin_data_cache import build_admin_data_cache_router
from app.routers.admin_frontend_users import build_admin_frontend_users_router
from app.routers.admin_job_runs import build_admin_job_runs_router
from app.routers.admin_jobs import build_admin_jobs_router
from app.routers.admin_overview import build_admin_overview_router
from app.routers.admin_strategy_runtime import build_admin_strategy_runtime_router
from app.routers.core_system import build_core_system_router
from app.routers.frontend_profile import build_frontend_profile_router
from app.routers.frontend_runtime import build_frontend_runtime_router
from app.routers.frontend_signal import build_frontend_signal_router
from app.routers.quant_basic import build_quant_basic_router
from app.quant.security import (
    admin_create_frontend_user,
    admin_delete_frontend_user,
    admin_reset_frontend_user_password,
    admin_set_frontend_user_disabled,
    admin_update_frontend_user,
    auth_status,
    debug_auth_status,
    ensure_admin_entry_path,
    frontend_user_profile,
    frontend_user_summary,
    login,
    register_frontend_user,
    require_request_scope,
    required_scope_for_api,
    runtime_config_form,
    runtime_config_status,
    setup_auth,
    update_frontend_user_profile,
    update_runtime_config,
    verify_token,
)


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
PROJECT_ROOT = BASE_DIR.parent
BACKUP_DIR = PROJECT_ROOT / "backups"
VERSION_FILE = PROJECT_ROOT / "VERSION"


def _app_version() -> str:
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
        return version or "0.0.0"
    except Exception:
        return os.getenv("QT_APP_VERSION", "0.0.0")


APP_VERSION = _app_version()
_FRONTEND_ACCOUNT_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_FRONTEND_ACCOUNT_CACHE_TTL = 300
_FRONTEND_ACCOUNT_PRECOMPUTE_QUEUE_LOCK = threading.Lock()
_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_LOCK = threading.Lock()
_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING: Dict[str, float] = {}
_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_TASKS: "queue.Queue[Callable[[], None]]" = queue.Queue()
_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKER_LOCK = threading.Lock()
_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED = 0
_MEMORY_PAYLOAD_CACHE: Dict[str, tuple[float, Dict[str, Any]]] = {}
_MEMORY_PAYLOAD_CACHE_MAX = 256
_FRONTEND_ACCOUNT_REPLAY_DAYS = max(20, min(int(safe_float(os.getenv("QT_FRONTEND_ACCOUNT_REPLAY_DAYS"), 90)), 260))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except Exception:
        return default


def _copy_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return copy.deepcopy(payload)
    except Exception:
        return dict(payload)


def _memory_cache_key(payload_type: str, parts: Dict[str, Any]) -> str:
    text = json.dumps({"type": payload_type, "parts": parts}, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
    return f"{payload_type}:{digest}"


def _memory_cache_get(payload_type: str, parts: Dict[str, Any], ttl_seconds: int) -> Optional[Dict[str, Any]]:
    ttl_seconds = max(0, int(ttl_seconds or 0))
    if ttl_seconds <= 0:
        return None
    key = _memory_cache_key(payload_type, parts)
    cached = _MEMORY_PAYLOAD_CACHE.get(key)
    if not cached:
        return None
    created_at, payload = cached
    if time.time() - created_at > ttl_seconds:
        _MEMORY_PAYLOAD_CACHE.pop(key, None)
        return None
    result = _copy_payload(payload)
    result["server_cache"] = "hit"
    result["server_cache_type"] = payload_type
    return result


def _memory_cache_set(payload_type: str, parts: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    if len(_MEMORY_PAYLOAD_CACHE) >= _MEMORY_PAYLOAD_CACHE_MAX:
        oldest = sorted(_MEMORY_PAYLOAD_CACHE.items(), key=lambda item: item[1][0])[: max(1, _MEMORY_PAYLOAD_CACHE_MAX // 8)]
        for key, _item in oldest:
            _MEMORY_PAYLOAD_CACHE.pop(key, None)
    key = _memory_cache_key(payload_type, parts)
    clean = _copy_payload(payload)
    clean.pop("server_cache", None)
    clean.pop("server_cache_type", None)
    _MEMORY_PAYLOAD_CACHE[key] = (time.time(), clean)
    result = _copy_payload(payload)
    result["server_cache"] = "miss"
    result["server_cache_type"] = payload_type
    return result


def _memory_cache_clear(payload_types: Optional[Any] = None) -> None:
    if payload_types is None:
        _MEMORY_PAYLOAD_CACHE.clear()
        return
    if isinstance(payload_types, str):
        target_types = {payload_types}
    else:
        try:
            target_types = {str(item) for item in payload_types if str(item)}
        except Exception:
            target_types = {str(payload_types)}
    if not target_types:
        return
    prefixes = tuple(f"{item}:" for item in target_types)
    for key in list(_MEMORY_PAYLOAD_CACHE.keys()):
        if str(key).startswith(prefixes):
            _MEMORY_PAYLOAD_CACHE.pop(key, None)


def _create_data_backup() -> Dict[str, Any]:
    if not DATA_DIR.exists():
        return {"status": "error", "message": f"data dir not found: {DATA_DIR}"}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"backend_data_{stamp}.tar.gz"
    with tarfile.open(backup_file, "w:gz") as archive:
        archive.add(DATA_DIR, arcname="data")
    return {
        "status": "ok",
        "backup_file": str(backup_file),
        "size_bytes": backup_file.stat().st_size,
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _restart_service_after_response() -> None:
    time.sleep(0.5)
    script = PROJECT_ROOT / "scripts" / "restart_server.sh"
    if not script.exists():
        return
    try:
        subprocess.Popen(
            ["bash", str(script)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except Exception:
        return


def _refresh_quant_caches() -> None:
    for attr in ("_events_cache", "_kline_cache", "_future_return_cache", "_correlation_cache"):
        value = getattr(quant_engine, attr, None)
        if isinstance(value, dict):
            value.clear()
        elif isinstance(value, list):
            value.clear()
    if hasattr(quant_engine, "_cache_source_key"):
        setattr(quant_engine, "_cache_source_key", "")
    if hasattr(quant_engine, "_events_cache_key"):
        setattr(quant_engine, "_events_cache_key", "")
    try:
        quant_engine.clear_market_cache()
    except Exception:
        pass
    _frontend_account_cache_clear()
    _memory_cache_clear()


DATA_IMPORT_JOBS: Dict[str, Dict[str, Any]] = {}
DATA_IMPORT_JOBS_LOCK = threading.Lock()


def _now_shanghai_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _data_import_job_snapshot(job_id: str) -> Dict[str, Any]:
    with DATA_IMPORT_JOBS_LOCK:
        job = dict(DATA_IMPORT_JOBS.get(job_id) or {})
        logs = job.get("logs") if isinstance(job.get("logs"), list) else []
        job["logs"] = list(logs)[-80:]
        return job


def _update_data_import_job(job_id: str, **updates: Any) -> Dict[str, Any]:
    with DATA_IMPORT_JOBS_LOCK:
        job = DATA_IMPORT_JOBS.setdefault(
            job_id,
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "message": "等待开始合并",
                "logs": [],
                "created_at": _now_shanghai_iso(),
            },
        )
        log_message = str(updates.pop("log_message", "") or "").strip()
        job.update({key: value for key, value in updates.items() if value is not None})
        job["updated_at"] = _now_shanghai_iso()
        if log_message:
            logs = job.setdefault("logs", [])
            if isinstance(logs, list):
                logs.append({"ts": job["updated_at"], "message": log_message, "stage": job.get("stage")})
                del logs[:-80]
        return dict(job)


def _data_import_progress(job_id: str, payload: Dict[str, Any]) -> None:
    total_files = int(safe_float(payload.get("total_files"), 0))
    imported_files = int(safe_float(payload.get("imported_files"), 0))
    sqlite_table_count = int(safe_float(payload.get("sqlite_table_count"), 0))
    sqlite_table_index = int(safe_float(payload.get("sqlite_table_index"), 0))
    progress = 35
    if total_files > 0:
        progress = 35 + int(min(58, imported_files / total_files * 58))
    if sqlite_table_count > 0:
        progress = max(progress, 35 + int(min(58, sqlite_table_index / sqlite_table_count * 58)))
    message = str(payload.get("message") or "正在合并数据")
    _update_data_import_job(
        job_id,
        status="running",
        stage=str(payload.get("stage") or "importing"),
        progress=min(93, max(35, progress)),
        message=message,
        imported_files=imported_files or None,
        total_files=total_files or None,
        current_file=payload.get("current_file"),
        sqlite_table=payload.get("sqlite_table"),
        sqlite_table_index=sqlite_table_index or None,
        sqlite_table_count=sqlite_table_count or None,
        added_records=payload.get("added_records"),
        log_message=message,
    )


def _run_data_import_job(job_id: str, upload_file: Path, received: int, backup: bool) -> None:
    backup_result: Dict[str, Any] = {}
    try:
        _update_data_import_job(
            job_id,
            status="running",
            stage="backup",
            progress=20,
            message="正在备份服务器现有数据",
            log_message="正在备份服务器现有数据",
        )
        if backup:
            backup_result = _create_data_backup()
            if backup_result.get("status") != "ok":
                raise RuntimeError(f"导入前备份失败：{backup_result.get('message') or 'unknown'}")
        else:
            _update_data_import_job(job_id, backup_skipped=True)
        _update_data_import_job(
            job_id,
            stage="importing",
            progress=32,
            message="备份完成，开始合并上传数据",
            backup=backup_result,
            log_message="备份完成，开始合并上传数据",
        )
        result = import_data_package(
            upload_file,
            DATA_DIR,
            progress=lambda payload: _data_import_progress(job_id, payload),
        )
        _update_data_import_job(
            job_id,
            stage="refresh",
            progress=96,
            message="数据已合并，正在刷新量化缓存",
            result=result,
            backup=backup_result,
            received_bytes=received,
            log_message="数据已合并，正在刷新量化缓存",
        )
        _refresh_quant_caches()
        result["backup"] = backup_result
        result["received_bytes"] = received
        _update_data_import_job(
            job_id,
            status="done",
            stage="done",
            progress=100,
            message="上传数据已合并完成",
            result=result,
            backup=backup_result,
            received_bytes=received,
            finished_at=_now_shanghai_iso(),
            log_message="上传数据已合并完成",
        )
        job_manager._append_log("warning", "后台已导入数据迁移包", job="admin_data_import", stage="finish", payload=result)
    except Exception as exc:
        payload = {"error": str(exc), "job_id": job_id}
        _update_data_import_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message=f"数据合并失败：{exc}",
            error=str(exc),
            finished_at=_now_shanghai_iso(),
            log_message=f"数据合并失败：{exc}",
        )
        job_manager._append_log("error", "后台数据导入失败", job="admin_data_import", stage="failed", payload=payload)
    finally:
        try:
            upload_file.unlink(missing_ok=True)
        except Exception:
            pass


def _json_fingerprint(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(payload)


def _log_key(item: Dict[str, Any]) -> str:
    return "|".join(
        str(item.get(key) or "")
        for key in ("ts", "job", "stage", "level", "message")
    )


def _latest_news_time_uncached() -> str:
    try:
        latest = latest_sqlite_news_time()
        if latest:
            return latest
    except Exception:
        pass
    try:
        return news_fetcher.latest_history_time()
    except Exception:
        return ""


def _latest_news_time() -> str:
    cache_ttl = cache_env_int("QT_LATEST_NEWS_TIME_CACHE_TTL_SECONDS", 5, minimum=0, maximum=300)
    cache_parts = {"version": APP_VERSION}
    cached = _memory_cache_get("latest_news_time", cache_parts, cache_ttl)
    if cached:
        return str(cached.get("latest_news_time") or "")
    latest = _latest_news_time_uncached()
    _memory_cache_set("latest_news_time", cache_parts, {"latest_news_time": latest})
    return latest


def _data_date_bounds_uncached() -> Dict[str, str]:
    db_path = DATA_DIR / "quant_data.sqlite3"
    first_dates: list[str] = []
    latest_dates: list[str] = []
    if db_path.exists():
        try:
            conn = sqlite3.connect(db_path)
            try:
                for table, column in (
                    ("news_events", "date"),
                    ("news_raw", "date"),
                    ("market_daily_bars", "date"),
                    ("lhb_records", "trade_date"),
                ):
                    try:
                        row = conn.execute(
                            f"SELECT MIN({column}), MAX({column}) FROM {table} WHERE {column} IS NOT NULL AND {column} != ''"
                        ).fetchone()
                    except Exception:
                        continue
                    first = str((row or ["", ""])[0] or "").strip()[:10]
                    latest = str((row or ["", ""])[1] or "").strip()[:10]
                    if first:
                        first_dates.append(first)
                    if latest:
                        latest_dates.append(latest)
            finally:
                conn.close()
        except Exception:
            pass
    first_date = min(first_dates) if first_dates else ""
    latest_date = max(latest_dates) if latest_dates else ""
    allow_engine_fallback = _env_flag("QT_DATA_DATE_ENGINE_FALLBACK_ENABLED", False)
    if allow_engine_fallback and not first_date:
        try:
            first_date = str(quant_engine.first_data_date() or "").strip()[:10]
        except Exception:
            first_date = ""
    if allow_engine_fallback and not latest_date:
        try:
            latest_date = str(quant_engine.latest_event_date() or "").strip()[:10]
        except Exception:
            latest_date = ""
    if not latest_date:
        latest_date = datetime.now().strftime("%Y-%m-%d")
    return {"first": first_date, "latest": latest_date}


def _data_date_bounds() -> Dict[str, str]:
    cache_ttl = cache_env_int("QT_DATA_DATE_CACHE_TTL_SECONDS", 10, minimum=0, maximum=300)
    cache_parts = {"version": APP_VERSION, "data_dir": str(DATA_DIR)}
    cached = _memory_cache_get("data_date_bounds", cache_parts, cache_ttl)
    if cached:
        return {"first": str(cached.get("first") or ""), "latest": str(cached.get("latest") or "")}
    return _memory_cache_set("data_date_bounds", cache_parts, _data_date_bounds_uncached())


def _latest_data_date() -> str:
    return str(_data_date_bounds().get("latest") or "")


def _first_data_date() -> str:
    return str(_data_date_bounds().get("first") or "")


def _git_ref() -> Dict[str, str]:
    if not (PROJECT_ROOT / ".git").exists():
        return {"branch": "", "commit": "", "ref": ""}
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        commit = subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        ref = f"{branch}@{commit}" if branch or commit else ""
        return {"branch": branch, "commit": commit, "ref": ref}
    except Exception:
        return {"branch": "", "commit": "", "ref": ""}


def app_version_payload() -> Dict[str, Any]:
    return {
        "status": "ok",
        "app": "涨停狙击手",
        "version": APP_VERSION,
        "backend_version": APP_VERSION,
        "frontend_version": APP_VERSION,
        "git": _git_ref(),
    }

app = FastAPI(title="Limit Up Sniper Quant System", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

static_dir = FRONTEND_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    started = time.perf_counter()
    auth_payload: Optional[Dict[str, Any]] = None
    status_code = 500
    required_scope = required_scope_for_api(request.url.path, request.method)
    try:
        if is_ip_blocked(client_ip_from_request(request)):
            status_code = 403
            return JSONResponse({"detail": "当前 IP 已被访问审计封禁"}, status_code=403)
        if required_scope:
            try:
                auth_payload = require_request_scope(request, required_scope)
            except HTTPException as exc:
                status_code = exc.status_code
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        else:
            authorization = request.headers.get("authorization") or request.headers.get("Authorization") or ""
            token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
            token = token or str(request.headers.get("x-qt-token") or "").strip()
            if token:
                try:
                    auth_payload = verify_token(token, "frontend")
                except HTTPException:
                    auth_payload = None
        request.state.auth_payload = auth_payload
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        record_access(request, status_code, (time.perf_counter() - started) * 1000, auth_payload)


@app.on_event("startup")
async def startup_jobs():
    if _env_flag("QT_MANUAL_TASK_START_ONLY", default=True):
        job_manager.mark_scheduler_disabled("QT_MANUAL_TASK_START_ONLY=1，服务重启后只允许手动启动任务")
    elif _env_flag("QUANT_SCHEDULER_ENABLED", default=False):
        job_manager.start()
    else:
        job_manager.mark_scheduler_disabled("QUANT_SCHEDULER_ENABLED=0")


@app.on_event("shutdown")
async def shutdown_jobs():
    await job_manager.stop()


def _debug_status_payload(request: Request) -> Dict[str, Any]:
    payload = getattr(request.state, "auth_payload", None)
    return {
        "status": "ok",
        "debug_auth": debug_auth_status(),
        "auth": {
            "scope": str((payload or {}).get("scope") or ""),
            "sub": str((payload or {}).get("sub") or ""),
            "debug": bool((payload or {}).get("debug")),
            "write_allowed": bool((payload or {}).get("write_allowed")),
        },
        "version": app_version_payload(),
    }


def _debug_routes_payload() -> Dict[str, Any]:
    paths = app.openapi().get("paths", {})
    modules: Dict[str, Dict[str, int]] = {}
    for path, operations in paths.items():
        if not isinstance(operations, dict):
            continue
        parts = [part for part in str(path).split("/") if part]
        module = parts[1] if len(parts) > 1 and parts[0] == "api" else "other"
        bucket = modules.setdefault(module, {"paths": 0, "operations": 0})
        bucket["paths"] += 1
        bucket["operations"] += len(operations)
    return {
        "status": "ok",
        "path_count": len(paths),
        "operation_count": sum(len(value) for value in paths.values() if isinstance(value, dict)),
        "modules": modules,
    }


def _auth_login_payload(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    return login(payload, request)


def _auth_register_payload(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    result = register_frontend_user(payload, request)
    username = str(result.get("username") or "").strip()
    if username:
        try:
            profile_payload = frontend_user_profile(username)
            _record_user_follow_period(username, profile_payload.get("profile"), source="front_register", reason="register", created_at=profile_payload.get("created_at"))
            result["account_precompute"] = _queue_frontend_account_precompute_for_user(
                username,
                reason="register",
                start_worker=False,
                async_enqueue=True,
            )
        except Exception:
            pass
    return result


def _request_username(request: Request) -> str:
    payload = getattr(request.state, "auth_payload", None)
    if not isinstance(payload, dict):
        payload = require_request_scope(request, "frontend")
    return str(payload.get("sub") or "").strip()


def _frontend_profile_payload(request: Request):
    return frontend_user_profile(_request_username(request))


def _frontend_profile_update_payload(
    request: Request,
    payload: Dict[str, Any],
    include_catalog: bool,
):
    started = time.time()
    last_stage_at = started
    profile_trace: list[Dict[str, Any]] = []

    def mark_profile_stage(stage: str) -> None:
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

    username = _request_username(request)
    previous = {}
    try:
        previous_payload = frontend_user_profile(username)
        previous = previous_payload.get("profile") if isinstance(previous_payload.get("profile"), dict) else {}
    except Exception:
        previous = {}
    mark_profile_stage("load_previous_profile")
    updates, resolved_model = _resolve_front_profile_updates(
        dict(payload) if isinstance(payload, dict) else {},
        previous,
        include_catalog,
        _frontend_strategy_models_payload,
        strategy_evolution.model,
    )
    mark_profile_stage("resolve_updates")
    result = update_frontend_user_profile(username, updates)
    mark_profile_stage("save_profile")
    context = _frontend_profile_context(
        request,
        include_catalog=include_catalog,
        fallback_catalog_on_missing=include_catalog,
        profile_payload=result,
        resolved_model=resolved_model,
    )
    mark_profile_stage("build_profile_context")
    follow_reason = _follow_period_reason(previous, context.get("profile"))
    follow_period_record = _queue_user_follow_period_record(
        username,
        context.get("profile"),
        previous_profile=previous,
        source="front_profile",
        reason=follow_reason,
        created_at=context.get("created_at"),
    )
    mark_profile_stage("queue_follow_period")
    account_precompute = _queue_frontend_account_precompute_for_user(
        username,
        reason=follow_reason,
        start_worker=False,
        async_enqueue=True,
    )
    mark_profile_stage("queue_account_precompute")
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


def _config_update_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = update_runtime_config(payload)
    job_manager._append_log("warning", "后台运行配置已保存", job="admin_config", stage="saved")
    return result


def _status_payload() -> Dict[str, Any]:
    now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
    latest_news_time = _latest_news_time()
    data_bounds = _data_date_bounds()
    data_date = latest_news_time[:10] if latest_news_time else quant_engine.latest_event_date()
    return {
        "status": "ok",
        "system": "quant",
        "app": "涨停狙击手",
        "version": APP_VERSION,
        "backend_version": APP_VERSION,
        "frontend_version": APP_VERSION,
        "data_dir": str(DATA_DIR),
        "current_date": now_cn.strftime("%Y-%m-%d"),
        "current_time": now_cn.isoformat(timespec="seconds"),
        "latest_event_date": data_date,
        "latest_news_time": latest_news_time,
        "data_date": data_date,
        "first_data_date": data_bounds.get("first", ""),
        "latest_data_date": data_bounds.get("latest", ""),
        "data_date_bounds": data_bounds,
        "ai_model": DEFAULT_AI_MODEL,
        "jobs": _frontend_light_jobs(job_manager.frontend_status()),
    }


app.include_router(
    build_core_system_router(
        version_payload=app_version_payload,
        auth_status_payload=auth_status,
        debug_status_payload=_debug_status_payload,
        debug_routes_payload=_debug_routes_payload,
        auth_setup_payload=setup_auth,
        auth_login_payload=_auth_login_payload,
        auth_register_payload=_auth_register_payload,
        config_status_payload=runtime_config_status,
        config_runtime_payload=runtime_config_form,
        config_update_payload=_config_update_payload,
        status_payload=_status_payload,
    )
)


def _light_status_payload(
    as_of: Optional[str] = None,
    jobs_payload: Optional[Dict[str, Any]] = None,
    include_data_dir: bool = True,
) -> Dict[str, Any]:
    now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
    latest_news_time = _latest_news_time()
    data_bounds = _data_date_bounds()
    data_date = str(as_of or "").strip() or (latest_news_time[:10] if latest_news_time else "")
    jobs = jobs_payload if isinstance(jobs_payload, dict) else {}
    payload = {
        "status": "ok",
        "system": "quant",
        "app": "涨停狙击手",
        "version": APP_VERSION,
        "backend_version": APP_VERSION,
        "frontend_version": APP_VERSION,
        "current_date": now_cn.strftime("%Y-%m-%d"),
        "current_time": now_cn.isoformat(timespec="seconds"),
        "latest_event_date": data_date,
        "latest_news_time": latest_news_time,
        "data_date": data_date,
        "first_data_date": data_bounds.get("first", ""),
        "latest_data_date": data_bounds.get("latest", ""),
        "data_date_bounds": data_bounds,
        "ai_model": DEFAULT_AI_MODEL,
        "jobs": jobs,
    }
    if include_data_dir:
        payload["data_dir"] = str(DATA_DIR)
    return payload


def _frontend_light_jobs(jobs_payload: Dict[str, Any]) -> Dict[str, Any]:
    jobs = jobs_payload if isinstance(jobs_payload, dict) else {}
    return {
        "scheduler": jobs.get("scheduler", {}),
        "running": jobs.get("running", {}),
        "paused_jobs": jobs.get("paused_jobs", {}),
    }


def _frontend_jobs_payload() -> Dict[str, Any]:
    cache_ttl = cache_env_int("QT_FRONT_JOBS_CACHE_TTL_SECONDS", 3, minimum=0, maximum=60)
    cache_parts = {"version": APP_VERSION}
    cached = _memory_cache_get("front_jobs", cache_parts, cache_ttl)
    if cached:
        return _frontend_light_jobs(cached)
    payload = _frontend_light_jobs(job_manager.frontend_status())
    _memory_cache_set("front_jobs", cache_parts, payload)
    return payload


def _jobs_status_payload(light: bool = True) -> Dict[str, Any]:
    payload = job_manager.status(light=light)
    if isinstance(payload, dict):
        payload["frontend_account_precompute_queue"] = _frontend_account_precompute_queue_status()
        payload["frontend_account_precompute_async"] = _frontend_account_precompute_async_status()
    return payload


def _safe_news_feed(**kwargs: Any) -> Dict[str, Any]:
    try:
        lightweight = lightweight_news_feed(**kwargs)
        if isinstance(lightweight, dict):
            return lightweight
    except Exception as exc:
        job_manager._append_log("warning", f"轻量新闻快照读取失败，回退完整引擎：{exc}", job="frontend_snapshot", stage="news_light")
    try:
        return quant_engine.news_feed(**kwargs)
    except Exception as exc:
        job_manager._append_log("error", f"新闻快照读取失败：{exc}", job="frontend_snapshot", stage="news")
        return {
            "status": "error",
            "items": [],
            "events": [],
            "count": 0,
            "error": "news feed unavailable",
        }


def _frontend_light_news_feed(**kwargs: Any) -> Dict[str, Any]:
    try:
        lightweight = lightweight_news_feed(**kwargs)
        if isinstance(lightweight, dict):
            return lightweight
    except Exception as exc:
        job_manager._append_log("warning", f"前台轻量新闻快照读取失败，返回空快照：{exc}", job="frontend_snapshot", stage="news_light")
    return {
        "status": "pending",
        "items": [],
        "events": [],
        "count": 0,
        "message": "lightweight news unavailable",
    }


def _market_sentiment(news_payload: Dict[str, Any]) -> Dict[str, Any]:
    events = news_payload.get("events") if isinstance(news_payload.get("events"), list) else []
    scores = [float(item.get("sentiment") or 0) for item in events if isinstance(item, dict)]
    avg = sum(scores) / len(scores) if scores else 0.0
    positive = sum(1 for value in scores if value > 0)
    negative = sum(1 for value in scores if value < 0)
    if avg >= 0.12:
        label = "偏暖"
    elif avg <= -0.12:
        label = "偏冷"
    else:
        label = "中性"
    return {
        "label": label,
        "score": round(avg, 4),
        "positive_count": positive,
        "negative_count": negative,
        "sample_count": len(scores),
    }


def _active_strategy_model() -> Dict[str, Any]:
    return {
        "id": "active",
        "name": "系统默认基础参数（非跟随策略）",
        "source": "baseline",
        "reusable": False,
        "description": "用于人工调参、诊断和生成新策略的默认参数模板；每个策略模型仍保存自己的独立基础参数。",
        "params": quant_engine.strategy_params(),
        "strategy_source": quant_engine.strategy_source(),
    }


def _frontend_strategy_models_payload(include_catalog: bool = True) -> Dict[str, Any]:
    cache_parts = {"include_catalog": bool(include_catalog), "version": APP_VERSION}
    cache_ttl = cache_env_int("QT_STRATEGY_MODELS_CACHE_TTL_SECONDS", 60, minimum=0, maximum=3600)
    cached = _memory_cache_get("strategy_models", cache_parts, cache_ttl)
    if cached:
        return cached
    if include_catalog:
        payload = strategy_evolution.models(limit=40, include_records=False)
    else:
        payload = {"status": "ok", "active": _active_strategy_model(), "items": [], "count": 0}
    if not isinstance(payload, dict):
        payload = {"status": "ok", "active": _active_strategy_model(), "items": [], "count": 0}
    base_params = quant_engine.strategy_params()
    presets = capital_presets(base_params)
    runtime_summaries = strategy_evolution.runtime_model_summaries(presets) if include_catalog else {}
    enriched_presets: list[Dict[str, Any]] = []
    for preset in presets:
        model_id = str(preset.get("id") or "")
        summary = runtime_summaries.get(model_id)
        if summary:
            enriched = {**preset, **summary}
            enriched["runtime_data_note"] = (
                f"已复盘 {summary.get('runtime_start_date') or '-'} 至 "
                f"{summary.get('runtime_end_date') or '-'}，"
                f"{int(safe_float(summary.get('trade_count'), 0))} 笔成交"
            )
        elif include_catalog:
            enriched = {
                **preset,
                "runtime_data_status": "missing",
                "has_runtime_data": False,
                "runtime_data_note": "等待本地或服务器策略复盘生成数据",
            }
        else:
            enriched = {
                **preset,
                "runtime_data_status": "not_loaded",
                "runtime_data_summary_loaded": False,
            }
        enriched_presets.append(enriched)
    payload["active"] = {**_active_strategy_model(), **(payload.get("active") if isinstance(payload.get("active"), dict) else {})}
    payload["active"]["name"] = "系统默认基础参数（非跟随策略）"
    payload["capital_presets"] = enriched_presets
    payload["capital_bands"] = CAPITAL_BANDS
    payload["catalog_included"] = bool(include_catalog)
    payload["capital_runtime_summary"] = {
        "total": len(enriched_presets),
        "ready": sum(1 for item in enriched_presets if item.get("has_runtime_data")),
        "missing": sum(1 for item in enriched_presets if not item.get("has_runtime_data")),
    }
    payload["count"] = int(safe_float(payload.get("count"), 0)) + len(enriched_presets)
    return _memory_cache_set("strategy_models", cache_parts, payload)


def _admin_model_signal_feed(
    as_of: Optional[str],
    models_payload: Optional[Dict[str, Any]] = None,
    limit_models: int = 24,
    limit_per_model: int = 12,
) -> Dict[str, Any]:
    payload = strategy_evolution.model_signal_feed(
        as_of=as_of,
        limit_models=limit_models,
        limit_per_model=limit_per_model,
        fallback_latest=True,
    )
    catalog_payload = models_payload if isinstance(models_payload, dict) else _frontend_strategy_models_payload(include_catalog=True)
    catalog_items = _strategy_catalog_items(catalog_payload)
    catalog = {str(item.get("id") or ""): item for item in catalog_items}
    catalog_order = {str(item.get("id") or ""): index for index, item in enumerate(catalog_items)}
    for group in payload.get("items") if isinstance(payload.get("items"), list) else []:
        if not isinstance(group, dict):
            continue
        model_id = str(group.get("model_id") or "")
        meta = catalog.get(model_id)
        if not isinstance(meta, dict):
            continue
        group["model_name"] = str(meta.get("name") or group.get("model_name") or model_id)
        group["model_description"] = str(meta.get("description") or "")
        group["model_source"] = str(meta.get("source") or group.get("source") or "")
        for key in ("objective", "return_pct", "max_drawdown_pct", "win_rate", "closed_trades"):
            if group.get(key) in (None, "", 0) and meta.get(key) not in (None, ""):
                group[key] = meta.get(key)
        if meta.get("capital_min") is not None:
            group["capital_min"] = meta.get("capital_min")
        if meta.get("capital_max") is not None:
            group["capital_max"] = meta.get("capital_max")
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    items.sort(
        key=lambda group: (
            catalog_order.get(str(group.get("model_id") or ""), 999999),
            -safe_float(group.get("objective"), 0),
            str(group.get("model_id") or ""),
        )
    )
    return payload


def _admin_strategy_runtime_matrix_payload(
    as_of: Optional[str] = None,
    limit_models: int = 80,
    include_signals: bool = True,
) -> Dict[str, Any]:
    effective_as_of = _frontend_account_as_of(as_of)
    clean_limit = clean_strategy_runtime_matrix_limit(limit_models)
    models_payload = _frontend_strategy_models_payload(include_catalog=True)
    catalog_items = strategy_runtime_catalog_items(models_payload, clean_limit)
    runtime_summaries = strategy_evolution.runtime_model_summaries(catalog_items)
    signal_feed = (
        _admin_model_signal_feed(
            effective_as_of,
            models_payload=models_payload,
            limit_models=min(clean_limit, 80),
            limit_per_model=1,
        )
        if include_signals
        else {"status": "skipped", "items": [], "data_date": ""}
    )
    return build_strategy_runtime_matrix_payload(
        effective_as_of=effective_as_of,
        catalog_items=catalog_items,
        runtime_summaries=runtime_summaries,
        signal_feed=signal_feed,
        include_signals=include_signals,
    )


def _quant_db_scalar(sql: str, params: Optional[list[Any]] = None) -> Any:
    db_path = DATA_DIR / "quant_data.sqlite3"
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(sql, params or []).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    return row[0] if row else None


def _quant_db_count(table: str) -> int:
    if not table.replace("_", "").isalnum():
        return 0
    value = _quant_db_scalar(f"SELECT COUNT(*) FROM {table}")
    return int(safe_float(value, 0))


def _light_dashboard_payload(
    as_of: Optional[str],
    news_payload: Optional[Dict[str, Any]] = None,
    model_signals: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    signal_items: list[Dict[str, Any]] = []
    for group in model_signals.get("items") if isinstance(model_signals, dict) and isinstance(model_signals.get("items"), list) else []:
        if not isinstance(group, dict):
            continue
        for signal in group.get("signals") if isinstance(group.get("signals"), list) else []:
            if not isinstance(signal, dict):
                continue
            signal_items.append(
                {
                    **signal,
                    "model_id": group.get("model_id"),
                    "model_name": group.get("model_name"),
                    "action": signal.get("action") or "买入候选",
                }
            )
    signal_items.sort(key=lambda item: safe_float(item.get("buy_score"), 0), reverse=True)
    news_items = news_payload.get("items") if isinstance(news_payload, dict) and isinstance(news_payload.get("items"), list) else []
    news_events = news_payload.get("events") if isinstance(news_payload, dict) and isinstance(news_payload.get("events"), list) else []
    kline_stock_count = _quant_db_scalar("SELECT COUNT(DISTINCT code) FROM market_daily_bars WHERE code IS NOT NULL AND code != ''")
    return {
        "status": "ok",
        "as_of": str(as_of or (model_signals or {}).get("data_date") or ""),
        "data": {
            "news_count": _quant_db_count("news_raw") or len(news_items),
            "ai_record_count": _quant_db_count("news_analysis"),
            "event_count": _quant_db_count("news_events") or len(news_events),
            "stock_count": len(getattr(quant_engine.universe, "code_to_name", {}) or {}),
            "kline_stock_count": int(safe_float(kline_stock_count, 0)),
            "lhb_record_count": _quant_db_count("lhb_records"),
        },
        "recommendations": {
            "status": "ok",
            "as_of": (model_signals or {}).get("data_date") or as_of,
            "items": signal_items[:30],
            "latest_events": news_events[:60],
            "source": "strategy_daily_signals",
        },
        "timeline": {},
        "portfolio": {},
        "strategy_params": quant_engine.strategy_params(),
        "strategy_source": quant_engine.strategy_source(),
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "light": True,
    }


def _frontend_profile_context_for_username(
    username: str,
    include_catalog: bool = True,
    fallback_catalog_on_missing: bool = True,
    profile_payload: Optional[Dict[str, Any]] = None,
    resolved_model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    username = str(username or "").strip()
    if not isinstance(profile_payload, dict):
        profile_payload = frontend_user_profile(username)
    profile = profile_payload.get("profile") if isinstance(profile_payload.get("profile"), dict) else {}
    simulated_cash = max(10_000.0, min(10_000_000.0, safe_float(profile.get("simulated_cash"), 10_000.0)))
    original_selected_id = str(profile.get("strategy_model_id") or "").strip()
    selected_id = original_selected_id
    models_payload = _frontend_strategy_models_payload(include_catalog=include_catalog)
    model_items = _strategy_catalog_items(models_payload)
    selected = next((item for item in model_items if str(item.get("id")) == selected_id), None)
    if selected is None and isinstance(resolved_model, dict) and str(resolved_model.get("id") or "") == selected_id:
        selected = resolved_model
        model_items.append(selected)
        items = models_payload.setdefault("items", [])
        if isinstance(items, list) and not any(str(item.get("id") or "") == selected_id for item in items if isinstance(item, dict)):
            items.append(selected)
    if not include_catalog and selected_id and selected_id != "active" and selected is None:
        try:
            selected = strategy_evolution.model(selected_id, include_records=False) or None
        except Exception:
            selected = None
        if selected:
            model_items.append(selected)
            items = models_payload.setdefault("items", [])
            if isinstance(items, list) and not any(str(item.get("id") or "") == selected_id for item in items if isinstance(item, dict)):
                items.append(selected)
        elif fallback_catalog_on_missing:
            models_payload = _frontend_strategy_models_payload(include_catalog=True)
            model_items = _strategy_catalog_items(models_payload)
            selected = next((item for item in model_items if str(item.get("id")) == selected_id), None)
    recommended_id = recommended_strategy_id(simulated_cash, model_items)
    should_recommend = (
        not selected_id
        or selected_id == "active"
        or selected is None
    )
    if should_recommend:
        selected_id = recommended_id or DEFAULT_FRONTEND_STRATEGY_ID
        selected = next((item for item in model_items if str(item.get("id")) == selected_id), None)
    if not selected:
        selected = _active_strategy_model()
        selected_id = "active"
        profile["strategy_model_id"] = selected_id
    params = quant_engine.strategy_params((selected or {}).get("params") if isinstance((selected or {}).get("params"), dict) else {})
    params = apply_capital_constraints(params, simulated_cash)
    profile["simulated_cash"] = round(simulated_cash, 2)
    profile["recommended_strategy_model_id"] = recommended_id
    profile["capital_mode"] = str(params.get("capital_mode") or "")
    profile["capital_label"] = str(params.get("capital_label") or "")
    if original_selected_id != selected_id:
        profile["strategy_model_id"] = selected_id
        try:
            update_frontend_user_profile(
                username,
                {
                    "simulated_cash": profile["simulated_cash"],
                    "strategy_model_id": selected_id,
                },
            )
        except Exception:
            pass
    models_payload["selected_model_id"] = selected_id
    models_payload["recommended_model_id"] = recommended_id
    return {
        "username": username,
        "created_at": str(profile_payload.get("created_at") or ""),
        "profile_updated_at": str(profile_payload.get("profile_updated_at") or ""),
        "profile": profile,
        "models_payload": models_payload,
        "followed_model": selected or {},
        "strategy_params": params,
    }


def _frontend_profile_context(
    request: Request,
    include_catalog: bool = True,
    fallback_catalog_on_missing: bool = True,
    profile_payload: Optional[Dict[str, Any]] = None,
    resolved_model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _frontend_profile_context_for_username(
        _request_username(request),
        include_catalog=include_catalog,
        fallback_catalog_on_missing=fallback_catalog_on_missing,
        profile_payload=profile_payload,
        resolved_model=resolved_model,
    )


def _frontend_full_model(model_id: str) -> Dict[str, Any]:
    model_id = str(model_id or "active").strip() or "active"
    return strategy_evolution.model(model_id, include_records=True) or {}


def _affordable_payload(payload: Dict[str, Any], context: Dict[str, Any], as_of: Optional[str]) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
    cash = safe_float(profile.get("simulated_cash"), params.get("account_initial_cash", 0))
    max_positions = max(1.0, safe_float(params.get("max_positions"), 1))
    position_cash = min(safe_float(params.get("paper_position_value"), cash), cash / max_positions if max_positions else cash)
    if cash <= 0:
        return payload

    def enrich(item: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(item)
        code = str(row.get("code") or "").strip()
        price = safe_float(row.get("price") or row.get("current") or row.get("close"), 0)
        if price <= 0 and code:
            latest = quant_engine.latest_price(code, as_of=as_of)
            price = safe_float((latest or {}).get("close"), 0)
        lot_amount = price * 100 if price > 0 else 0.0
        max_qty = math.floor(position_cash / price / 100) * 100 if price > 0 else 0
        affordable = bool(price > 0 and max_qty >= 100 and lot_amount <= cash)
        row["estimated_price"] = round(price, 3) if price > 0 else 0.0
        row["min_lot_amount"] = round(lot_amount, 2)
        row["max_buy_qty"] = int(max_qty)
        row["affordable"] = affordable
        if not affordable:
            row["capital_note"] = "模拟资金不足以买入一手" if price > 0 else "缺少可用行情，暂不能估算一手金额"
        elif cash <= 50_000:
            row["capital_note"] = "小资金可买一手"
        return row

    next_payload = dict(payload)
    for key in ("items", "buy_list"):
        values = payload.get(key)
        if isinstance(values, list):
            enriched = [enrich(item) if isinstance(item, dict) else item for item in values]
            if cash <= 50_000:
                enriched.sort(
                    key=lambda item: (
                        0 if isinstance(item, dict) and item.get("affordable") else 1,
                        -safe_float(item.get("buy_score"), 0) if isinstance(item, dict) else 0,
                    )
                )
            next_payload[key] = enriched
    next_payload["capital_filter"] = {
        "simulated_cash": round(cash, 2),
        "position_cash": round(position_cash, 2),
        "max_positions": int(max_positions),
        "small_cash_mode": cash <= 50_000,
    }
    if isinstance(params, dict):
        next_payload["strategy_params"] = {**(next_payload.get("strategy_params") if isinstance(next_payload.get("strategy_params"), dict) else {}), **params}
    return next_payload


def _scale_model_trades_for_cash(model: Dict[str, Any], target_cash: float) -> list[Dict[str, Any]]:
    trades = model.get("trade_records") if isinstance(model.get("trade_records"), list) else []
    if not trades:
        return []
    backtest = model.get("backtest") if isinstance(model.get("backtest"), dict) else {}
    params = model.get("params") if isinstance(model.get("params"), dict) else {}
    base_cash = safe_float(backtest.get("initial_cash"), safe_float(params.get("account_initial_cash"), target_cash))
    scale = target_cash / base_cash if base_cash > 0 and target_cash > 0 else 1.0
    if abs(scale - 1.0) < 0.0001:
        return [dict(trade) for trade in trades if isinstance(trade, dict)]
    scaled: list[Dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        qty = safe_float(trade.get("qty"), 0)
        price = safe_float(trade.get("price"), 0)
        scaled_qty = math.floor(qty * scale / 100) * 100 if qty > 0 else 0
        if scaled_qty <= 0 or price <= 0:
            continue
        item = dict(trade)
        item["qty"] = scaled_qty
        item["amount"] = round(scaled_qty * price, 2)
        item["scaled_for_cash"] = round(target_cash, 2)
        scaled.append(item)
    return scaled


def _frontend_account_cache_get(key: str) -> Optional[Dict[str, Any]]:
    cached = _FRONTEND_ACCOUNT_CACHE.get(key)
    if not cached:
        return None
    ts, payload = cached
    if time.time() - ts > _FRONTEND_ACCOUNT_CACHE_TTL:
        _FRONTEND_ACCOUNT_CACHE.pop(key, None)
        return None
    return dict(payload)


def _frontend_account_cache_set(key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if len(_FRONTEND_ACCOUNT_CACHE) > 64:
        oldest = sorted(_FRONTEND_ACCOUNT_CACHE.items(), key=lambda item: item[1][0])[:16]
        for old_key, _item in oldest:
            _FRONTEND_ACCOUNT_CACHE.pop(old_key, None)
    _FRONTEND_ACCOUNT_CACHE[key] = (time.time(), dict(payload))
    return payload


def _frontend_account_cache_clear() -> None:
    _FRONTEND_ACCOUNT_CACHE.clear()


def _follow_period_reason(previous: Any, current: Any) -> str:
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


def _record_user_follow_period(
    username: str,
    profile: Any,
    previous_profile: Optional[Dict[str, Any]] = None,
    source: str = "",
    reason: str = "",
    created_at: Any = "",
) -> Dict[str, Any]:
    if not isinstance(profile, dict):
        return {"status": "invalid"}
    return strategy_evolution.record_user_follow_period(
        username,
        profile,
        reason=reason or "profile_sync",
        source=source or "frontend_profile",
        previous_profile=previous_profile,
        created_at=str(created_at or ""),
    )


def _queue_user_follow_period_record(
    username: str,
    profile: Any,
    previous_profile: Optional[Dict[str, Any]] = None,
    source: str = "",
    reason: str = "",
    created_at: Any = "",
) -> Dict[str, Any]:
    if not _env_flag("QT_FRONT_PROFILE_FOLLOW_PERIOD_ASYNC", True):
        return _record_user_follow_period(
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
    profile_copy = _copy_payload(profile)
    previous_copy = _copy_payload(previous_profile) if isinstance(previous_profile, dict) else None
    reason_text = str(reason or "profile_sync")
    source_text = str(source or "frontend_profile")
    created_text = str(created_at or "")

    def worker() -> None:
        try:
            _record_user_follow_period(
                clean_username,
                profile_copy,
                previous_profile=previous_copy,
                source=source_text,
                reason=reason_text,
                created_at=created_text,
            )
        except Exception as exc:
            job_manager._append_log(
                "warning",
                f"用户跟随周期异步记录失败：{exc}",
                job="front_profile",
                stage="follow_period",
                payload={"username": clean_username, "reason": reason_text, "source": source_text},
            )

    threading.Thread(target=worker, name=f"qt-follow-period-{clean_username}", daemon=True).start()
    return {"status": "queued", "async": True, "username": clean_username, "reason": reason_text, "source": source_text}


def _frontend_user_with_diagnostics(item: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(item)
    username = str(row.get("username") or "").strip()
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
    if username and profile:
        period = _record_user_follow_period(
            username,
            profile,
            source="admin_user_summary",
            reason="admin_summary_sync",
            created_at=row.get("created_at"),
        )
        diagnostic = strategy_evolution.user_follow_diagnostics(username, profile)
        if period.get("status") == "ok" and not diagnostic.get("current_period"):
            diagnostic["current_period"] = period
        row["follow_period"] = diagnostic.get("current_period") or period
        row["account_diagnostic"] = diagnostic
    return row


def _admin_frontend_user_summary() -> Dict[str, Any]:
    payload = frontend_user_summary()
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    enriched = [_frontend_user_with_diagnostics(item) for item in items if isinstance(item, dict)]
    next_payload = dict(payload)
    next_payload["items"] = enriched
    next_payload["account_snapshot_count"] = sum(1 for item in enriched if (item.get("account_diagnostic") or {}).get("account_snapshot"))
    return next_payload


def _frontend_account_as_of(as_of: Optional[str]) -> Optional[str]:
    latest = str(_latest_data_date() or "").strip()
    requested = str(as_of or "").strip()
    if requested and latest and requested > latest:
        return latest
    return requested or latest or None


def _frontend_replay_start_date(end_date: Optional[str]) -> Optional[str]:
    first = str(_first_data_date() or "").strip()
    if not end_date:
        return first or None
    try:
        start = datetime.strptime(end_date[:10], "%Y-%m-%d") - timedelta(days=_FRONTEND_ACCOUNT_REPLAY_DAYS)
        start_text = start.strftime("%Y-%m-%d")
        return max(first, start_text) if first else start_text
    except Exception:
        return first or None


def _frontend_follow_start_date(context: Dict[str, Any], end_date: Optional[str]) -> Optional[str]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    candidates = [
        str(profile.get("follow_start_date") or "").strip()[:10],
        str(profile.get("follow_started_at") or "").strip()[:10],
        str(context.get("created_at") or "").strip()[:10],
    ]
    first = str(_first_data_date() or "").strip()
    latest = str(end_date or _latest_data_date() or "").strip()[:10]
    start = next((item for item in candidates if item), "")
    if not start:
        start = latest or first
    if first and start < first:
        start = first
    if latest and start > latest:
        start = latest
    return start or first or latest or None


def _frontend_followed_model_version(context: Dict[str, Any]) -> str:
    model = context.get("followed_model") if isinstance(context.get("followed_model"), dict) else {}
    record_counts = model.get("record_counts") if isinstance(model.get("record_counts"), dict) else {}
    return "|".join(
        [
            str(model.get("id") or ""),
            str(model.get("run_id") or ""),
            str(model.get("generated_at") or ""),
            str(model.get("rank") or ""),
            json.dumps(record_counts, ensure_ascii=False, sort_keys=True, default=str),
        ]
    )


def _frontend_payload_cache_parts(context: Dict[str, Any], payload_type: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    return {
        "payload_type": payload_type,
        "strategy_model_id": str(profile.get("strategy_model_id") or ""),
        "simulated_cash": round(safe_float(profile.get("simulated_cash"), 0), 2),
        "follow_start_date": str(profile.get("follow_start_date") or ""),
        "follow_started_at": str(profile.get("follow_started_at") or ""),
        "model_version": _frontend_followed_model_version(context),
        "strategy_params": context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {},
        **extra,
    }


def _frontend_payload_cache_ttl(name: str, default: int) -> int:
    return cache_env_int(name, default, minimum=0, maximum=86400)


def _frontend_payload_precompute_enabled() -> bool:
    return _env_flag("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", False)


def _frontend_payload_auto_precompute_on_miss() -> bool:
    return _frontend_payload_precompute_enabled() and _env_flag("QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS", False)


def _deferred_job_response_state(
    job_result: Dict[str, Any],
    default_message: str,
    cache_state: str = "miss_deferred",
) -> tuple[str, str, str]:
    result = job_result if isinstance(job_result, dict) else {}
    status = str(result.get("status") or "").strip().lower()
    message = str(result.get("message") or default_message)
    if status == "busy":
        return "busy", "busy", message
    if status == "paused":
        return "paused", "paused", message
    if status == "disabled":
        return "pending", "disabled", message
    if status in {"error", "failed"}:
        return "error", "error", message
    if status == "running" and not (result.get("background") or result.get("process_pid")):
        return "running", "running", message
    return "pending", cache_state, default_message


def _queue_frontend_payload_precompute(
    context: Dict[str, Any],
    effective_as_of: Optional[str],
    lookback_days: int = 2,
    top_n: int = 30,
    limit_days: int = 30,
    force: bool = False,
) -> Dict[str, Any]:
    username = str(context.get("username") or "").strip()
    auto_on_miss = _frontend_payload_auto_precompute_on_miss()
    if not auto_on_miss:
        return {
            "status": "disabled",
            "job": "frontend_payload_precompute",
            "background": False,
            "process": False,
            "queued": False,
            "message": "缓存未命中自动预计算已关闭，请在后台手动预计算前台缓存。",
            "frontend_payload_precompute_enabled": _frontend_payload_precompute_enabled(),
            "frontend_payload_auto_precompute_on_miss": auto_on_miss,
        }
    try:
        return job_manager.run_frontend_payload_precompute(
            as_of=effective_as_of,
            usernames=[username] if username else None,
            limit_users=1 if username else cache_env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS", 8, minimum=1, maximum=500),
            force=force,
            background=True,
            process=_env_flag("QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED", True),
            lookback_days=lookback_days,
            top_n=top_n,
            limit_days=limit_days,
            max_seconds=cache_env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS", 20, minimum=0, maximum=86400),
        )
    except Exception as exc:
        job_manager._append_log("warning", f"前台推荐和日计划预计算排队失败：{exc}", job="frontend_payload_precompute", stage="queue")
        return {"status": "error", "message": str(exc)}


def _frontend_pending_payload(payload_type: str, effective_as_of: Optional[str], job_result: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    default_message = "缓存未命中，后台正在预计算，请稍后刷新。"
    status, cache_state, message = _deferred_job_response_state(job_result, default_message, cache_state="queued")
    payload: Dict[str, Any] = {
        "status": status,
        "as_of": effective_as_of,
        "frontend_payload_cache": cache_state,
        "frontend_payload_job": {
            "status": job_result.get("status"),
            "job": job_result.get("job"),
            "background": bool(job_result.get("background") or job_result.get("process")),
            "message": job_result.get("message"),
            "queued": bool(job_result.get("queued", status == "pending" and cache_state == "queued")),
            "frontend_payload_precompute_enabled": job_result.get("frontend_payload_precompute_enabled"),
            "frontend_payload_auto_precompute_on_miss": job_result.get("frontend_payload_auto_precompute_on_miss"),
        },
        "message": message,
        **extra,
    }
    if payload_type == "front_recommendations":
        payload.setdefault("items", [])
    if payload_type == "front_daily_plan":
        payload.setdefault("buy_list", [])
        payload.setdefault("sell_list", [])
        payload.setdefault("hold_list", [])
    return payload


def _frontend_pending_account(
    context: Dict[str, Any],
    effective_as_of: Optional[str],
    replay_start_date: Optional[str],
    limit: int,
    reason: str,
) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
    target_cash = safe_float(params.get("account_initial_cash"), safe_float(profile.get("simulated_cash"), 10_000))
    model_id = str(profile.get("strategy_model_id") or "active").strip() or "active"
    message = "账户运行结果缓存未命中，已跳过同步回放。请先在后台手动运行策略复盘，或导入本地策略运行结果小包。"
    return {
        "status": "pending",
        "as_of": effective_as_of,
        "start_date": replay_start_date or "",
        "follow_start_date": replay_start_date or "",
        "strategy_model_id": model_id,
        "strategy_account_source": "pending_runtime_missing",
        "strategy_account_cache": "miss_deferred",
        "frontend_account_deferred": True,
        "frontend_account_defer_reason": reason,
        "message": message,
        "account": {
            "status": "pending",
            "initial_cash": round(target_cash, 2),
            "simulated_cash": round(target_cash, 2),
            "total_asset": round(target_cash, 2),
            "cash": round(target_cash, 2),
            "available_cash": round(target_cash, 2),
            "market_value": 0.0,
            "total_pnl": 0.0,
            "return_pct": 0.0,
            "position_count": 0,
            "deal_count": 0,
        },
        "positions": [],
        "today_deals": [],
        "history_deals": [],
        "delivery_records": [],
        "daily_settlements": [],
        "portfolio": {"cash": round(target_cash, 2), "total_value": round(target_cash, 2), "strategy_params": params},
        "limit": limit,
    }


def _frontend_cached_recommendations_and_plan(
    context: Dict[str, Any],
    effective_as_of: Optional[str],
    top_n: int,
    limit_days: int,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    rec_ttl = _frontend_payload_cache_ttl("QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS", 1800)
    plan_ttl = _frontend_payload_cache_ttl("QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS", 1800)
    effective_start = _frontend_replay_start_date(effective_as_of)
    rec_parts = _frontend_payload_cache_parts(
        context,
        "front_recommendations",
        {"as_of": effective_as_of, "lookback_days": 2, "top_n": top_n},
    )
    plan_parts = _frontend_payload_cache_parts(
        context,
        "front_daily_plan",
        {"as_of": effective_as_of, "start_date": effective_start, "limit_days": limit_days},
    )
    recommendations = load_payload_cache("front_recommendations", rec_parts, rec_ttl)
    daily_plan = load_payload_cache("front_daily_plan", plan_parts, plan_ttl)
    if recommendations and daily_plan:
        return recommendations, daily_plan
    job_result = _queue_frontend_payload_precompute(context, effective_as_of, lookback_days=2, top_n=top_n, limit_days=limit_days)
    if not recommendations:
        recommendations = _frontend_pending_payload(
            "front_recommendations",
            effective_as_of,
            job_result,
            lookback_days=2,
            top_n=top_n,
        )
    if not daily_plan:
        daily_plan = _frontend_pending_payload(
            "front_daily_plan",
            effective_as_of,
            job_result,
            start_date=effective_start,
            limit_days=limit_days,
        )
    return recommendations, daily_plan


def _frontend_strategy_account(
    context: Dict[str, Any],
    as_of: Optional[str],
    limit: int,
    force: bool = False,
    record_period: bool = True,
    defer_miss: bool = True,
    persist_derived: bool = True,
    hydrate_runtime_trades: bool = True,
) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    followed_id = str(profile.get("strategy_model_id") or "active").strip() or "active"
    params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
    target_cash = safe_float(params.get("account_initial_cash"), safe_float(profile.get("simulated_cash"), 10_000))
    effective_as_of = _frontend_account_as_of(as_of)
    replay_start_date = _frontend_follow_start_date(context, effective_as_of)
    model_version = _frontend_followed_model_version(context)
    username = str(context.get("username") or "").strip() or "anonymous"
    if record_period:
        _record_user_follow_period(username, profile, source="front_account", reason="account_view", created_at=context.get("created_at"))

    def persist_user_follow(account: Dict[str, Any], source: str) -> Dict[str, Any]:
        if not persist_derived:
            marked = dict(account)
            marked["user_follow_persist_deferred"] = True
            marked["user_follow_persist_source"] = source
            marked["frontend_account_precompute_reason"] = "account_persist_deferred"
            return marked
        strategy_evolution.save_user_follow_account(
            username,
            followed_id,
            params,
            target_cash,
            replay_start_date,
            effective_as_of,
            limit,
            account,
            model_version=model_version,
            source=source,
        )
        return account

    user_cached = None if force else strategy_evolution.load_user_follow_account(
        username,
        followed_id,
        target_cash,
        replay_start_date,
        effective_as_of,
        limit,
        model_version=model_version,
        params=params,
        hydrate_trades=hydrate_runtime_trades,
    )
    if user_cached:
        return user_cached

    runtime_account = None if force else strategy_evolution.load_runtime_account(
        followed_id,
        target_cash,
        replay_start_date,
        effective_as_of,
        limit,
        model_version=model_version,
        params=params,
    )
    if runtime_account:
        if persist_derived:
            strategy_evolution.save_account_cache(
                followed_id,
                params,
                target_cash,
                replay_start_date,
                effective_as_of,
                limit,
                runtime_account,
                model_version=model_version,
                source="runtime_tables",
            )
        return persist_user_follow(runtime_account, "runtime_tables")

    sqlite_cached = None if force else strategy_evolution.load_account_cache(
        followed_id,
        params,
        target_cash,
        replay_start_date,
        effective_as_of,
        limit,
        model_version=model_version,
    )
    if sqlite_cached:
        return persist_user_follow(sqlite_cached, str(sqlite_cached.get("strategy_account_source") or "strategy_runtime_snapshot"))

    if followed_id != "active":
        allow_model_records = bool(force or _env_flag("QT_FRONT_ACCOUNT_MODEL_RECORDS_FALLBACK", False))
        if allow_model_records:
            model = _frontend_full_model(followed_id)
            raw_records = model.get("trade_records") if isinstance(model.get("trade_records"), list) else []
            if raw_records:
                trade_records = _scale_model_trades_for_cash(model, target_cash)
                account = quant_engine.account_from_trades(
                    trade_records,
                    initial_cash=target_cash,
                    as_of=effective_as_of,
                    start_date=replay_start_date,
                    limit=limit,
                    drop_unmatched_sells=True,
                )
                account["strategy_account_source"] = "model_records"
                account["follow_start_date"] = replay_start_date
                account["strategy_account_cache"] = "miss"
                if persist_derived:
                    strategy_evolution.save_account_cache(
                        followed_id,
                        params,
                        target_cash,
                        replay_start_date,
                        effective_as_of,
                        limit,
                        account,
                        model_version=model_version,
                        source="model_records",
                    )
                return persist_user_follow(account, "model_records")

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "model_id": followed_id,
                    "as_of": effective_as_of,
                    "start_date": replay_start_date,
                    "limit": limit,
                    "cash": round(target_cash, 2),
                    "params": params,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        cache_key = f"front-account:{fingerprint}"
        cached = None if force else _frontend_account_cache_get(cache_key)
        if cached:
            cached["strategy_account_cache"] = "hit"
            return cached
        if defer_miss:
            return _frontend_pending_account(
                context,
                effective_as_of,
                replay_start_date,
                limit,
                reason="strategy_runtime_cache_miss",
            )
        with quant_engine.temporary_strategy_params(params):
            timeline = quant_engine.walk_forward(
                start_date=replay_start_date,
                end_date=effective_as_of,
                initial_cash=target_cash,
                max_positions=int(params.get("max_positions", 5)),
                hold_days=int(params.get("max_hold_days", 3)),
                top_n=int(params.get("top_n", 5)),
                auto_fill=False,
            )
            trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
            account = quant_engine.account_from_trades(
                trades,
                initial_cash=target_cash,
                as_of=effective_as_of or timeline.get("end_date"),
                start_date=replay_start_date,
                limit=limit,
                drop_unmatched_sells=True,
            )
        account["strategy_account_source"] = "strategy_replay"
        account["strategy_account_cache"] = "miss"
        account["follow_start_date"] = replay_start_date
        account["strategy_timeline_summary"] = {
            "mode": timeline.get("mode", "daily"),
            "start_date": timeline.get("start_date"),
            "end_date": timeline.get("end_date"),
            "replay_days": _FRONTEND_ACCOUNT_REPLAY_DAYS,
            "trade_count": len(trades),
            "closed_trades": timeline.get("closed_trades", 0),
            "return_pct": timeline.get("return_pct", 0),
            "max_drawdown_pct": timeline.get("max_drawdown_pct", 0),
        }
        _frontend_account_cache_set(cache_key, account)
        if persist_derived:
            strategy_evolution.save_account_cache(
                followed_id,
                params,
                target_cash,
                replay_start_date,
                effective_as_of,
                limit,
                account,
                model_version=model_version,
                source="strategy_replay",
            )
        return persist_user_follow(account, "strategy_replay")

    if defer_miss:
        return _frontend_pending_account(
            context,
            effective_as_of,
            replay_start_date,
            limit,
            reason="baseline_runtime_cache_miss",
        )
    with quant_engine.temporary_strategy_params(params):
        account = quant_engine.trading_account(as_of=effective_as_of, limit=limit)
    account["strategy_account_source"] = "baseline_replay"
    account["follow_start_date"] = replay_start_date
    account["strategy_account_cache"] = "miss"
    if persist_derived:
        strategy_evolution.save_account_cache(
            followed_id,
            params,
            target_cash,
            replay_start_date,
            effective_as_of,
            limit,
            account,
            model_version=model_version,
            source="baseline_replay",
        )
    return persist_user_follow(account, "baseline_replay")


def _split_usernames(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    return [str(value or "").strip()] if str(value or "").strip() else []


def _frontend_account_precompute_queue_file() -> Path:
    return DATA_DIR / "frontend_account_precompute_queue.json"


def _frontend_account_precompute_queue_lock_file() -> Path:
    return DATA_DIR / "frontend_account_precompute_queue.lock"


@contextmanager
def _frontend_account_precompute_queue_file_lock():
    path = _frontend_account_precompute_queue_lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    timeout_ms = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_TIMEOUT_MS", 5000, minimum=100, maximum=60000)
    stale_ms = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS", 30000, minimum=1000, maximum=600000)
    deadline = time.time() + timeout_ms / 1000
    fd: Optional[int] = None
    while True:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            payload = {
                "pid": os.getpid(),
                "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            }
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale_ms / 1000:
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() >= deadline:
                raise TimeoutError(f"frontend account precompute queue lock timeout: {path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _load_frontend_account_precompute_queue() -> list[Dict[str, Any]]:
    path = _frontend_account_precompute_queue_file()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    clean: list[Dict[str, Any]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        if not username or username in seen:
            continue
        seen.add(username)
        clean.append(
            {
                "username": username,
                "reason": str(item.get("reason") or ""),
                "as_of": str(item.get("as_of") or ""),
                "queued_at": str(item.get("queued_at") or ""),
            }
        )
    return clean


def _save_frontend_account_precompute_queue(items: list[Dict[str, Any]]) -> None:
    path = _frontend_account_precompute_queue_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "items": items,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _enqueue_frontend_account_precompute(username: str, reason: str, as_of: Optional[str] = None) -> Dict[str, Any]:
    username = str(username or "").strip()
    if not username:
        return {"status": "skipped", "queued": False, "reason": "missing_username"}
    with _FRONTEND_ACCOUNT_PRECOMPUTE_QUEUE_LOCK:
        with _frontend_account_precompute_queue_file_lock():
            items = _load_frontend_account_precompute_queue()
            items = [item for item in items if str(item.get("username") or "") != username]
            items.append(
                {
                    "username": username,
                    "reason": str(reason or ""),
                    "as_of": str(as_of or ""),
                    "queued_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                }
            )
            max_items = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_USERS", 500, minimum=1, maximum=5000)
            if len(items) > max_items:
                items = items[-max_items:]
            _save_frontend_account_precompute_queue(items)
            return {"status": "queued", "queued": True, "queue_size": len(items), "username": username}


def _dequeue_frontend_account_precompute(limit_users: int) -> list[Dict[str, Any]]:
    clean_limit = max(1, min(int(limit_users or 50), 500))
    with _FRONTEND_ACCOUNT_PRECOMPUTE_QUEUE_LOCK:
        with _frontend_account_precompute_queue_file_lock():
            items = _load_frontend_account_precompute_queue()
            batch = items[:clean_limit]
            remaining = items[clean_limit:]
            _save_frontend_account_precompute_queue(remaining)
            return batch


def _frontend_account_precompute_queue_size() -> int:
    with _FRONTEND_ACCOUNT_PRECOMPUTE_QUEUE_LOCK:
        with _frontend_account_precompute_queue_file_lock():
            return len(_load_frontend_account_precompute_queue())


def _frontend_account_precompute_queue_status() -> Dict[str, Any]:
    path = _frontend_account_precompute_queue_file()
    lock_path = _frontend_account_precompute_queue_lock_file()
    now = time.time()
    stale_ms = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_LOCK_STALE_MS", 30000, minimum=1000, maximum=600000)
    payload: Dict[str, Any] = {
        "status": "ok",
        "queued": 0,
        "empty": True,
        "updated_at": "",
        "oldest_queued_at": "",
        "newest_queued_at": "",
        "reason_counts": {},
        "queue_file_exists": path.exists(),
        "lock": {
            "exists": lock_path.exists(),
            "age_ms": 0,
            "stale": False,
            "stale_after_ms": stale_ms,
        },
    }
    try:
        if path.exists():
            payload["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("updated_at"):
                    payload["updated_at"] = str(raw.get("updated_at") or "")
            except Exception:
                pass
        items = _load_frontend_account_precompute_queue()
        queued_at_values = [str(item.get("queued_at") or "") for item in items if str(item.get("queued_at") or "")]
        reason_counts: Dict[str, int] = {}
        for item in items:
            reason = str(item.get("reason") or "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        payload.update(
            {
                "queued": len(items),
                "empty": len(items) <= 0,
                "oldest_queued_at": min(queued_at_values) if queued_at_values else "",
                "newest_queued_at": max(queued_at_values) if queued_at_values else "",
                "reason_counts": reason_counts,
            }
        )
    except Exception as exc:
        payload.update({"status": "error", "message": str(exc)})

    if lock_path.exists():
        try:
            age_ms = max(0, int((now - lock_path.stat().st_mtime) * 1000))
            payload["lock"] = {
                "exists": True,
                "age_ms": age_ms,
                "stale": age_ms > stale_ms,
                "stale_after_ms": stale_ms,
            }
        except Exception as exc:
            payload["lock"] = {"exists": True, "age_ms": 0, "stale": False, "stale_after_ms": stale_ms, "error": str(exc)}
    return payload


def _frontend_account_precompute_async_status() -> Dict[str, Any]:
    now = time.time()
    debounce_seconds = max(0.0, min(_env_float("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DEBOUNCE_SECONDS", 5.0), 300.0))
    stale_after = max(debounce_seconds, 60.0)
    reason_counts: Dict[str, int] = {}
    mode_counts: Dict[str, int] = {}
    ages_ms: list[int] = []
    with _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_LOCK:
        for key, ts in list(_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING.items()):
            if now - ts > stale_after:
                _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING.pop(key, None)
                continue
            parts = str(key).split("|")
            reason = parts[1] if len(parts) > 1 and parts[1] else "unknown"
            mode = parts[3] if len(parts) > 3 and parts[3] else "queue_only"
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            ages_ms.append(max(0, int((now - ts) * 1000)))
        pending_count = len(_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING)
    return {
        "status": "ok",
        "pending_count": pending_count,
        "empty": pending_count <= 0,
        "debounce_seconds": round(debounce_seconds, 3),
        "stale_after_seconds": round(stale_after, 3),
        "oldest_age_ms": max(ages_ms) if ages_ms else 0,
        "newest_age_ms": min(ages_ms) if ages_ms else 0,
        "queued_tasks": _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_TASKS.qsize(),
        "worker_started": _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED > 0,
        "worker_count": _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED,
        "worker_target": cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS", 4, minimum=1, maximum=16),
        "reason_counts": reason_counts,
        "mode_counts": mode_counts,
    }


def _ensure_frontend_account_precompute_async_worker() -> None:
    global _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED
    worker_target = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS", 4, minimum=1, maximum=16)
    if _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED >= worker_target:
        return
    with _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKER_LOCK:
        if _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED >= worker_target:
            return

        def run() -> None:
            while True:
                task = _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_TASKS.get()
                try:
                    delay_ms = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DISPATCH_DELAY_MS", 25, minimum=0, maximum=1000)
                    if delay_ms > 0:
                        time.sleep(delay_ms / 1000)
                    task()
                except Exception as exc:
                    try:
                        job_manager._append_log(
                            "warning",
                            f"用户账户预热异步任务失败：{exc}",
                            job="frontend_account_precompute",
                            stage="queue_async",
                        )
                    except Exception:
                        pass
                finally:
                    _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_TASKS.task_done()

        while _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED < worker_target:
            next_index = _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED + 1
            threading.Thread(target=run, name=f"qt-account-precompute-async-{next_index}", daemon=True).start()
            _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_WORKERS_STARTED = next_index


def _submit_frontend_account_precompute_async(task: Callable[[], None]) -> None:
    _ensure_frontend_account_precompute_async_worker()
    _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_TASKS.put(task)


def _start_frontend_account_precompute_worker_for_queue(as_of: Optional[str] = None, reason: str = "") -> Dict[str, Any]:
    try:
        queue_size = _frontend_account_precompute_queue_size()
    except Exception as exc:
        job_manager._append_log("warning", f"账户预热队列状态读取失败：{exc}", job="frontend_account_precompute", stage="queue")
        return {"status": "error", "queued": False, "worker_started": False, "reason": reason, "message": str(exc)}
    if queue_size <= 0:
        return {"status": "skipped", "queued": False, "worker_started": False, "reason": reason or "queue_empty", "queue_size": 0}

    payload = {
        "as_of": as_of,
        "usernames": None,
        "limit_users": cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_BATCH_USERS", 50, minimum=1, maximum=500),
        "limit": cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_LIMIT", 160, minimum=1, maximum=2000),
        "force": False,
        "drain_queue": True,
    }

    def execute() -> Dict[str, Any]:
        return _precompute_frontend_accounts(**payload)

    try:
        if _env_flag("QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED", True):
            result = job_manager.run_job_process(
                "frontend_account_precompute",
                payload=payload,
                message="前台账户快照队列预热已转入独立进程运行",
            )
        else:
            result = job_manager.run_job_background(
                "frontend_account_precompute",
                execute,
                payload=payload,
                message="前台账户快照队列预热已转入后台运行",
            )
    except Exception as exc:
        job_manager._append_log("warning", f"账户预热队列 worker 启动失败：{exc}", job="frontend_account_precompute", stage="queue")
        return {"status": "queued", "queued": True, "worker_started": False, "reason": reason, "queue_size": queue_size, "message": str(exc)}

    if isinstance(result, dict):
        worker_started = bool(result.get("process_pid") or result.get("progress_pct") is not None)
        return {**result, "queued": True, "worker_started": worker_started, "reason": reason, "queue_size": queue_size}
    return {"status": "ok", "queued": True, "worker_started": True, "reason": reason, "queue_size": queue_size}


def _merge_frontend_account_precompute_result(target: Dict[str, Any], result: Dict[str, Any]) -> None:
    target["user_count"] += int(safe_float(result.get("user_count"), 0))
    target["saved"] += int(safe_float(result.get("saved"), 0))
    target["cached"] += int(safe_float(result.get("cached"), 0))
    target["pending"] += int(safe_float(result.get("pending"), 0))
    target["error_count"] += int(safe_float(result.get("error_count"), 0))
    target["items"].extend(result.get("items") if isinstance(result.get("items"), list) else [])
    target["errors"].extend(result.get("errors") if isinstance(result.get("errors"), list) else [])


def _precompute_frontend_accounts_once(
    as_of: Optional[str] = None,
    usernames: Optional[Any] = None,
    limit_users: int = 50,
    limit: int = 160,
    force: bool = False,
) -> Dict[str, Any]:
    effective_as_of = _frontend_account_as_of(as_of)
    requested = set(_split_usernames(usernames))
    clean_limit_users = max(1, min(int(limit_users or 50), 500))
    clean_limit = max(1, min(int(limit or 160), 2000))
    users_payload = frontend_user_summary()
    candidates = []
    for item in users_payload.get("items", []) if isinstance(users_payload.get("items"), list) else []:
        if not isinstance(item, dict) or item.get("disabled"):
            continue
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        if requested and username not in requested:
            continue
        candidates.append(username)
        if len(candidates) >= clean_limit_users:
            break

    results = []
    saved = 0
    cached = 0
    pending = 0
    errors = []
    for username in candidates:
        row: Dict[str, Any] = {"username": username}
        try:
            context = _frontend_profile_context_for_username(username, include_catalog=False)
            account = _frontend_strategy_account(
                context,
                effective_as_of,
                limit=clean_limit,
                force=force,
                record_period=True,
                defer_miss=True,
            )
            source = str(account.get("strategy_account_source") or "")
            cache_state = str(account.get("strategy_account_cache") or "")
            row.update(
                {
                    "status": "pending" if account.get("frontend_account_deferred") else "ok",
                    "strategy_model_id": str((context.get("profile") or {}).get("strategy_model_id") or ""),
                    "follow_start_date": str(account.get("follow_start_date") or ""),
                    "source": source,
                    "cache": cache_state,
                    "message": account.get("message") or "",
                }
            )
            if account.get("frontend_account_deferred"):
                pending += 1
            elif cache_state == "user_follow":
                cached += 1
            else:
                saved += 1
        except Exception as exc:
            row.update({"status": "error", "error": str(exc)})
            errors.append({"username": username, "error": str(exc)})
        results.append(row)

    status = "ok" if not errors else ("partial" if saved or cached or pending else "error")
    return {
        "status": status,
        "job": "frontend_account_precompute",
        "as_of": effective_as_of,
        "user_count": len(candidates),
        "saved": saved,
        "cached": cached,
        "pending": pending,
        "error_count": len(errors),
        "errors": errors[:20],
        "items": results,
        "force": bool(force),
        "limit": clean_limit,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _precompute_frontend_accounts(
    as_of: Optional[str] = None,
    usernames: Optional[Any] = None,
    limit_users: int = 50,
    limit: int = 160,
    force: bool = False,
    drain_queue: bool = False,
) -> Dict[str, Any]:
    if not drain_queue:
        return _precompute_frontend_accounts_once(as_of=as_of, usernames=usernames, limit_users=limit_users, limit=limit, force=force)

    clean_limit_users = max(1, min(int(limit_users or 50), 500))
    summary: Dict[str, Any] = {
        "status": "ok",
        "job": "frontend_account_precompute",
        "as_of": _frontend_account_as_of(as_of),
        "drain_queue": True,
        "batches": 0,
        "user_count": 0,
        "saved": 0,
        "cached": 0,
        "pending": 0,
        "error_count": 0,
        "errors": [],
        "items": [],
        "force": bool(force),
        "limit": max(1, min(int(limit or 160), 2000)),
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }
    max_batches = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_MAX_BATCHES", 20, minimum=1, maximum=200)
    idle_grace_ms = cache_env_int("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_IDLE_GRACE_MS", 500, minimum=0, maximum=5000)
    idle_checked = False
    for _index in range(max_batches):
        batch = _dequeue_frontend_account_precompute(clean_limit_users)
        if not batch:
            if idle_grace_ms > 0 and not idle_checked:
                idle_checked = True
                time.sleep(idle_grace_ms / 1000)
                continue
            break
        idle_checked = False
        usernames_batch = [str(item.get("username") or "").strip() for item in batch if str(item.get("username") or "").strip()]
        if not usernames_batch:
            continue
        batch_as_of = as_of or next((str(item.get("as_of") or "").strip() for item in batch if str(item.get("as_of") or "").strip()), None)
        result = _precompute_frontend_accounts_once(
            as_of=batch_as_of,
            usernames=usernames_batch,
            limit_users=len(usernames_batch),
            limit=limit,
            force=force,
        )
        summary["batches"] += 1
        _merge_frontend_account_precompute_result(summary, result)

    if summary["error_count"]:
        summary["status"] = "partial" if summary["saved"] or summary["cached"] or summary["pending"] else "error"
    summary["errors"] = summary["errors"][:20]
    return summary


def _queue_frontend_account_precompute_for_user(
    username: str,
    reason: str = "",
    as_of: Optional[str] = None,
    start_worker: Optional[bool] = None,
    async_enqueue: bool = False,
) -> Dict[str, Any]:
    username = str(username or "").strip()
    reason = str(reason or "").strip()
    if not username:
        return {"status": "skipped", "reason": "missing_username"}
    if reason not in {"profile_strategy_changed", "profile_cash_changed", "profile_cash_and_strategy_changed", "register", "account_runtime_missing"}:
        return {"status": "skipped", "reason": reason or "profile_unchanged"}
    if not _env_flag("QT_FRONT_ACCOUNT_AUTO_PRECOMPUTE_ENABLED", True):
        return {"status": "disabled", "reason": reason}

    should_start_worker = (
        _env_flag("QT_FRONT_ACCOUNT_START_WORKER_ON_PROFILE", False)
        if start_worker is None
        else bool(start_worker)
    )
    if async_enqueue and _env_flag("QT_FRONT_ACCOUNT_PRECOMPUTE_QUEUE_ASYNC_ON_PROFILE", True):
        clean_username = username
        reason_text = reason
        as_of_text = as_of
        start_worker_value = should_start_worker
        async_key = "|".join([clean_username, reason_text, str(as_of_text or ""), "start_worker" if start_worker_value else "queue_only"])
        debounce_seconds = max(0.0, min(_env_float("QT_FRONT_ACCOUNT_PRECOMPUTE_ASYNC_DEBOUNCE_SECONDS", 5.0), 300.0))
        now_ts = time.time()
        with _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_LOCK:
            stale_after = max(debounce_seconds, 60.0)
            for key, ts in list(_FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING.items()):
                if now_ts - ts > stale_after:
                    _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING.pop(key, None)
            previous_ts = _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING.get(async_key)
            if debounce_seconds > 0 and previous_ts and now_ts - previous_ts < debounce_seconds:
                return {
                    "status": "queued_async",
                    "queued": True,
                    "async": True,
                    "deduped": True,
                    "queue_pending": True,
                    "reason": reason,
                    "username": username,
                    "worker_started": False,
                    "worker_start_deferred": not should_start_worker,
                    "worker_start_pending": bool(should_start_worker),
                    "debounce_seconds": round(debounce_seconds, 3),
                }
            _FRONTEND_ACCOUNT_PRECOMPUTE_ASYNC_PENDING[async_key] = now_ts

        def worker() -> None:
            result = _queue_frontend_account_precompute_for_user(
                clean_username,
                reason=reason_text,
                as_of=as_of_text,
                start_worker=start_worker_value,
                async_enqueue=False,
            )
            if str(result.get("status") or "") == "error":
                job_manager._append_log(
                    "warning",
                    f"用户账户预热异步入队失败：{result.get('message') or 'unknown error'}",
                    job="frontend_account_precompute",
                    stage="queue_async",
                    payload={"username": clean_username, "reason": reason_text},
                )

        _submit_frontend_account_precompute_async(worker)
        return {
            "status": "queued_async",
            "queued": True,
            "async": True,
            "deduped": False,
            "queue_pending": True,
            "reason": reason,
            "username": username,
            "worker_started": False,
            "worker_start_deferred": not should_start_worker,
            "worker_start_pending": bool(should_start_worker),
            "debounce_seconds": round(debounce_seconds, 3),
        }

    try:
        queue_result = _enqueue_frontend_account_precompute(username, reason=reason, as_of=as_of)
    except Exception as exc:
        job_manager._append_log("warning", f"用户账户预热入队失败：{exc}", job="frontend_account_precompute", stage="queue")
        return {"status": "error", "queued": False, "reason": reason, "username": username, "message": str(exc)}
    if not queue_result.get("queued"):
        return {**queue_result, "reason": reason, "username": username}

    if not should_start_worker:
        return {
            **queue_result,
            "reason": reason,
            "username": username,
            "queued": True,
            "worker_started": False,
            "worker_start_deferred": True,
        }

    worker_result = _start_frontend_account_precompute_worker_for_queue(as_of=as_of, reason=reason)
    return {**worker_result, **queue_result, "reason": reason, "username": username, "queued": True}


def _frontend_account_needs_precompute(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    return bool(
        payload.get("frontend_account_deferred")
        or payload.get("user_follow_persist_deferred")
        or str(payload.get("status") or "") == "pending"
        or str(account.get("status") or "") == "pending"
    )


def _attach_frontend_account_precompute(
    payload: Dict[str, Any],
    context: Dict[str, Any],
    as_of: Optional[str],
    reason: str = "account_runtime_missing",
) -> Dict[str, Any]:
    if not _env_flag("QT_FRONT_ACCOUNT_AUTO_PRECOMPUTE_ENABLED", True):
        return payload
    if not _frontend_account_needs_precompute(payload):
        return payload
    effective_reason = str(payload.get("frontend_account_precompute_reason") or reason or "account_runtime_missing")
    rescue = _queue_frontend_account_precompute_for_user(
        str(context.get("username") or ""),
        reason=effective_reason,
        as_of=as_of,
        start_worker=True,
        async_enqueue=True,
    )
    if rescue.get("queued") or rescue.get("status") == "error":
        payload["account_precompute"] = rescue
        payload["account_precompute_queued"] = bool(rescue.get("queued"))
    return payload


def _scale_row(row: Dict[str, Any], scale: float, keys: tuple[str, ...]) -> Dict[str, Any]:
    item = dict(row)
    for key in keys:
        if key in item:
            item[key] = round(safe_float(item.get(key), 0) * scale, 2)
    return item


def _frontend_trading_account(account_payload: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    target_cash = safe_float(profile.get("simulated_cash"), 0)
    account = account_payload.get("account") if isinstance(account_payload.get("account"), dict) else {}
    base_initial = safe_float(account.get("total_asset"), 0) - safe_float(account.get("total_pnl"), 0)
    if base_initial <= 0:
        base_initial = safe_float(((account_payload.get("portfolio") or {}).get("strategy_params") or {}).get("account_initial_cash"), target_cash)
    scale = target_cash / base_initial if base_initial > 0 and target_cash > 0 else 1.0
    money_keys = (
        "total_asset",
        "cash",
        "available_cash",
        "frozen_cash",
        "state_cash_gross",
        "market_value",
        "position_cost",
        "unrealized_pnl",
        "realized_pnl",
        "total_pnl",
        "total_fees",
    )
    position_money_keys = ("qty", "available_qty", "frozen_qty", "market_value", "cost_amount", "pnl_amount")
    deal_money_keys = (
        "qty",
        "amount",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "total_fee",
        "net_amount",
        "cost_amount",
        "realized_pnl",
    )
    settlement_money_keys = (
        "buy_amount",
        "sell_amount",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "total_fee",
        "net_amount",
        "realized_pnl",
    )
    next_payload = dict(account_payload)
    next_account = _scale_row(account, scale, money_keys)
    next_account["initial_cash"] = round(target_cash, 2)
    next_account["simulated_cash"] = round(target_cash, 2)
    next_account["total_pnl"] = round(safe_float(next_account.get("total_asset"), target_cash) - target_cash, 2)
    next_account["return_pct"] = round(safe_float(next_account.get("total_pnl"), 0) / target_cash * 100, 3) if target_cash > 0 else 0.0
    next_account["follow_model_id"] = str(profile.get("strategy_model_id") or "active")
    next_account["follow_model_name"] = str((context.get("followed_model") or {}).get("name") or "未选择策略")
    next_payload["account"] = next_account
    next_payload["positions"] = [_scale_row(item, scale, position_money_keys) for item in account_payload.get("positions", []) if isinstance(item, dict)]
    next_payload["today_deals"] = [_scale_row(item, scale, deal_money_keys) for item in account_payload.get("today_deals", []) if isinstance(item, dict)]
    next_payload["history_deals"] = [_scale_row(item, scale, deal_money_keys) for item in account_payload.get("history_deals", []) if isinstance(item, dict)]
    next_payload["delivery_records"] = [_scale_row(item, scale, deal_money_keys) for item in account_payload.get("delivery_records", []) if isinstance(item, dict)]
    next_payload["daily_settlements"] = [_scale_row(item, scale, settlement_money_keys) for item in account_payload.get("daily_settlements", []) if isinstance(item, dict)]
    portfolio = account_payload.get("portfolio") if isinstance(account_payload.get("portfolio"), dict) else {}
    next_portfolio = _scale_row(portfolio, scale, ("cash", "total_value"))
    next_portfolio["strategy_params"] = context.get("strategy_params") or portfolio.get("strategy_params") or {}
    next_payload["portfolio"] = next_portfolio
    next_payload["frontend_profile"] = profile
    next_payload["followed_model"] = context.get("followed_model") or {}
    next_payload["follow_start_date"] = account_payload.get("follow_start_date") or profile.get("follow_start_date") or ""
    return next_payload


def _find_strategy_model(model_id: str, include_records: bool = True) -> Dict[str, Any]:
    model_id = str(model_id or "active").strip() or "active"
    model = strategy_evolution.model(model_id, include_records=include_records)
    if model:
        return model
    models_payload = _frontend_strategy_models_payload(include_catalog=True)
    catalog_model = next(
        (item for item in _strategy_catalog_items(models_payload) if str(item.get("id") or "") == model_id),
        None,
    )
    if catalog_model:
        return catalog_model
    raise HTTPException(status_code=404, detail="strategy model not found")


def _model_backtest_payload(
    model: Dict[str, Any],
    start_date: Optional[str],
    end_date: Optional[str],
    mode: str,
    limit: int,
) -> Dict[str, Any]:
    params = quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
    start_date = str(start_date or quant_engine.first_data_date() or "").strip() or None
    end_date = str(end_date or quant_engine.latest_event_date() or "").strip() or None
    mode = str(mode or "intraday").strip().lower()
    with quant_engine.temporary_strategy_params(params):
        if mode in {"intraday", "intraday_5m", "minute"}:
            timeline = quant_engine.walk_forward_intraday(
                start_date=start_date,
                end_date=end_date,
                initial_cash=params.get("account_initial_cash"),
                max_positions=int(params.get("max_positions", 5)),
                hold_days=int(params.get("max_hold_days", 3)),
                top_n=int(params.get("top_n", 5)),
                auto_fill=False,
            )
        else:
            timeline = quant_engine.walk_forward(
                start_date=start_date,
                end_date=end_date,
                initial_cash=params.get("account_initial_cash"),
                max_positions=int(params.get("max_positions", 5)),
                hold_days=int(params.get("max_hold_days", 3)),
                top_n=int(params.get("top_n", 5)),
                auto_fill=False,
            )
        trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
        account = quant_engine.account_from_trades(
            trades,
            initial_cash=timeline.get("initial_cash", params.get("account_initial_cash")),
            as_of=end_date or timeline.get("end_date"),
            limit=limit,
        )
    return {
        "status": "ok",
        "model": model,
        "model_id": model.get("id"),
        "model_name": model.get("name"),
        "mode": timeline.get("mode", mode),
        "start_date": timeline.get("start_date") or start_date,
        "end_date": timeline.get("end_date") or end_date,
        "summary": {
            "initial_cash": timeline.get("initial_cash"),
            "final_value": timeline.get("final_value"),
            "return_pct": timeline.get("return_pct", 0),
            "max_drawdown_pct": timeline.get("max_drawdown_pct", 0),
            "annualized_return_pct": timeline.get("annualized_return_pct", 0),
            "sharpe_ratio": timeline.get("sharpe_ratio", 0),
            "profit_factor": timeline.get("profit_factor", 0),
            "win_rate": timeline.get("win_rate", 0),
            "closed_trades": timeline.get("closed_trades", 0),
            "trade_count": len(trades),
            "total_fees": timeline.get("total_fees", 0),
        },
        "account": account.get("account", {}),
        "positions": account.get("positions", []),
        "trade_records": trades if limit <= 0 else trades[-limit:],
        "delivery_records": account.get("delivery_records", []),
        "daily_settlements": account.get("daily_settlements", []),
        "equity_curve": timeline.get("equity_curve", []),
        "days": timeline.get("days", []),
        "strategy_params": params,
    }


def _model_backtest_cache_ttl() -> int:
    return cache_env_int("QT_MODEL_BACKTEST_CACHE_TTL_SECONDS", 600, minimum=0, maximum=86400)


def _model_backtest_cache_parts(
    model: Dict[str, Any],
    start_date: Optional[str],
    end_date: Optional[str],
    mode: str,
    limit: int,
) -> Dict[str, Any]:
    params = model.get("params") if isinstance(model.get("params"), dict) else {}
    params_hash = hashlib.sha256(
        json.dumps(params, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="ignore")
    ).hexdigest()
    return {
        "model_id": str(model.get("id") or ""),
        "model_version": strategy_evolution.runtime_model_version(model),
        "params_hash": params_hash,
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "mode": str(mode or "intraday").strip().lower(),
        "limit": max(0, min(int(limit or 0), 5000)),
        "version": APP_VERSION,
    }


def _compact_model_backtest_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "status": payload.get("status") or "ok",
        "job": "model_backtest",
        "model_id": payload.get("model_id") or "",
        "model_name": payload.get("model_name") or "",
        "mode": payload.get("mode") or "",
        "start_date": payload.get("start_date") or "",
        "end_date": payload.get("end_date") or "",
        "return_pct": summary.get("return_pct", 0),
        "trade_count": summary.get("trade_count", 0),
        "closed_trades": summary.get("closed_trades", 0),
        "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _compute_model_backtest_cached(
    model: Dict[str, Any],
    start_date: Optional[str],
    end_date: Optional[str],
    mode: str,
    limit: int,
) -> Dict[str, Any]:
    clean_limit = max(0, min(int(limit or 0), 5000))
    payload = _model_backtest_payload(
        model=model,
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        limit=clean_limit,
    )
    if isinstance(payload, dict):
        payload["source"] = "model_backtest_recompute"
        payload["model_backtest_cache"] = "refresh"
        payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        save_payload_cache(
            "model_backtest",
            _model_backtest_cache_parts(model, start_date, end_date, mode, clean_limit),
            payload,
            _model_backtest_cache_ttl(),
        )
        return payload
    return payload


def _queue_model_backtest_recompute(
    model: Dict[str, Any],
    start_date: Optional[str],
    end_date: Optional[str],
    mode: str,
    limit: int,
    process: bool = True,
) -> Dict[str, Any]:
    clean_limit = max(0, min(int(limit or 0), 5000))
    payload = {
        "model_id": str(model.get("id") or ""),
        "model_name": str(model.get("name") or model.get("id") or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "mode": str(mode or "intraday").strip().lower(),
        "limit": clean_limit,
    }
    if process:
        return job_manager.run_job_process(
            "model_backtest",
            payload=payload,
            message="模型回测重算已转入独立进程运行",
        )

    def execute() -> Dict[str, Any]:
        result = _compute_model_backtest_cached(model, start_date, end_date, mode, clean_limit)
        return _compact_model_backtest_result(result if isinstance(result, dict) else {})

    return job_manager.run_job_background(
        "model_backtest",
        execute,
        payload=payload,
        message="模型回测重算已转入后台运行",
    )


def _pending_model_backtest(
    model: Dict[str, Any],
    start_date: Optional[str],
    end_date: Optional[str],
    mode: str,
    limit: int,
    job_result: Dict[str, Any],
) -> Dict[str, Any]:
    status, cache_state, message = _deferred_job_response_state(
        job_result,
        "模型回测重算正在后台生成，请稍后刷新。",
    )
    return {
        "status": status,
        "source": "model_backtest_recompute",
        "model_backtest_cache": cache_state,
        "message": message,
        "model": model,
        "model_id": model.get("id"),
        "model_name": model.get("name"),
        "mode": str(mode or "intraday").strip().lower(),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "summary": {},
        "account": {},
        "positions": [],
        "trade_records": [],
        "delivery_records": [],
        "daily_settlements": [],
        "equity_curve": [],
        "days": [],
        "strategy_params": quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {}),
        "limit": max(0, min(int(limit or 0), 5000)),
        "job_result": job_result,
    }


def _manual_required_heavy_job(job: str, message: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "manual_required",
        "job": job,
        "manual_required": True,
        "message": message,
        **payload,
    }


def _model_backtest_recompute_payload(
    model: Dict[str, Any],
    start_date: Optional[str],
    end_date: Optional[str],
    mode: str,
    limit: int,
    force: bool = False,
    defer: bool = True,
    manual: bool = False,
    process: bool = True,
) -> Dict[str, Any]:
    clean_limit = max(0, min(int(limit or 0), 5000))
    cache_parts = _model_backtest_cache_parts(model, start_date, end_date, mode, clean_limit)
    cached = None if force else load_payload_cache("model_backtest", cache_parts, _model_backtest_cache_ttl())
    if cached:
        cached["model_backtest_cache"] = "hit"
        return cached
    if _env_flag("QT_MODEL_BACKTEST_REQUIRE_MANUAL_TRIGGER", True) and not manual:
        return _manual_required_heavy_job(
            "model_backtest",
            "模型回测重算需要显式手动触发；普通刷新只读取已保存记录或短缓存。",
            {
                "source": "model_backtest_recompute",
                "model_backtest_cache": "manual_required",
                "model": model,
                "model_id": model.get("id"),
                "model_name": model.get("name"),
                "mode": str(mode or "intraday").strip().lower(),
                "start_date": str(start_date or ""),
                "end_date": str(end_date or ""),
                "summary": {},
                "account": {},
                "positions": [],
                "trade_records": [],
                "delivery_records": [],
                "daily_settlements": [],
                "equity_curve": [],
                "days": [],
                "strategy_params": quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {}),
                "limit": clean_limit,
            },
        )
    if defer:
        job_result = _queue_model_backtest_recompute(model, start_date, end_date, mode, clean_limit, process=process)
        return _pending_model_backtest(model, start_date, end_date, mode, clean_limit, job_result)
    return _compute_model_backtest_cached(model, start_date, end_date, mode, clean_limit)


def _stored_model_backtest_payload(model: Dict[str, Any], limit: int = 0) -> Dict[str, Any]:
    backtest = model.get("backtest") if isinstance(model.get("backtest"), dict) else {}
    trades = model.get("trade_records") if isinstance(model.get("trade_records"), list) else []
    deliveries = model.get("delivery_records") if isinstance(model.get("delivery_records"), list) else []
    settlements = model.get("daily_settlements") if isinstance(model.get("daily_settlements"), list) else []
    params = quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
    if not backtest and not trades and not deliveries and not settlements:
        return {
            "status": "missing",
            "source": "strategy_model_records",
            "model": model,
            "model_id": model.get("id"),
            "model_name": model.get("name"),
            "message": "该模型还没有保存的回测交割单；需要时请手动重新回测或运行策略复盘。",
            "summary": {},
            "trade_records": [],
            "delivery_records": [],
            "daily_settlements": [],
            "equity_curve": [],
            "days": [],
            "strategy_params": params,
        }
    initial_cash = backtest.get("initial_cash") or params.get("account_initial_cash")
    as_of = backtest.get("end_date") or model.get("generated_at")
    account = quant_engine.account_from_trades(trades, initial_cash=initial_cash, as_of=as_of, limit=limit) if trades else {}
    return {
        "status": "ok",
        "source": "strategy_model_records",
        "model": model,
        "model_id": model.get("id"),
        "model_name": model.get("name"),
        "mode": backtest.get("mode") or "",
        "start_date": backtest.get("start_date") or "",
        "end_date": backtest.get("end_date") or "",
        "summary": {
            "initial_cash": initial_cash,
            "final_value": backtest.get("final_value"),
            "return_pct": backtest.get("return_pct", model.get("return_pct", 0)),
            "max_drawdown_pct": backtest.get("max_drawdown_pct", model.get("max_drawdown_pct", 0)),
            "annualized_return_pct": backtest.get("annualized_return_pct", 0),
            "sharpe_ratio": backtest.get("sharpe_ratio", 0),
            "profit_factor": backtest.get("profit_factor", 0),
            "win_rate": backtest.get("win_rate", model.get("win_rate", 0)),
            "closed_trades": backtest.get("closed_trades", model.get("closed_trades", 0)),
            "trade_count": backtest.get("trade_count", len(trades)),
            "total_fees": backtest.get("total_fees", 0),
        },
        "account": account.get("account", {}),
        "positions": account.get("positions", []),
        "trade_records": trades if limit <= 0 else trades[-limit:],
        "delivery_records": deliveries or account.get("delivery_records", []),
        "daily_settlements": settlements or account.get("daily_settlements", []),
        "equity_curve": model.get("equity_curve") if isinstance(model.get("equity_curve"), list) else [],
        "days": model.get("days") if isinstance(model.get("days"), list) else [],
        "strategy_params": params,
    }


def _quant_timeline_payload(
    *,
    model_id: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: Optional[int],
    top_n: Optional[int],
    intraday: bool,
    use_daily_fallback: bool = True,
    auto_fill: bool = True,
) -> Dict[str, Any]:
    clean_model_id = str(model_id or "").strip()
    if not clean_model_id:
        if intraday:
            return quant_engine.walk_forward_intraday(
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=hold_days,
                top_n=top_n,
                use_daily_fallback=use_daily_fallback,
                auto_fill=auto_fill,
            )
        return quant_engine.walk_forward(
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            auto_fill=auto_fill,
        )

    model = _find_strategy_model(clean_model_id, include_records=False)
    params = quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
    effective_initial_cash = initial_cash if initial_cash is not None else safe_float(params.get("account_initial_cash"), 100000)
    effective_max_positions = max_positions if max_positions is not None else int(safe_float(params.get("max_positions"), 5))
    effective_hold_days = hold_days if hold_days is not None else int(safe_float(params.get("max_hold_days"), 3))
    effective_top_n = top_n if top_n is not None else int(safe_float(params.get("top_n"), 5))
    with quant_engine.temporary_strategy_params(params):
        if intraday:
            payload = quant_engine.walk_forward_intraday(
                start_date=start_date,
                end_date=end_date,
                initial_cash=effective_initial_cash,
                max_positions=effective_max_positions,
                hold_days=effective_hold_days,
                top_n=effective_top_n,
                use_daily_fallback=use_daily_fallback,
                auto_fill=auto_fill,
            )
        else:
            payload = quant_engine.walk_forward(
                start_date=start_date,
                end_date=end_date,
                initial_cash=effective_initial_cash,
                max_positions=effective_max_positions,
                hold_days=effective_hold_days,
                top_n=effective_top_n,
                auto_fill=auto_fill,
            )
    if isinstance(payload, dict):
        payload["strategy_model_id"] = str(model.get("id") or clean_model_id)
        payload["strategy_name"] = str(model.get("name") or clean_model_id)
        payload["strategy_params"] = params
        payload["strategy_scope"] = "strategy_model"
    return payload


def _quant_timeline_cache_ttl() -> int:
    return cache_env_int("QT_TIMELINE_CACHE_TTL_SECONDS", 600, minimum=0, maximum=86400)


def _quant_timeline_cache_parts(
    *,
    model_id: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: Optional[int],
    top_n: Optional[int],
    intraday: bool,
    use_daily_fallback: bool = True,
    auto_fill: bool = True,
) -> Dict[str, Any]:
    clean_model_id = str(model_id or "").strip()
    identity: Dict[str, Any] = {}
    if clean_model_id:
        model = _find_strategy_model(clean_model_id, include_records=False)
        params = quant_engine.strategy_params(model.get("params") if isinstance(model.get("params"), dict) else {})
        identity["model_version"] = strategy_evolution.runtime_model_version(model)
        identity["strategy_name"] = str(model.get("name") or clean_model_id)
    else:
        params = quant_engine.strategy_params()
        identity["strategy_source"] = quant_engine.strategy_source()
    params_hash = hashlib.sha256(
        json.dumps(params, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="ignore")
    ).hexdigest()
    return {
        "model_id": clean_model_id,
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "initial_cash": None if initial_cash is None else round(safe_float(initial_cash), 2),
        "max_positions": None if max_positions is None else int(max_positions),
        "hold_days": None if hold_days is None else int(hold_days),
        "top_n": None if top_n is None else int(top_n),
        "intraday": bool(intraday),
        "use_daily_fallback": bool(use_daily_fallback),
        "auto_fill": bool(auto_fill),
        "params_hash": params_hash,
        "version": APP_VERSION,
        **identity,
    }


def _compact_quant_timeline_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    trades = payload.get("trades") if isinstance(payload.get("trades"), list) else []
    days = payload.get("days") if isinstance(payload.get("days"), list) else []
    return {
        "status": payload.get("status") or "ok",
        "job": "quant_timeline",
        "mode": payload.get("mode") or "",
        "start_date": payload.get("start_date") or "",
        "end_date": payload.get("end_date") or "",
        "strategy_model_id": payload.get("strategy_model_id") or "",
        "strategy_name": payload.get("strategy_name") or "",
        "return_pct": payload.get("return_pct", 0),
        "trade_count": len(trades),
        "closed_trades": payload.get("closed_trades", 0),
        "day_count": len(days),
        "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _compute_quant_timeline_cached(
    *,
    model_id: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: Optional[int],
    top_n: Optional[int],
    intraday: bool,
    use_daily_fallback: bool = True,
    auto_fill: bool = True,
) -> Dict[str, Any]:
    payload = _quant_timeline_payload(
        model_id=model_id,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        intraday=intraday,
        use_daily_fallback=use_daily_fallback,
        auto_fill=auto_fill,
    )
    if isinstance(payload, dict):
        payload["timeline_cache"] = "refresh"
        payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        save_payload_cache(
            "quant_timeline",
            _quant_timeline_cache_parts(
                model_id=model_id,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=hold_days,
                top_n=top_n,
                intraday=intraday,
                use_daily_fallback=use_daily_fallback,
                auto_fill=auto_fill,
            ),
            payload,
            _quant_timeline_cache_ttl(),
        )
        return payload
    return payload


def _queue_quant_timeline_precompute(
    *,
    model_id: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: Optional[int],
    top_n: Optional[int],
    intraday: bool,
    use_daily_fallback: bool = True,
    auto_fill: bool = True,
    process: bool = True,
) -> Dict[str, Any]:
    payload = {
        "model_id": str(model_id or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "initial_cash": initial_cash,
        "max_positions": max_positions,
        "hold_days": hold_days,
        "top_n": top_n,
        "intraday": bool(intraday),
        "mode": "intraday" if intraday else "daily",
        "use_daily_fallback": bool(use_daily_fallback),
        "auto_fill": bool(auto_fill),
    }
    if process:
        return job_manager.run_job_process(
            "quant_timeline",
            payload=payload,
            message="策略时间线回测已转入独立进程运行",
        )

    def execute() -> Dict[str, Any]:
        result = _compute_quant_timeline_cached(
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            intraday=intraday,
            use_daily_fallback=use_daily_fallback,
            auto_fill=auto_fill,
        )
        return _compact_quant_timeline_result(result if isinstance(result, dict) else {})

    return job_manager.run_job_background(
        "quant_timeline",
        execute,
        payload=payload,
        message="策略时间线回测已转入后台运行",
    )


def _pending_quant_timeline(
    *,
    model_id: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: Optional[int],
    top_n: Optional[int],
    intraday: bool,
    use_daily_fallback: bool,
    auto_fill: bool,
    job_result: Dict[str, Any],
) -> Dict[str, Any]:
    status, cache_state, message = _deferred_job_response_state(
        job_result,
        "策略时间线回测正在后台生成，请稍后刷新。",
    )
    return {
        "status": status,
        "source": "quant_timeline",
        "timeline_cache": cache_state,
        "message": message,
        "mode": "intraday" if intraday else "daily",
        "model_id": str(model_id or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "initial_cash": initial_cash,
        "max_positions": max_positions,
        "hold_days": hold_days,
        "top_n": top_n,
        "intraday": bool(intraday),
        "use_daily_fallback": bool(use_daily_fallback),
        "auto_fill": bool(auto_fill),
        "trades": [],
        "equity_curve": [],
        "days": [],
        "job_result": job_result,
    }


def _quant_timeline_cached_or_deferred(
    *,
    model_id: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: Optional[int],
    top_n: Optional[int],
    intraday: bool,
    use_daily_fallback: bool = True,
    auto_fill: bool = True,
    force: bool = False,
    defer: bool = True,
    process: bool = True,
    manual: bool = False,
) -> Dict[str, Any]:
    cache_parts = _quant_timeline_cache_parts(
        model_id=model_id,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        intraday=intraday,
        use_daily_fallback=use_daily_fallback,
        auto_fill=auto_fill,
    )
    cached = None if force else load_payload_cache("quant_timeline", cache_parts, _quant_timeline_cache_ttl())
    if cached:
        cached["timeline_cache"] = "hit"
        return cached
    if _env_flag("QT_TIMELINE_REQUIRE_MANUAL_TRIGGER", True) and not manual:
        return _manual_required_heavy_job(
            "quant_timeline",
            "策略时间线回测需要显式手动触发；普通刷新只读取短缓存，不自动启动重计算。",
            {
                "source": "quant_timeline",
                "timeline_cache": "manual_required",
                "mode": "intraday" if intraday else "daily",
                "model_id": str(model_id or ""),
                "start_date": str(start_date or ""),
                "end_date": str(end_date or ""),
                "initial_cash": initial_cash,
                "max_positions": max_positions,
                "hold_days": hold_days,
                "top_n": top_n,
                "intraday": bool(intraday),
                "use_daily_fallback": bool(use_daily_fallback),
                "auto_fill": bool(auto_fill),
                "trades": [],
                "equity_curve": [],
                "days": [],
            },
        )
    if defer:
        job_result = _queue_quant_timeline_precompute(
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            intraday=intraday,
            use_daily_fallback=use_daily_fallback,
            auto_fill=auto_fill,
            process=process,
        )
        return _pending_quant_timeline(
            model_id=model_id,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            intraday=intraday,
            use_daily_fallback=use_daily_fallback,
            auto_fill=auto_fill,
            job_result=job_result,
        )
    return _compute_quant_timeline_cached(
        model_id=model_id,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        intraday=intraday,
        use_daily_fallback=use_daily_fallback,
        auto_fill=auto_fill,
    )


def _frontend_snapshot_news_stable(
    effective_as_of: str,
    mobile: bool,
    light: bool,
    news_limit: int,
) -> Dict[str, Any]:
    cache_parts = {
        "as_of": effective_as_of,
        "mobile": bool(mobile),
        "light": bool(light),
        "news_limit": int(news_limit),
        "version": APP_VERSION,
    }
    cache_ttl = cache_env_int("QT_FRONT_SNAPSHOT_NEWS_CACHE_TTL_SECONDS", 30, minimum=0, maximum=3600)
    cached = _memory_cache_get("front_snapshot_news", cache_parts, cache_ttl)
    if cached:
        return cached
    if light and _env_flag("QT_FRONT_SNAPSHOT_LIGHT_NEWS_NO_ENGINE_FALLBACK", True):
        news_payload = _frontend_light_news_feed(as_of=effective_as_of, limit=news_limit, fallback_latest=True)
    else:
        news_payload = _safe_news_feed(as_of=effective_as_of, limit=news_limit, fallback_latest=True)
    return _memory_cache_set(
        "front_snapshot_news",
        cache_parts,
        {
            "news": news_payload,
            "market_sentiment": _market_sentiment(news_payload),
        },
    )


def _frontend_public_snapshot_payload(
    as_of: Optional[str] = None,
    mobile: bool = False,
    light: bool = True,
):
    news_limit = 12 if mobile or light else 80
    light_jobs = _frontend_jobs_payload()
    effective_as_of = _frontend_account_as_of(as_of)
    cache_parts = {
        "as_of": effective_as_of,
        "mobile": bool(mobile),
        "light": bool(light),
        "news_limit": news_limit,
        "version": APP_VERSION,
    }
    cache_ttl = cache_env_int("QT_PUBLIC_SNAPSHOT_CACHE_TTL_SECONDS", 30, minimum=0, maximum=3600)
    stable = _memory_cache_get("front_public_snapshot", cache_parts, cache_ttl)
    if not stable:
        news_stable = _frontend_snapshot_news_stable(effective_as_of, bool(mobile), bool(light), news_limit)
        stable = _memory_cache_set(
            "front_public_snapshot",
            cache_parts,
            news_stable,
        )
    return {
        "status": "ok",
        "status_payload": _light_status_payload(as_of=effective_as_of, jobs_payload=light_jobs, include_data_dir=False),
        "jobs": light_jobs,
        **stable,
    }


def _frontend_snapshot_payload(
    request: Request,
    as_of: Optional[str] = None,
    mobile: bool = False,
    light: bool = True,
    include_catalog: bool = False,
):
    news_limit = 12 if mobile or light else 80
    top_n = 12 if mobile else 30
    visible_jobs = _frontend_jobs_payload()
    effective_as_of = _frontend_account_as_of(as_of)
    catalog_included = bool(include_catalog or not light)
    context = _frontend_profile_context(request, include_catalog=catalog_included)
    cache_parts = _frontend_payload_cache_parts(
        context,
        "front_snapshot",
        {
            "as_of": effective_as_of,
            "mobile": bool(mobile),
            "light": bool(light),
            "news_limit": news_limit,
            "top_n": top_n,
            "include_catalog": catalog_included,
            "version": APP_VERSION,
        },
    )
    cache_ttl = cache_env_int("QT_FRONT_SNAPSHOT_CACHE_TTL_SECONDS", 45, minimum=0, maximum=3600)
    stable = _memory_cache_get("front_snapshot", cache_parts, cache_ttl)
    if not stable:
        news_stable = _frontend_snapshot_news_stable(effective_as_of, bool(mobile), bool(light), news_limit)
        trading_account: Dict[str, Any] = {}
        recommendations: Dict[str, Any] = {}
        daily_plan: Dict[str, Any] = {}
        try:
            trading_account = _frontend_strategy_account(
                context,
                effective_as_of,
                limit=80 if light else 500,
                record_period=not light,
                persist_derived=not light,
                hydrate_runtime_trades=not light,
            )
            trading_account = _frontend_trading_account(trading_account, context)
        except Exception as exc:
            job_manager._append_log("warning", f"前台当前策略持仓快照失败：{exc}", job="frontend_snapshot", stage="account")
            trading_account = {}
        if not light:
            recommendations, daily_plan = _frontend_cached_recommendations_and_plan(context, effective_as_of, top_n=top_n, limit_days=120)
        stable = {
            "frontend_profile": context["profile"],
            "followed_model": context["followed_model"],
            "strategy_models": context["models_payload"],
            "strategy_catalog_included": catalog_included,
            **news_stable,
        }
        if trading_account:
            stable["trading_account"] = trading_account
        if recommendations:
            stable["recommendations"] = recommendations
        if daily_plan:
            stable["daily_plan"] = daily_plan
        stable = _memory_cache_set("front_snapshot", cache_parts, stable)
    if isinstance(stable.get("trading_account"), dict):
        stable = dict(stable)
        stable["trading_account"] = _attach_frontend_account_precompute(
            _copy_payload(stable["trading_account"]),
            context,
            effective_as_of,
            reason="account_runtime_missing",
        )
    payload = {
        "status": "ok",
        "status_payload": _light_status_payload(as_of=effective_as_of, jobs_payload=visible_jobs, include_data_dir=False),
        "jobs": visible_jobs,
        **stable,
    }
    return payload


def _frontend_strategy_models_route_payload(request: Request):
    context = _frontend_profile_context(request, include_catalog=True)
    return {
        "status": "ok",
        "frontend_profile": context["profile"],
        "followed_model": context["followed_model"],
        "strategy_models": context["models_payload"],
        "strategy_catalog_included": True,
    }


def _frontend_trading_account_payload(
    request: Request,
    as_of: Optional[str] = None,
    limit: int = 500,
    force: bool = False,
    defer: bool = True,
):
    context = _frontend_profile_context(request, include_catalog=False)
    persist_on_read = bool(force or _env_flag("QT_FRONT_ACCOUNT_PERSIST_ON_READ", False))
    account = _frontend_strategy_account(
        context,
        as_of,
        limit=limit,
        force=force,
        record_period=persist_on_read,
        defer_miss=bool(defer and not force),
        persist_derived=persist_on_read,
    )
    payload = _frontend_trading_account(account, context)
    if not force:
        payload = _attach_frontend_account_precompute(payload, context, as_of, reason="account_runtime_missing")
    return payload


def _frontend_recommendations_payload(
    request: Request,
    as_of: Optional[str] = None,
    lookback_days: int = 2,
    top_n: int = 30,
    force: bool = False,
    defer: bool = True,
):
    context = _frontend_profile_context(request, include_catalog=False)
    effective_as_of = _frontend_account_as_of(as_of)
    cache_parts = _frontend_payload_cache_parts(
        context,
        "front_recommendations",
        {
            "as_of": effective_as_of,
            "lookback_days": lookback_days,
            "top_n": top_n,
        },
    )
    ttl = _frontend_payload_cache_ttl("QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS", 1800)
    cached = None if force else load_payload_cache("front_recommendations", cache_parts, ttl)
    if cached:
        return cached
    if defer and not force:
        job_result = _queue_frontend_payload_precompute(
            context,
            effective_as_of,
            lookback_days=lookback_days,
            top_n=top_n,
            limit_days=120,
        )
        return _frontend_pending_payload(
            "front_recommendations",
            effective_as_of,
            job_result,
            lookback_days=lookback_days,
            top_n=top_n,
        )
    with quant_engine.temporary_strategy_params(context["strategy_params"]):
        payload = quant_engine.recommendations(as_of=effective_as_of, lookback_days=lookback_days, top_n=top_n)
    payload = _affordable_payload(payload, context, effective_as_of)
    payload["frontend_payload_cache"] = "miss"
    save_payload_cache("front_recommendations", cache_parts, payload, ttl)
    return payload


def _frontend_daily_plan_payload(
    request: Request,
    as_of: Optional[str] = None,
    start_date: Optional[str] = None,
    limit_days: int = 120,
    force: bool = False,
    defer: bool = True,
):
    context = _frontend_profile_context(request, include_catalog=False)
    effective_as_of = _frontend_account_as_of(as_of)
    effective_start = start_date or _frontend_replay_start_date(effective_as_of)
    cache_parts = _frontend_payload_cache_parts(
        context,
        "front_daily_plan",
        {
            "as_of": effective_as_of,
            "start_date": effective_start,
            "limit_days": limit_days,
        },
    )
    ttl = _frontend_payload_cache_ttl("QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS", 1800)
    cached = None if force else load_payload_cache("front_daily_plan", cache_parts, ttl)
    if cached:
        return cached
    if defer and not force:
        job_result = _queue_frontend_payload_precompute(
            context,
            effective_as_of,
            lookback_days=2,
            top_n=30,
            limit_days=limit_days,
        )
        return _frontend_pending_payload(
            "front_daily_plan",
            effective_as_of,
            job_result,
            start_date=effective_start,
            limit_days=limit_days,
        )
    with quant_engine.temporary_strategy_params(context["strategy_params"]):
        payload = quant_engine.daily_plan(as_of=effective_as_of, start_date=effective_start, limit_days=limit_days)
    payload = _affordable_payload(payload, context, effective_as_of)
    payload["frontend_payload_cache"] = "miss"
    save_payload_cache("front_daily_plan", cache_parts, payload, ttl)
    return payload


def _admin_snapshot_payload(as_of: Optional[str] = None, light: bool = True):
    effective_as_of = _frontend_account_as_of(as_of)
    if light:
        jobs_payload = _jobs_status_payload(light=True)
        cache_parts = {"as_of": effective_as_of, "version": APP_VERSION}
        cache_ttl = cache_env_int("QT_ADMIN_SNAPSHOT_CACHE_TTL_SECONDS", 20, minimum=0, maximum=3600)
        stable = _memory_cache_get("admin_snapshot_light", cache_parts, cache_ttl)
        if not stable:
            news_payload = _safe_news_feed(as_of=effective_as_of, limit=60, fallback_latest=True)
            models_payload = _frontend_strategy_models_payload(include_catalog=True)
            model_signals = _admin_model_signal_feed(effective_as_of, models_payload=models_payload, limit_models=24, limit_per_model=12)
            try:
                dashboard = _light_dashboard_payload(effective_as_of, news_payload=news_payload, model_signals=model_signals)
            except Exception as exc:
                job_manager._append_log("warning", f"后台轻量信号快照失败：{exc}", job="admin_snapshot", stage="dashboard")
                dashboard = {
                    "status": "ok",
                    "as_of": effective_as_of,
                    "strategy_params": quant_engine.strategy_params(),
                    "strategy_source": quant_engine.strategy_source(),
                    "recommendations": {"status": "error", "items": [], "latest_events": [], "error": str(exc)},
                    "timeline": {},
                }
            stable = _memory_cache_set(
                "admin_snapshot_light",
                cache_parts,
                {
                    "strategy_models": models_payload,
                    "frontend_users": frontend_user_summary(),
                    "dashboard": dashboard,
                    "model_signals": model_signals,
                    "news": news_payload,
                    "market_sentiment": _market_sentiment(news_payload),
                },
            )
        return {
            "status": "ok",
            "status_payload": _light_status_payload(as_of=effective_as_of, jobs_payload=jobs_payload),
            "jobs": jobs_payload,
            "biying": biying_minute_sync.status(),
            "lhb": lhb_status(),
            "notification_status": trade_notifier.status(),
            "evolution_status": strategy_evolution.status(),
            **stable,
        }
    models_payload = _frontend_strategy_models_payload(include_catalog=True)
    news_payload = quant_engine.news_feed(as_of=effective_as_of, limit=120, fallback_latest=True)
    return {
        "status": "ok",
        "status_payload": _status_payload(),
        "jobs": _jobs_status_payload(light=True),
        "biying": biying_minute_sync.status(),
        "lhb": lhb_status(),
        "ai_usage": ai_usage_summary(),
        "notification_status": trade_notifier.status(),
        "evolution_status": strategy_evolution.status(),
        "strategy_models": models_payload,
        "access_logs": access_logs(limit=120),
        "frontend_users": _admin_frontend_user_summary(),
        "dashboard": quant_engine.dashboard(as_of=effective_as_of, include_heavy=False),
        "trading_account": _admin_strategy_trading_account(as_of=effective_as_of, limit=1000),
        "model_signals": _admin_model_signal_feed(effective_as_of, models_payload=models_payload, limit_models=32, limit_per_model=20),
        "news": news_payload,
        "coverage": _data_coverage_payload(
            as_of=effective_as_of,
            top_n=100,
            defer=_env_flag("QT_DATA_COVERAGE_DEFER_MISSES", True),
        ),
        "ai_failures": ai_failures(limit=40),
        "ai_records": ai_records_feed(limit=80),
    }


def _admin_model_signals_payload(
    as_of: Optional[str] = None,
    limit_models: int = 24,
    limit_per_model: int = 12,
):
    effective_as_of = _frontend_account_as_of(as_of)
    models_payload = _frontend_strategy_models_payload(include_catalog=True)
    return _admin_model_signal_feed(
        effective_as_of,
        models_payload=models_payload,
        limit_models=limit_models,
        limit_per_model=limit_per_model,
    )


def _admin_strategy_trading_account(
    as_of: Optional[str] = None,
    model_id: Optional[str] = None,
    initial_cash: Optional[float] = None,
    start_date: Optional[str] = None,
    limit: int = 1000,
) -> Dict[str, Any]:
    effective_as_of = _frontend_account_as_of(as_of)
    models_payload = _frontend_strategy_models_payload(include_catalog=True)
    catalog = [item for item in _strategy_catalog_items(models_payload) if str((item or {}).get("id") or "") != "active"]
    requested_id = str(model_id or "").strip()
    if not requested_id:
        ready = next((item for item in catalog if item.get("has_runtime_data")), None)
        requested_id = str((ready or {}).get("id") or DEFAULT_FRONTEND_STRATEGY_ID)
    model = next((item for item in catalog if str(item.get("id") or "") == requested_id), None)
    if not model:
        raise HTTPException(status_code=404, detail="strategy model not found")
    base_params = model.get("params") if isinstance(model.get("params"), dict) else {}
    cash = safe_float(initial_cash, safe_float(base_params.get("account_initial_cash"), safe_float(model.get("initial_cash"), 10_000)))
    params = quant_engine.strategy_params(base_params)
    params = apply_capital_constraints(params, cash)
    selected_start = str(start_date or model.get("runtime_start_date") or quant_engine.first_data_date() or "").strip() or None
    model_version = strategy_evolution.runtime_model_version(model)
    payload = strategy_evolution.load_runtime_account(
        requested_id,
        cash,
        selected_start,
        effective_as_of,
        limit,
        model_version=model_version,
        params=params,
    )
    if not payload:
        payload = {
            "status": "missing",
            "as_of": effective_as_of,
            "start_date": selected_start or "",
            "account": {
                "initial_cash": round(cash, 2),
                "total_asset": round(cash, 2),
                "cash": round(cash, 2),
                "available_cash": round(cash, 2),
                "market_value": 0,
                "position_count": 0,
                "deal_count": 0,
                "return_pct": 0,
            },
            "positions": [],
            "today_deals": [],
            "history_deals": [],
            "delivery_records": [],
            "daily_settlements": [],
            "message": "该策略还没有复盘运行表，请先运行策略复盘或上传合并本地复盘数据",
        }
    payload["strategy_name"] = str(model.get("name") or requested_id)
    payload["strategy_model_id"] = requested_id
    payload["strategy_model"] = model
    payload["strategy_account_source"] = payload.get("strategy_account_source") or "strategy_runtime"
    payload["strategy_scope"] = "strategy_runtime"
    payload["strategy_params"] = params
    payload["selected_initial_cash"] = round(cash, 2)
    payload["selected_start_date"] = selected_start or ""
    payload["selected_as_of"] = effective_as_of or ""
    return payload


def _admin_strategy_runtime_replay(
    as_of: Optional[str] = None,
    model_id: Optional[str] = None,
    initial_cash: Optional[float] = None,
    start_date: Optional[str] = None,
    limit: int = 1000,
) -> Dict[str, Any]:
    payload = _admin_strategy_trading_account(
        as_of=as_of,
        model_id=model_id,
        initial_cash=initial_cash,
        start_date=start_date,
        limit=limit,
    )
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    trades = payload.get("history_deals") if isinstance(payload.get("history_deals"), list) else []
    deliveries = payload.get("delivery_records") if isinstance(payload.get("delivery_records"), list) else trades
    settlements = payload.get("daily_settlements") if isinstance(payload.get("daily_settlements"), list) else []
    initial = safe_float(account.get("initial_cash"), safe_float(payload.get("selected_initial_cash"), 0))
    final_value = safe_float(account.get("total_asset"), initial)
    return_pct = safe_float(account.get("return_pct"), ((final_value / initial - 1) * 100 if initial > 0 else 0))
    sell_rows = [
        item
        for item in deliveries
        if str(item.get("side") or item.get("direction") or "").upper() in {"SELL", "卖出"}
    ]
    closed_trades = len(sell_rows)
    wins = [item for item in sell_rows if safe_float(item.get("realized_pnl"), 0) > 0]
    win_rate = round(len(wins) / closed_trades * 100, 2) if closed_trades else 0.0
    curve = []
    cumulative_realized = 0.0
    for row in sorted([item for item in settlements if isinstance(item, dict)], key=lambda item: str(item.get("date") or "")):
        cumulative_realized += safe_float(row.get("realized_pnl"), 0)
        value = initial + cumulative_realized
        curve.append(
            {
                "date": str(row.get("date") or ""),
                "total_value": round(value, 2),
                "return_pct": round((value / initial - 1) * 100, 3) if initial > 0 else 0.0,
                "deal_count": int(safe_float(row.get("deal_count"), 0)),
            }
        )
    if not curve and payload.get("selected_as_of"):
        curve.append(
            {
                "date": payload.get("selected_as_of"),
                "total_value": round(final_value, 2),
                "return_pct": round(return_pct, 3),
                "deal_count": len(trades),
            }
        )
    peak = initial if initial > 0 else 1.0
    max_drawdown = 0.0
    for point in curve:
        value = safe_float(point.get("total_value"), peak)
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
    return {
        "status": payload.get("status") or "ok",
        "source": "strategy_runtime",
        "model_id": payload.get("strategy_model_id"),
        "strategy_model_id": payload.get("strategy_model_id"),
        "strategy_name": payload.get("strategy_name"),
        "strategy_model": payload.get("strategy_model"),
        "mode": "strategy_runtime",
        "start_date": payload.get("selected_start_date") or payload.get("start_date") or "",
        "end_date": payload.get("selected_as_of") or payload.get("as_of") or "",
        "initial_cash": round(initial, 2),
        "final_value": round(final_value, 2),
        "return_pct": round(return_pct, 3),
        "max_drawdown_pct": round(max_drawdown, 3),
        "win_rate": win_rate,
        "closed_trades": closed_trades,
        "trade_count": len(trades),
        "runtime_signal_count": payload.get("runtime_signal_count", 0),
        "runtime_generated_at": payload.get("runtime_generated_at", ""),
        "account": account,
        "positions": payload.get("positions", []),
        "trades": trades,
        "trade_records": trades,
        "delivery_records": deliveries,
        "daily_settlements": settlements,
        "equity_curve": curve,
        "days": payload.get("days", []),
        "message": payload.get("message", ""),
        "strategy_params": payload.get("strategy_params", {}),
    }


app.include_router(
    build_frontend_profile_router(
        profile_payload=_frontend_profile_payload,
        update_profile_payload=_frontend_profile_update_payload,
    )
)


app.include_router(
    build_frontend_runtime_router(
        public_snapshot_payload=_frontend_public_snapshot_payload,
        snapshot_payload=_frontend_snapshot_payload,
        strategy_models_payload=_frontend_strategy_models_route_payload,
        trading_account_payload=_frontend_trading_account_payload,
        account_defer_default=_env_flag("QT_FRONT_ACCOUNT_DEFER_MISSES", True),
    )
)


app.include_router(
    build_frontend_signal_router(
        recommendations_payload=_frontend_recommendations_payload,
        daily_plan_payload=_frontend_daily_plan_payload,
        payload_defer_default=_env_flag("QT_FRONT_PAYLOAD_DEFER_MISSES", True),
    )
)


app.include_router(
    build_admin_overview_router(
        snapshot_payload=_admin_snapshot_payload,
        model_signals_payload=_admin_model_signals_payload,
    )
)


app.include_router(
    build_admin_strategy_runtime_router(
        matrix_payload=_admin_strategy_runtime_matrix_payload,
        trading_account_payload=_admin_strategy_trading_account,
        replay_payload=_admin_strategy_runtime_replay,
    )
)


@app.websocket("/ws/admin/live")
async def admin_live(websocket: WebSocket):
    await websocket.accept()
    try:
        auth_message = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        verify_token(str(auth_message.get("token") or ""), "admin")
    except Exception:
        await websocket.close(code=1008)
        return

    sent_logs: set[str] = set()
    status_fp = ""
    jobs_fp = ""
    biying_fp = ""
    try:
        while True:
            jobs_payload = _jobs_status_payload(light=True)
            status_payload = _light_status_payload(jobs_payload=jobs_payload)
            biying_payload = biying_minute_sync.status()
            logs_payload = job_manager.logs(limit=120)
            logs_delta = []
            for item in reversed(logs_payload.get("items", [])):
                if not isinstance(item, dict):
                    continue
                key = _log_key(item)
                if key in sent_logs:
                    continue
                sent_logs.add(key)
                logs_delta.append(item)
            if len(sent_logs) > 1000:
                sent_logs = set(list(sent_logs)[-500:])

            message: Dict[str, Any] = {
                "type": "live_delta",
                "server_time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            }
            next_status_fp = _json_fingerprint(status_payload)
            next_jobs_fp = _json_fingerprint(jobs_payload)
            next_biying_fp = _json_fingerprint(biying_payload)
            if next_status_fp != status_fp:
                message["status_payload"] = status_payload
                status_fp = next_status_fp
            if next_jobs_fp != jobs_fp:
                message["jobs"] = jobs_payload
                jobs_fp = next_jobs_fp
            if next_biying_fp != biying_fp:
                message["biying"] = biying_payload
                biying_fp = next_biying_fp
            if logs_delta:
                message["logs_delta"] = logs_delta
            if len(message) > 2:
                await websocket.send_json(message)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        return


def _quant_dashboard_payload(as_of: Optional[str] = None, light: bool = False) -> Dict[str, Any]:
    return quant_engine.dashboard(as_of=as_of, include_heavy=not light)


def _quant_recommendations_payload(
    as_of: Optional[str] = None,
    lookback_days: int = 2,
    top_n: int = 30,
) -> Dict[str, Any]:
    return quant_engine.recommendations(as_of=as_of, lookback_days=lookback_days, top_n=top_n)


def _quant_daily_plan_payload(
    as_of: Optional[str] = None,
    start_date: Optional[str] = None,
    limit_days: int = 80,
) -> Dict[str, Any]:
    return quant_engine.daily_plan(as_of=as_of, start_date=start_date, limit_days=limit_days)


def _quant_strategy_params_payload() -> Dict[str, Any]:
    return {
        "status": "ok",
        "strategy_params": quant_engine.strategy_params(),
        "strategy_source": quant_engine.strategy_source(),
        "model_weights": quant_engine.model_weights(),
    }


def _quant_update_strategy_params_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return quant_engine.update_strategy_params(payload)


def _quant_reset_strategy_params_payload() -> Dict[str, Any]:
    return quant_engine.reset_strategy_params()


def _quant_events_payload(as_of: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    events = quant_engine.events()
    if as_of:
        events = [event for event in events if event.date <= as_of]
    return {"items": [event.compact() for event in events[:limit]], "count": len(events)}


def _quant_news_payload(
    as_of: Optional[str] = None,
    limit: int = 120,
    fallback_latest: bool = True,
    source: Optional[str] = None,
    keyword: Optional[str] = None,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    return _safe_news_feed(
        as_of=as_of,
        limit=limit,
        fallback_latest=fallback_latest,
        source=source,
        keyword=keyword,
        code=code,
    )


def _quant_correlation_payload(as_of: Optional[str] = None, hold_days: int = 3) -> Dict[str, Any]:
    return quant_engine.correlation(as_of=as_of, hold_days=hold_days)


def _quant_portfolio_payload(as_of: Optional[str] = None) -> Dict[str, Any]:
    return quant_engine.paper_portfolio(as_of=as_of)


def _quant_trading_account_basic_payload(as_of: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
    return quant_engine.trading_account(as_of=as_of, limit=limit)


def _quant_run_payload(as_of: Optional[str] = None, calibrate: bool = True) -> Dict[str, Any]:
    calibration = quant_engine.calibrate_model(as_of=as_of) if calibrate else None
    portfolio = quant_engine.run_paper_trading(as_of=as_of)
    notification = trade_notifier.notify_trade_events(
        portfolio.get("trades", []) if isinstance(portfolio.get("trades"), list) else [],
        as_of=portfolio["as_of"],
        source="manual_quant_run",
    )
    recommendations = quant_engine.recommendations(as_of=portfolio["as_of"], lookback_days=2, top_n=30)
    return {
        "status": "ok",
        "as_of": portfolio["as_of"],
        "calibration": calibration,
        "portfolio": portfolio,
        "notification": notification,
        "recommendations": recommendations,
    }


def _news_history_payload(limit: int = 200) -> Dict[str, Any]:
    items = quant_engine.load_news_history()[:limit]
    return {"items": items, "count": len(items)}


app.include_router(
    build_quant_basic_router(
        dashboard_payload=_quant_dashboard_payload,
        recommendations_payload=_quant_recommendations_payload,
        daily_plan_payload=_quant_daily_plan_payload,
        strategy_params_payload=_quant_strategy_params_payload,
        strategy_params_update_payload=_quant_update_strategy_params_payload,
        strategy_params_reset_payload=_quant_reset_strategy_params_payload,
        events_payload=_quant_events_payload,
        news_payload=_quant_news_payload,
        correlation_payload=_quant_correlation_payload,
        portfolio_payload=_quant_portfolio_payload,
        trading_account_payload=_quant_trading_account_basic_payload,
        run_payload=_quant_run_payload,
        news_history_payload=_news_history_payload,
    )
)


def _compact_quant_fit_strategy_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    best = payload.get("best") if isinstance(payload.get("best"), dict) else {}
    return {
        "status": payload.get("status") or "ok",
        "job": "fit_strategy",
        "as_of": payload.get("as_of") or "",
        "start_date": payload.get("start_date") or "",
        "applied": bool(payload.get("applied")),
        "best_name": best.get("name") or "",
        "objective": best.get("objective", 0),
        "return_pct": best.get("return_pct", 0),
        "candidate_count": len(payload.get("candidates") if isinstance(payload.get("candidates"), list) else []),
        "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _compute_quant_fit_strategy(
    *,
    as_of: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    apply_best: bool = True,
) -> Dict[str, Any]:
    payload = quant_engine.fit_strategy(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        apply_best=apply_best,
    )
    if isinstance(payload, dict):
        payload["source"] = "fit_strategy"
        payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    return payload


def _queue_quant_fit_strategy(
    *,
    as_of: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    apply_best: bool,
    process: bool = True,
) -> Dict[str, Any]:
    payload = {
        "as_of": str(as_of or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "apply_best": bool(apply_best),
    }
    if process:
        return job_manager.run_job_process(
            "fit_strategy",
            payload=payload,
            message="参数拟合已转入独立进程运行",
        )

    def execute() -> Dict[str, Any]:
        result = _compute_quant_fit_strategy(**payload)
        return _compact_quant_fit_strategy_result(result if isinstance(result, dict) else {})

    return job_manager.run_job_background(
        "fit_strategy",
        execute,
        payload=payload,
        message="参数拟合已转入后台运行",
    )


def _pending_quant_fit_strategy(
    *,
    as_of: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    apply_best: bool,
    job_result: Dict[str, Any],
) -> Dict[str, Any]:
    status, _cache_state, message = _deferred_job_response_state(
        job_result,
        "参数拟合正在后台生成，请查看任务状态，完成后刷新策略参数。",
    )
    return {
        "status": status,
        "source": "fit_strategy",
        "message": message,
        "as_of": str(as_of or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "apply_best": bool(apply_best),
        "applied": False,
        "best": {},
        "candidates": [],
        "strategy_params": quant_engine.strategy_params(),
        "job_result": job_result,
    }


def _quant_fit_strategy_deferred_or_sync(
    *,
    as_of: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    apply_best: bool,
    defer: bool = True,
    process: bool = True,
) -> Dict[str, Any]:
    if defer:
        job_result = _queue_quant_fit_strategy(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            apply_best=apply_best,
            process=process,
        )
        return _pending_quant_fit_strategy(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            apply_best=apply_best,
            job_result=job_result,
        )
    return _compute_quant_fit_strategy(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        apply_best=apply_best,
    )


@app.post("/api/quant/fit_strategy")
def quant_fit_strategy(
    as_of: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    apply_best: bool = Query(default=True),
    defer: bool = Query(default=_env_flag("QT_FIT_STRATEGY_DEFER_MISSES", True)),
    process: bool = Query(default=_env_flag("QT_FIT_STRATEGY_PROCESS_ENABLED", True)),
):
    return _quant_fit_strategy_deferred_or_sync(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        apply_best=apply_best,
        defer=defer,
        process=process,
    )


@app.get("/api/quant/evolution/status")
def quant_evolution_status():
    return strategy_evolution.status()


@app.get("/api/quant/evolution/trace")
def quant_evolution_trace(
    run_id: Optional[str] = Query(default=None),
    generation: Optional[int] = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=2000),
):
    return strategy_evolution.trace(run_id=run_id, generation=generation, limit=limit)


@app.post("/api/quant/evolution/pause")
def quant_pause_evolution():
    return strategy_evolution.pause()


@app.post("/api/quant/evolution/resume")
def quant_resume_evolution():
    return strategy_evolution.resume()


@app.get("/api/quant/models")
def quant_strategy_models():
    return strategy_evolution.models()


@app.get("/api/quant/model/backtest")
def quant_strategy_model_backtest(
    model_id: str = Query(default="active"),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    mode: str = Query(default="intraday"),
    limit: int = Query(default=0, ge=0, le=5000),
    recompute: bool = Query(default=False),
    force: bool = Query(default=False),
    defer: bool = Query(default=_env_flag("QT_MODEL_BACKTEST_DEFER_RECOMPUTE", True)),
    manual: bool = Query(default=False),
    process: bool = Query(default=_env_flag("QT_MODEL_BACKTEST_PROCESS_ENABLED", True)),
):
    model = _find_strategy_model(model_id)
    if not recompute:
        return _stored_model_backtest_payload(model, limit=limit)
    return _model_backtest_recompute_payload(
        model=model,
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        limit=limit,
        force=force,
        defer=defer,
        manual=manual,
        process=process,
    )


@app.post("/api/quant/model/apply")
def quant_apply_strategy_model(model_id: str = Query(...)):
    models_payload = _frontend_strategy_models_payload(include_catalog=True)
    model = next((item for item in _strategy_catalog_items(models_payload) if str(item.get("id") or "") == str(model_id)), None)
    if not model:
        raise HTTPException(status_code=404, detail="strategy model not found")
    params = model.get("params") if isinstance(model.get("params"), dict) else {}
    source_type = "capital_preset" if model.get("is_capital_preset") else "strategy_model"
    result = quant_engine.update_strategy_params(
        params,
        source={
            "type": source_type,
            "model_id": str(model.get("id") or ""),
            "name": str(model.get("name") or model.get("id") or ""),
            "description": "来自资金档策略复制为系统默认基础参数。" if source_type == "capital_preset" else "来自策略库模型复制为系统默认基础参数。",
            "objective": model.get("objective"),
            "return_pct": model.get("return_pct"),
            "max_drawdown_pct": model.get("max_drawdown_pct"),
            "win_rate": model.get("win_rate"),
        },
    )
    if source_type == "strategy_model":
        strategy_evolution.mark_applied_model(model)
    return {
        "status": "ok",
        "model": model,
        "strategy_params": result.get("strategy_params"),
        "strategy_source": result.get("strategy_source"),
    }


@app.post("/api/quant/evolve_strategy")
def quant_evolve_strategy(
    generations: int = Query(default=4, ge=1, le=30),
    population_size: int = Query(default=16, ge=6, le=80),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    apply_best: bool = Query(default=False),
    mode: str = Query(default="intraday"),
    background: bool = Query(default=True),
    process: bool = Query(default=_env_flag("QT_HEAVY_JOB_PROCESS_ENABLED", True)),
):
    current = strategy_evolution.status()
    if current.get("status") == "running":
        return current
    if process:
        return job_manager.run_strategy_evolution(
            start_date=start_date,
            end_date=end_date,
            mode=mode,
            generations=generations,
            population_size=population_size,
            apply_best=apply_best,
            process=True,
        )
    if background:
        def worker() -> None:
            job_manager.run_strategy_evolution(
                start_date=start_date,
                end_date=end_date,
                mode=mode,
                generations=generations,
                population_size=population_size,
                apply_best=apply_best,
            )

        threading.Thread(target=worker, name="strategy-evolution", daemon=True).start()
        return {
            "status": "running",
            "progress_pct": 1,
            "progress_message": "进化任务已启动，后台持续运行",
            "generations": generations,
            "population_size": population_size,
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode,
            "background": True,
        }
    return strategy_evolution.run(
        generations=generations,
        population_size=population_size,
        start_date=start_date,
        end_date=end_date,
        apply_best=apply_best,
        mode=mode,
    )


app.include_router(
    build_admin_jobs_router(
        status_payload=_jobs_status_payload,
        logs_payload=lambda limit, level, job: job_manager.logs(limit=limit, level=level, job=job),
        scheduler_start_payload=lambda: job_manager.start(),
        scheduler_stop_payload=lambda: job_manager.stop(),
        pause_payload=lambda job_name: job_manager.pause_job(job_name),
        resume_payload=lambda job_name: job_manager.resume_job(job_name),
        stop_payload=lambda job_name: job_manager.stop_job(job_name),
    )
)


def _jobs_news_fetch_payload(
    hours: int,
    pages: int,
    page_size: int,
    background: bool,
    process: bool,
) -> Dict[str, Any]:
    return job_manager.run_news_fetch(
        hours=hours,
        pages=pages,
        page_size=page_size,
        refresh_events=True,
        background=background,
        process=process,
    )


def _jobs_market_sync_payload(
    date: Optional[str],
    source: str,
    max_codes: int,
    force: bool,
    include_latest: bool,
    background: bool,
    process: bool,
) -> Dict[str, Any]:
    return job_manager.run_market_sync(
        date=date,
        source=source,
        max_codes=max_codes,
        force=force,
        include_latest=include_latest,
        background=background,
        process=process,
    )


def _jobs_ai_analyze_payload(
    as_of: Optional[str],
    max_items: int,
    batch_size: int,
    background: bool,
    process: bool,
) -> Dict[str, Any]:
    return job_manager.run_ai_analysis(
        as_of=as_of,
        max_items=max_items,
        batch_size=batch_size,
        background=background,
        process=process,
    )


def _jobs_trading_run_payload(
    date: Optional[str],
    notify: bool,
    background: bool,
    process: bool,
) -> Dict[str, Any]:
    return job_manager.run_trade_cycle(date=date, notify=notify, background=background, process=process)


def _jobs_strategy_replay_payload(
    start_date: Optional[str],
    end_date: Optional[str],
    mode: str,
    batch_days: Optional[int],
    use_cursor: bool,
    background: bool,
    process: bool,
) -> Dict[str, Any]:
    return job_manager.run_strategy_replay(
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        background=background,
        batch_days=batch_days,
        use_cursor=use_cursor,
        process=process,
    )


def _jobs_frontend_payload_precompute_payload(
    as_of: Optional[str],
    usernames: Optional[str],
    limit_users: int,
    force: bool,
    background: bool,
    process: bool,
    lookback_days: int,
    top_n: int,
    limit_days: int,
    max_seconds: Optional[int],
) -> Dict[str, Any]:
    return job_manager.run_frontend_payload_precompute(
        as_of=as_of,
        usernames=usernames,
        limit_users=limit_users,
        force=force,
        background=background,
        process=process,
        lookback_days=lookback_days,
        top_n=top_n,
        limit_days=limit_days,
        max_seconds=max_seconds,
    )


def _jobs_frontend_account_precompute_payload(
    as_of: Optional[str],
    usernames: Optional[str],
    limit_users: int,
    limit: int,
    force: bool,
    background: bool,
    process: bool,
    drain_queue: Optional[bool],
) -> Dict[str, Any]:
    queue_status = _frontend_account_precompute_queue_status()
    effective_drain_queue = bool(drain_queue) if drain_queue is not None else (
        not str(usernames or "").strip() and int(safe_float(queue_status.get("queued"), 0)) > 0
    )
    payload = {
        "as_of": as_of,
        "usernames": usernames,
        "limit_users": limit_users,
        "limit": limit,
        "force": bool(force),
        "drain_queue": effective_drain_queue,
    }

    def execute() -> Dict[str, Any]:
        return _precompute_frontend_accounts(**payload)

    if process:
        return job_manager.run_job_process(
            "frontend_account_precompute",
            payload=payload,
            message="前台账户快照预热已转入独立进程运行",
        )
    if background:
        return job_manager.run_job_background(
            "frontend_account_precompute",
            execute,
            payload=payload,
            message="前台账户快照预热已转入后台运行",
        )
    return job_manager.run_job("frontend_account_precompute", execute, payload=payload)


def _run_system_startup_flow(
    target_date: str,
    replay_start_date: str,
    news_hours: int,
    news_pages: int,
    ai_items: int,
    market_codes: int,
    notify: bool,
    run_strategy_replay: bool = False,
) -> Dict[str, Any]:
    steps = []

    def stopped_before(stage: str) -> Optional[Dict[str, Any]]:
        if not job_manager.is_stop_requested("system_startup"):
            return None
        message = f"系统启动流程已在{stage}前停止"
        job_manager.update_progress("system_startup", 100, message, {"step": stage, "stopped": True})
        return {
            "status": "stopped",
            "message": message,
            "start_date": replay_start_date,
            "date": target_date,
            "steps": steps,
        }

    stopped = stopped_before("新闻抓取")
    if stopped:
        return stopped
    job_manager.update_progress("system_startup", 8, "抓取新闻", {"step": "news_fetch"})
    news_result = job_manager.run_news_fetch(hours=news_hours, pages=news_pages, page_size=20)
    if news_result.get("status") == "ok":
        quant_engine.events(force=True)
    steps.append({"name": "新闻抓取", "job": "news_fetch", "result": news_result})

    stopped = stopped_before("AI 分析")
    if stopped:
        return stopped
    job_manager.update_progress("system_startup", 22, "AI 分析", {"step": "ai_analysis"})
    ai_result = job_manager.run_ai_analysis(as_of=target_date, max_items=ai_items, batch_size=4)
    steps.append({"name": "AI 分析", "job": "ai_analysis", "result": ai_result})

    stopped = stopped_before("补齐日K")
    if stopped:
        return stopped
    job_manager.update_progress("system_startup", 38, "补齐日K", {"step": "kline_fill", "start_date": replay_start_date, "end_date": target_date})
    kline_result = job_manager.run_kline_fill(
        start_date=replay_start_date,
        end_date=target_date,
        max_codes=market_codes,
        force=False,
    )
    steps.append({"name": "日K补齐", "job": "kline_fill", "result": kline_result})

    stopped = stopped_before("同步龙虎榜")
    if stopped:
        return stopped
    job_manager.update_progress("system_startup", 54, "同步龙虎榜", {"step": "lhb_sync", "start_date": replay_start_date, "end_date": target_date})
    lhb_result = job_manager.run_lhb_sync(
        start_date=replay_start_date,
        end_date=target_date,
        max_stock_days=market_codes,
        force=False,
    )
    steps.append({"name": "龙虎榜同步", "job": "lhb_sync", "result": lhb_result})

    stopped = stopped_before("同步分时行情")
    if stopped:
        return stopped
    job_manager.update_progress("system_startup", 68, "同步分时行情", {"step": "market_sync"})
    market_result = job_manager.run_market_sync(
        date=target_date,
        source="auto",
        max_codes=market_codes,
        force=False,
        include_latest=True,
    )
    steps.append({"name": "行情同步", "job": "market_sync", "result": market_result})

    stopped = stopped_before("交易循环")
    if stopped:
        return stopped
    job_manager.update_progress("system_startup", 82, "从数据起点重建模拟交易", {"step": "trade_cycle", "start_date": replay_start_date})
    trade_result = job_manager.run_trade_cycle(date=target_date, notify=notify)
    steps.append({"name": "交易循环", "job": "trade_cycle", "result": trade_result})

    if run_strategy_replay:
        stopped = stopped_before("策略复盘")
        if stopped:
            return stopped
        job_manager.update_progress("system_startup", 94, "策略复盘", {"step": "strategy_replay", "start_date": replay_start_date})
        replay_result = job_manager.run_strategy_replay(
            start_date=replay_start_date,
            end_date=target_date,
            mode="intraday",
            batch_days=15,
            use_cursor=True,
        )
        steps.append({"name": "策略复盘", "job": "strategy_replay", "result": replay_result})
    else:
        job_manager.update_progress(
            "system_startup",
            94,
            "跳过策略复盘，训练和回测仅手动触发",
            {"step": "strategy_replay", "manual_only": True},
        )
        steps.append(
            {
                "name": "策略复盘",
                "job": "strategy_replay",
                "result": {
                    "status": "skipped",
                    "manual_only": True,
                    "message": "策略复盘、训练和回测默认不随系统启动执行，请在后台手动触发。",
                },
            }
        )

    failed = [step for step in steps if (step.get("result") or {}).get("status") not in {"ok", "running", "skipped"}]
    return {
        "status": "partial" if failed else "ok",
        "message": "系统启动流程完成" if not failed else "系统启动流程完成，但有步骤未成功，请查看运行日志",
        "start_date": replay_start_date,
        "date": target_date,
        "steps": steps,
    }


def _admin_system_startup_payload(
    date: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    news_hours: int,
    news_pages: int,
    ai_items: int,
    market_codes: int,
    notify: bool,
    background: bool,
    process: bool,
    run_strategy_replay: bool,
) -> Dict[str, Any]:
    target_date = str(end_date or date or quant_engine.latest_event_date() or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")).strip()
    replay_start_date = str(start_date or quant_engine.first_data_date() or "2026-03-01").strip()
    payload = {
        "date": target_date,
        "start_date": replay_start_date,
        "end_date": target_date,
        "news_hours": news_hours,
        "news_pages": news_pages,
        "ai_items": ai_items,
        "market_codes": market_codes,
        "notify": notify,
        "run_strategy_replay": run_strategy_replay,
    }
    def runner() -> Dict[str, Any]:
        return _run_system_startup_flow(
            target_date=target_date,
            replay_start_date=replay_start_date,
            news_hours=news_hours,
            news_pages=news_pages,
            ai_items=ai_items,
            market_codes=market_codes,
            notify=notify,
            run_strategy_replay=run_strategy_replay,
        )
    if process:
        return job_manager.run_job_process(
            "system_startup",
            payload=payload,
            message="系统启动流程已转入独立进程运行，请查看任务状态和右侧日志",
        )
    if background:
        return job_manager.run_job_background(
            "system_startup",
            runner,
            payload=payload,
            message="系统启动流程已转入后台运行，请查看任务状态和右侧日志",
        )
    return job_manager.run_job(
        "system_startup",
        runner,
        payload=payload,
    )


app.include_router(
    build_admin_job_runs_router(
        news_fetch_payload=_jobs_news_fetch_payload,
        market_sync_payload=_jobs_market_sync_payload,
        ai_analyze_payload=_jobs_ai_analyze_payload,
        trading_run_payload=_jobs_trading_run_payload,
        strategy_replay_payload=_jobs_strategy_replay_payload,
        frontend_payload_precompute_payload=_jobs_frontend_payload_precompute_payload,
        frontend_account_precompute_payload=_jobs_frontend_account_precompute_payload,
        system_startup_payload=_admin_system_startup_payload,
        news_fetch_process_default=_env_flag("QT_NEWS_FETCH_PROCESS_ENABLED", True),
        market_sync_process_default=_env_flag("QT_MARKET_SYNC_PROCESS_ENABLED", True),
        ai_analysis_process_default=_env_flag("QT_AI_ANALYSIS_PROCESS_ENABLED", True),
        trade_cycle_process_default=_env_flag("QT_TRADE_CYCLE_PROCESS_ENABLED", True),
        heavy_job_process_default=_env_flag("QT_HEAVY_JOB_PROCESS_ENABLED", True),
        frontend_payload_process_default=_env_flag("QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED", True),
        frontend_account_process_default=_env_flag("QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED", True),
        system_startup_process_default=_env_flag("QT_SYSTEM_STARTUP_PROCESS_ENABLED", True),
        system_startup_run_strategy_replay_default=_env_flag("QT_SYSTEM_STARTUP_RUN_STRATEGY_REPLAY", False),
    )
)


@app.post("/api/admin/backup")
def admin_backup():
    result = _create_data_backup()
    job_manager._append_log("info", "后台已请求数据备份", job="admin_backup", stage="finish", payload=result)
    return result


@app.get("/api/admin/data/export")
def admin_data_export(include_logs: bool = Query(default=False)):
    result = create_safe_data_package(BACKUP_DIR, DATA_DIR, include_logs=include_logs)
    job_manager._append_log("info", "后台已生成数据迁移包", job="admin_data_export", stage="finish", payload=result)
    package_file = Path(result["package_file"])
    return FileResponse(
        package_file,
        media_type="application/gzip",
        filename=package_file.name,
    )


@app.get("/api/admin/data/import/{job_id}")
def admin_data_import_status(job_id: str):
    job = _data_import_job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="data import job not found")
    return {"status": "ok", "job": job}


def _admin_database_tables_payload():
    cache_ttl = cache_env_int("QT_DATABASE_OVERVIEW_CACHE_TTL_SECONDS", 30, minimum=0, maximum=3600)
    cache_parts = {"version": APP_VERSION, "data_dir": str(DATA_DIR)}
    cached = _memory_cache_get("admin_database_overview", cache_parts, cache_ttl)
    if cached:
        return cached
    return _memory_cache_set("admin_database_overview", cache_parts, database_overview())


def _admin_database_table_payload(
    table_name: str,
    limit: int = 50,
    offset: int = 0,
):
    try:
        return database_table_rows(table_name, limit=limit, offset=offset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _admin_cache_status_payload():
    payload = runtime_cache_status()
    if isinstance(payload, dict):
        payload["memory_cache"] = {
            "row_count": len(_MEMORY_PAYLOAD_CACHE),
            "max_rows": _MEMORY_PAYLOAD_CACHE_MAX,
        }
    return payload


def _admin_cache_clear_payload(scope: str = "expired"):
    scope_text = str(scope or "expired").strip().lower()
    if scope_text in {"all", "memory", "payload", "expired"}:
        _memory_cache_clear()
    result = clear_runtime_cache(scope=scope)
    if isinstance(result, dict):
        result["memory_cache_cleared"] = scope_text in {"all", "memory", "payload", "expired"}
    return result


app.include_router(
    build_admin_data_cache_router(
        database_tables_payload=_admin_database_tables_payload,
        database_table_payload=_admin_database_table_payload,
        cache_status_payload=_admin_cache_status_payload,
        cache_clear_payload=_admin_cache_clear_payload,
    )
)


@app.post("/api/admin/data/import")
async def admin_data_import(request: Request, background_tasks: BackgroundTasks, backup: bool = Query(default=True)):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    max_bytes = int(max(1.0, _env_float("QT_DATA_UPLOAD_MAX_MB", 1024.0)) * 1024 * 1024)
    upload_fd, upload_name = tempfile.mkstemp(prefix="qt_data_upload_", suffix=".tar.gz", dir=str(BACKUP_DIR))
    os.close(upload_fd)
    upload_file: Optional[Path] = Path(upload_name)
    received = 0
    try:
        with upload_file.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_bytes:
                    raise HTTPException(status_code=413, detail="数据包超过服务器允许大小")
                handle.write(chunk)
        if received <= 0:
            raise HTTPException(status_code=400, detail="上传文件为空")
        validation = validate_data_package(upload_file)
        job_id = uuid.uuid4().hex[:16]
        _update_data_import_job(
            job_id,
            status="queued",
            stage="queued",
            progress=15,
            message="数据包已上传，等待后台合并",
            received_bytes=received,
            validation=validation,
            upload_file=str(upload_file),
            log_message="数据包已上传，等待后台合并",
        )
        background_tasks.add_task(_run_data_import_job, job_id, upload_file, received, backup)
        upload_file = None
        return {
            "status": "accepted",
            "job_id": job_id,
            "message": "数据包已上传，后台正在合并；请在进度浮窗查看状态",
            "received_bytes": received,
            "validation": validation,
        }
    except DataPackageError as exc:
        job_manager._append_log("error", "后台数据导入被拒绝", job="admin_data_import", stage="rejected", payload={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if upload_file is not None:
            try:
                upload_file.unlink(missing_ok=True)
            except Exception:
                pass


@app.post("/api/admin/data/clear_sample_state")
def admin_clear_sample_state():
    result = clear_sample_quant_state(DATA_DIR)
    if result.get("cleared"):
        _refresh_quant_caches()
    job_manager._append_log("warning", "后台已检查并清理样例持仓", job="admin_data_clear_sample", stage="finish", payload=result)
    return result


def _admin_access_logs_payload(
    limit: int = 220,
    offset: int = 0,
    username: Optional[str] = None,
    ip: Optional[str] = None,
    path: Optional[str] = None,
    status_code: Optional[int] = None,
):
    return access_logs(limit=limit, offset=offset, username=username, ip=ip, path=path, status_code=status_code)


def _admin_access_security_payload(limit: int = 120):
    return access_security(limit=limit)


def _admin_access_security_block_payload(payload: Dict[str, Any]):
    ip = str((payload or {}).get("ip") or "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="ip is required")
    reason = str((payload or {}).get("reason") or "后台手动封禁").strip()
    result = block_ip(ip, reason=reason, source="manual")
    result["security"] = access_security(limit=120)
    return result


def _admin_access_security_unblock_payload(payload: Dict[str, Any]):
    ip = str((payload or {}).get("ip") or "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="ip is required")
    result = unblock_ip(ip)
    result["security"] = access_security(limit=120)
    return result


def _admin_access_security_block_all_payload(payload: Dict[str, Any]):
    limit = int(safe_float((payload or {}).get("limit"), 500))
    summary = access_security(limit=max(1, min(limit, 500)))
    blocked = []
    skipped = []
    for item in summary.get("items", []):
        if not isinstance(item, dict) or item.get("blocked"):
            continue
        ip = str(item.get("ip") or "").strip()
        if not ip:
            continue
        reason = "、".join(item.get("reasons") or []) or "后台一键拉黑异常访问"
        result = block_ip(ip, reason=reason, source="manual_bulk")
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
        "security": access_security(limit=120),
    }


app.include_router(
    build_admin_access_router(
        access_logs_payload=_admin_access_logs_payload,
        access_security_payload=_admin_access_security_payload,
        block_payload=_admin_access_security_block_payload,
        unblock_payload=_admin_access_security_unblock_payload,
        block_all_payload=_admin_access_security_block_all_payload,
    )
)


def _admin_frontend_users_payload():
    return _admin_frontend_user_summary()


def _admin_create_frontend_user_payload(request: Request, payload: Dict[str, Any]):
    result = admin_create_frontend_user(payload, request)
    _memory_cache_clear()
    user = result.get("user") if isinstance(result.get("user"), dict) else {}
    if user:
        _record_user_follow_period(user.get("username"), user.get("profile"), source="admin_create_user", reason="admin_create_user", created_at=user.get("created_at"))
        result["user"] = _frontend_user_with_diagnostics(user)
    return result


def _admin_update_frontend_user_payload(username: str, payload: Dict[str, Any]):
    previous = {}
    try:
        previous_payload = frontend_user_profile(username)
        previous = previous_payload.get("profile") if isinstance(previous_payload.get("profile"), dict) else {}
    except Exception:
        previous = {}
    result = admin_update_frontend_user(username, payload)
    _frontend_account_cache_clear()
    _memory_cache_clear()
    user = result.get("user") if isinstance(result.get("user"), dict) else {}
    if user:
        _record_user_follow_period(
            user.get("username"),
            user.get("profile"),
            previous_profile=previous,
            source="admin_update_user",
            reason=_follow_period_reason(previous, user.get("profile")),
            created_at=user.get("created_at"),
        )
        result["user"] = _frontend_user_with_diagnostics(user)
    return result


def _admin_reset_frontend_user_password_payload(username: str, payload: Dict[str, Any]):
    result = admin_reset_frontend_user_password(username, payload)
    _memory_cache_clear()
    return result


def _admin_ban_frontend_user_payload(username: str, payload: Dict[str, Any]):
    result = admin_set_frontend_user_disabled(username, True, str((payload or {}).get("reason") or ""))
    _memory_cache_clear()
    return result


def _admin_unban_frontend_user_payload(username: str):
    result = admin_set_frontend_user_disabled(username, False)
    _memory_cache_clear()
    return result


def _admin_delete_frontend_user_payload(username: str):
    result = admin_delete_frontend_user(username)
    _memory_cache_clear()
    return result


app.include_router(
    build_admin_frontend_users_router(
        list_users_payload=_admin_frontend_users_payload,
        create_user_payload=_admin_create_frontend_user_payload,
        update_user_payload=_admin_update_frontend_user_payload,
        reset_password_payload=_admin_reset_frontend_user_password_payload,
        ban_user_payload=_admin_ban_frontend_user_payload,
        unban_user_payload=_admin_unban_frontend_user_payload,
        delete_user_payload=_admin_delete_frontend_user_payload,
    )
)


@app.post("/api/admin/restart")
def admin_restart(background_tasks: BackgroundTasks):
    if not _env_flag("QUANT_ALLOW_API_RESTART", default=False):
        result = {
            "status": "disabled",
            "message": "Set QUANT_ALLOW_API_RESTART=1 on the server to enable API-triggered restart.",
        }
        job_manager._append_log("warning", "后台重启被拦截：服务器未启用 API 重启", job="admin_restart", stage="blocked", payload=result)
        return result
    script = PROJECT_ROOT / "scripts" / "restart_server.sh"
    if not script.exists() or not shutil.which("bash"):
        result = {
            "status": "unavailable",
            "message": "restart script or bash runtime is not available on this host.",
        }
        job_manager._append_log("error", "后台重启不可用：缺少重启脚本或 bash", job="admin_restart", stage="unavailable", payload=result)
        return result
    background_tasks.add_task(_restart_service_after_response)
    result = {"status": "ok", "message": "restart scheduled"}
    job_manager._append_log("warning", "后台已安排服务重启", job="admin_restart", stage="scheduled", payload=result)
    return result


@app.get("/api/notifications/status")
def notifications_status():
    return trade_notifier.status()


@app.post("/api/notifications/test")
def notifications_test():
    return trade_notifier.send_test()


@app.get("/api/quant/timeline")
def quant_timeline(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    model_id: Optional[str] = Query(default=None),
    initial_cash: Optional[float] = Query(default=None, gt=0),
    max_positions: Optional[int] = Query(default=None, ge=1, le=20),
    hold_days: Optional[int] = Query(default=None, ge=1, le=20),
    top_n: Optional[int] = Query(default=None, ge=1, le=20),
    auto_fill: bool = Query(default=True),
    force: bool = Query(default=False),
    defer: bool = Query(default=_env_flag("QT_TIMELINE_DEFER_MISSES", True)),
    process: bool = Query(default=_env_flag("QT_TIMELINE_PROCESS_ENABLED", True)),
    manual: bool = Query(default=False),
):
    return _quant_timeline_cached_or_deferred(
        model_id=model_id,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        intraday=False,
        auto_fill=auto_fill,
        force=force,
        defer=defer,
        process=process,
        manual=manual,
    )


@app.get("/api/quant/intraday_timeline")
def quant_intraday_timeline(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    model_id: Optional[str] = Query(default=None),
    initial_cash: Optional[float] = Query(default=None, gt=0),
    max_positions: Optional[int] = Query(default=None, ge=1, le=20),
    hold_days: Optional[int] = Query(default=None, ge=1, le=20),
    top_n: Optional[int] = Query(default=None, ge=1, le=20),
    use_daily_fallback: bool = Query(default=True),
    auto_fill: bool = Query(default=True),
    force: bool = Query(default=False),
    defer: bool = Query(default=_env_flag("QT_TIMELINE_DEFER_MISSES", True)),
    process: bool = Query(default=_env_flag("QT_TIMELINE_PROCESS_ENABLED", True)),
    manual: bool = Query(default=False),
):
    return _quant_timeline_cached_or_deferred(
        model_id=model_id,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        intraday=True,
        use_daily_fallback=use_daily_fallback,
        auto_fill=auto_fill,
        force=force,
        defer=defer,
        process=process,
        manual=manual,
    )


def _data_coverage_top_n(top_n: int) -> int:
    return max(1, min(int(top_n or 80), 300))


def _data_coverage_cache_parts(effective_as_of: str, top_n: int) -> Dict[str, Any]:
    return {"as_of": effective_as_of, "top_n": _data_coverage_top_n(top_n), "version": APP_VERSION}


def _data_coverage_cache_ttl() -> int:
    return cache_env_int("QT_DATA_COVERAGE_CACHE_TTL_SECONDS", 60, minimum=0, maximum=3600)


def _compact_data_coverage_result(payload: Dict[str, Any], top_n: int) -> Dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    daily = payload.get("daily_coverage") if isinstance(payload.get("daily_coverage"), dict) else {}
    minute = payload.get("minute_coverage") if isinstance(payload.get("minute_coverage"), dict) else {}
    lhb = payload.get("lhb") if isinstance(payload.get("lhb"), dict) else {}
    return {
        "status": payload.get("status") or "ok",
        "job": "data_coverage",
        "as_of": payload.get("as_of") or "",
        "top_n": _data_coverage_top_n(top_n),
        "target_count": int(safe_float(summary.get("target_count"), 0)),
        "daily_ratio": safe_float(daily.get("ratio"), 0),
        "minute_ratio": safe_float(minute.get("ratio"), 0),
        "lhb_rows": int(safe_float(lhb.get("rows"), safe_float(summary.get("lhb_rows"), 0))),
        "latest_lhb_date": lhb.get("latest_date") or summary.get("latest_lhb_date") or "",
        "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _compute_data_coverage_cached(effective_as_of: str, top_n: int) -> Dict[str, Any]:
    clean_top_n = _data_coverage_top_n(top_n)
    cache_parts = _data_coverage_cache_parts(effective_as_of, clean_top_n)
    payload = data_coverage(as_of=effective_as_of, top_n=clean_top_n)
    if isinstance(payload, dict):
        payload["data_coverage_cache"] = "refresh"
        payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        save_payload_cache("data_coverage", cache_parts, payload, _data_coverage_cache_ttl())
        return _memory_cache_set("data_coverage", cache_parts, payload)
    return payload


def _queue_data_coverage_precompute(effective_as_of: str, top_n: int, process: bool = True) -> Dict[str, Any]:
    clean_top_n = _data_coverage_top_n(top_n)
    payload = {"as_of": effective_as_of, "top_n": clean_top_n}
    if process:
        return job_manager.run_job_process(
            "data_coverage",
            payload=payload,
            message="数据覆盖率统计已转入独立进程运行",
        )

    def execute() -> Dict[str, Any]:
        result = _compute_data_coverage_cached(effective_as_of, clean_top_n)
        return _compact_data_coverage_result(result if isinstance(result, dict) else {}, clean_top_n)

    return job_manager.run_job_background(
        "data_coverage",
        execute,
        payload=payload,
        message="数据覆盖率统计已转入后台运行",
    )


def _pending_data_coverage(effective_as_of: str, top_n: int, job_result: Dict[str, Any]) -> Dict[str, Any]:
    clean_top_n = _data_coverage_top_n(top_n)
    status, cache_state, message = _deferred_job_response_state(
        job_result,
        "数据覆盖率统计正在后台生成，请稍后刷新。",
    )
    return {
        "status": status,
        "as_of": effective_as_of,
        "top_n": clean_top_n,
        "data_coverage_cache": cache_state,
        "message": message,
        "summary": {"target_count": 0},
        "daily_coverage": {"covered": 0, "missing": 0, "ratio": 0},
        "minute_coverage": {"covered": 0, "missing": 0, "ratio": 0},
        "minute_cache_dates": [],
        "recent_event_dates": {},
        "news": {},
        "ai": {},
        "biying": {},
        "lhb": {},
        "targets": [],
        "job_result": job_result,
    }


def _data_coverage_payload(
    as_of: Optional[str] = None,
    top_n: int = 80,
    force: bool = False,
    defer: bool = True,
    process: Optional[bool] = None,
) -> Dict[str, Any]:
    effective_as_of = _frontend_account_as_of(as_of)
    clean_top_n = _data_coverage_top_n(top_n)
    cache_parts = _data_coverage_cache_parts(effective_as_of, clean_top_n)
    ttl = _data_coverage_cache_ttl()
    cached = None if force else (
        load_payload_cache("data_coverage", cache_parts, ttl)
        or _memory_cache_get("data_coverage", cache_parts, ttl)
    )
    if cached:
        cached["data_coverage_cache"] = "hit"
        return cached
    if defer and not force:
        use_process = _env_flag("QT_DATA_COVERAGE_PROCESS_ENABLED", True) if process is None else bool(process)
        job_result = _queue_data_coverage_precompute(effective_as_of, clean_top_n, process=use_process)
        return _pending_data_coverage(effective_as_of, clean_top_n, job_result)
    return _compute_data_coverage_cached(effective_as_of, clean_top_n)


@app.get("/api/data/biying/status")
def biying_status():
    return biying_minute_sync.status()


@app.get("/api/data/coverage")
def quant_data_coverage(
    as_of: Optional[str] = Query(default=None),
    top_n: int = Query(default=80, ge=1, le=300),
    force: bool = Query(default=False),
    defer: bool = Query(default=_env_flag("QT_DATA_COVERAGE_DEFER_MISSES", True)),
    process: bool = Query(default=_env_flag("QT_DATA_COVERAGE_PROCESS_ENABLED", True)),
):
    return _data_coverage_payload(as_of=as_of, top_n=top_n, force=force, defer=defer, process=process)


@app.post("/api/data/kline/fill")
def data_kline_fill(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    max_codes: int = Query(default=300, ge=1, le=5000),
    force: bool = Query(default=False),
    background: bool = Query(default=True),
    process: bool = Query(default=_env_flag("QT_KLINE_FILL_PROCESS_ENABLED", True)),
):
    return job_manager.run_kline_fill(
        start_date=start_date,
        end_date=end_date,
        max_codes=max_codes,
        force=force,
        background=background,
        process=process,
    )


@app.get("/api/data/lhb/status")
def data_lhb_status():
    return lhb_status()


@app.post("/api/data/lhb/sync")
def data_lhb_sync(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    max_stock_days: int = Query(default=300, ge=1, le=2000),
    force: bool = Query(default=False),
    background: bool = Query(default=True),
    process: bool = Query(default=_env_flag("QT_LHB_SYNC_PROCESS_ENABLED", True)),
):
    return job_manager.run_lhb_sync(
        start_date=start_date,
        end_date=end_date,
        max_stock_days=max_stock_days,
        force=force,
        refresh_events=True,
        background=background,
        process=process,
    )


@app.post("/api/data/biying/sync_intraday")
def biying_sync_intraday(
    date: Optional[str] = Query(default=None),
    source: str = Query(default="events"),
    max_codes: int = Query(default=200, ge=1, le=5000),
    codes: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
    include_latest: bool = Query(default=True),
    background: bool = Query(default=True),
    process: bool = Query(default=_env_flag("QT_MARKET_SYNC_PROCESS_ENABLED", True)),
):
    return job_manager.run_market_sync(
        date=date,
        source=source,
        max_codes=max_codes,
        codes=codes,
        force=force,
        include_latest=include_latest,
        background=background,
        process=process,
    )


@app.get("/api/ai/usage")
def quant_ai_usage():
    cache_ttl = cache_env_int("QT_AI_STATUS_CACHE_TTL_SECONDS", 60, minimum=0, maximum=3600)
    cache_parts = {"version": APP_VERSION}
    cached = _memory_cache_get("ai_usage", cache_parts, cache_ttl)
    if cached:
        return cached
    return _memory_cache_set("ai_usage", cache_parts, ai_usage_summary())


@app.get("/api/ai/records")
def quant_ai_records(
    limit: int = Query(default=100, ge=1, le=500),
    code: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    cache_ttl = cache_env_int("QT_AI_STATUS_CACHE_TTL_SECONDS", 60, minimum=0, maximum=3600)
    cache_parts = {"limit": int(limit or 100), "code": code or "", "source": source or "", "version": APP_VERSION}
    cached = _memory_cache_get("ai_records", cache_parts, cache_ttl)
    if cached:
        return cached
    return _memory_cache_set("ai_records", cache_parts, ai_records_feed(limit=limit, code=code, source=source))


@app.get("/api/ai/failures")
def quant_ai_failures(limit: int = Query(default=100, ge=1, le=500)):
    cache_ttl = cache_env_int("QT_AI_STATUS_CACHE_TTL_SECONDS", 60, minimum=0, maximum=3600)
    cache_parts = {"limit": int(limit or 100), "version": APP_VERSION}
    cached = _memory_cache_get("ai_failures", cache_parts, cache_ttl)
    if cached:
        return cached
    return _memory_cache_set("ai_failures", cache_parts, ai_failures(limit=limit))


def _quant_backtest_cache_ttl() -> int:
    return cache_env_int("QT_BACKTEST_CACHE_TTL_SECONDS", 600, minimum=0, maximum=86400)


def _quant_backtest_cache_parts(
    *,
    as_of: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: int,
    top_n: int,
    auto_fill: bool,
) -> Dict[str, Any]:
    params = quant_engine.strategy_params()
    params_hash = hashlib.sha256(
        json.dumps(params, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8", errors="ignore")
    ).hexdigest()
    return {
        "as_of": str(as_of or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "initial_cash": round(safe_float(initial_cash, 0), 2) if initial_cash is not None else "",
        "max_positions": int(max_positions) if max_positions is not None else "",
        "hold_days": max(1, min(int(hold_days or 3), 60)),
        "top_n": max(1, min(int(top_n or 5), 50)),
        "auto_fill": bool(auto_fill),
        "strategy_source": quant_engine.strategy_source(),
        "params_hash": params_hash,
        "version": APP_VERSION,
    }


def _compact_quant_backtest_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": payload.get("status") or "ok",
        "job": "quant_backtest",
        "as_of": payload.get("as_of") or "",
        "start_date": payload.get("start_date") or "",
        "end_date": payload.get("end_date") or "",
        "return_pct": payload.get("return_pct", 0),
        "trade_count": payload.get("timeline_trade_count", payload.get("trades", 0)),
        "closed_trades": payload.get("closed_trades", 0),
        "generated_at": payload.get("generated_at") or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
    }


def _compute_quant_backtest_cached(
    *,
    as_of: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    initial_cash: Optional[float] = None,
    max_positions: Optional[int] = None,
    hold_days: int = 3,
    top_n: int = 5,
    auto_fill: bool = True,
) -> Dict[str, Any]:
    clean_hold_days = max(1, min(int(hold_days or 3), 60))
    clean_top_n = max(1, min(int(top_n or 5), 50))
    payload = quant_engine.backtest(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=clean_hold_days,
        top_n=clean_top_n,
        auto_fill=auto_fill,
    )
    if isinstance(payload, dict):
        payload["source"] = "quant_backtest"
        payload["backtest_cache"] = "refresh"
        payload["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        save_payload_cache(
            "quant_backtest",
            _quant_backtest_cache_parts(
                as_of=as_of,
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                max_positions=max_positions,
                hold_days=clean_hold_days,
                top_n=clean_top_n,
                auto_fill=auto_fill,
            ),
            payload,
            _quant_backtest_cache_ttl(),
        )
    return payload


def _queue_quant_backtest_precompute(
    *,
    as_of: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: int,
    top_n: int,
    auto_fill: bool,
    process: bool = True,
) -> Dict[str, Any]:
    payload = {
        "as_of": str(as_of or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "initial_cash": initial_cash,
        "max_positions": max_positions,
        "hold_days": max(1, min(int(hold_days or 3), 60)),
        "top_n": max(1, min(int(top_n or 5), 50)),
        "auto_fill": bool(auto_fill),
    }
    if process:
        return job_manager.run_job_process(
            "quant_backtest",
            payload=payload,
            message="通用回测已转入独立进程运行",
        )

    def execute() -> Dict[str, Any]:
        result = _compute_quant_backtest_cached(**payload)
        return _compact_quant_backtest_result(result if isinstance(result, dict) else {})

    return job_manager.run_job_background(
        "quant_backtest",
        execute,
        payload=payload,
        message="通用回测已转入后台运行",
    )


def _pending_quant_backtest(
    *,
    as_of: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: int,
    top_n: int,
    auto_fill: bool,
    job_result: Dict[str, Any],
) -> Dict[str, Any]:
    status, cache_state, message = _deferred_job_response_state(
        job_result,
        "通用回测正在后台生成，请稍后刷新。",
    )
    return {
        "status": status,
        "source": "quant_backtest",
        "backtest_cache": cache_state,
        "message": message,
        "as_of": str(as_of or ""),
        "start_date": str(start_date or ""),
        "end_date": str(end_date or ""),
        "initial_cash": initial_cash,
        "max_positions": max_positions,
        "hold_days": max(1, min(int(hold_days or 3), 60)),
        "top_n": max(1, min(int(top_n or 5), 50)),
        "auto_fill": bool(auto_fill),
        "recent_trades": [],
        "trade_records": [],
        "account": {},
        "positions": [],
        "delivery_records": [],
        "daily_settlements": [],
        "days": [],
        "equity_curve": [],
        "job_result": job_result,
    }


def _quant_backtest_cached_or_deferred(
    *,
    as_of: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    initial_cash: Optional[float],
    max_positions: Optional[int],
    hold_days: int,
    top_n: int,
    auto_fill: bool,
    force: bool = False,
    defer: bool = True,
    process: bool = True,
    manual: bool = False,
) -> Dict[str, Any]:
    clean_hold_days = max(1, min(int(hold_days or 3), 60))
    clean_top_n = max(1, min(int(top_n or 5), 50))
    cache_parts = _quant_backtest_cache_parts(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=clean_hold_days,
        top_n=clean_top_n,
        auto_fill=auto_fill,
    )
    cached = None if force else load_payload_cache("quant_backtest", cache_parts, _quant_backtest_cache_ttl())
    if cached:
        cached["backtest_cache"] = "hit"
        return cached
    if _env_flag("QT_BACKTEST_REQUIRE_MANUAL_TRIGGER", True) and not manual:
        return _manual_required_heavy_job(
            "quant_backtest",
            "通用回测需要显式手动触发；普通刷新只读取短缓存，不自动启动重计算。",
            {
                "source": "quant_backtest",
                "backtest_cache": "manual_required",
                "as_of": str(as_of or ""),
                "start_date": str(start_date or ""),
                "end_date": str(end_date or ""),
                "initial_cash": initial_cash,
                "max_positions": max_positions,
                "hold_days": clean_hold_days,
                "top_n": clean_top_n,
                "auto_fill": bool(auto_fill),
                "recent_trades": [],
                "trade_records": [],
                "account": {},
                "positions": [],
                "delivery_records": [],
                "daily_settlements": [],
                "days": [],
                "equity_curve": [],
            },
        )
    if defer:
        job_result = _queue_quant_backtest_precompute(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=clean_hold_days,
            top_n=clean_top_n,
            auto_fill=auto_fill,
            process=process,
        )
        return _pending_quant_backtest(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=clean_hold_days,
            top_n=clean_top_n,
            auto_fill=auto_fill,
            job_result=job_result,
        )
    return _compute_quant_backtest_cached(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=clean_hold_days,
        top_n=clean_top_n,
        auto_fill=auto_fill,
    )


@app.get("/api/quant/backtest")
@app.post("/api/quant/backtest")
def quant_backtest(
    as_of: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    initial_cash: Optional[float] = Query(default=None, gt=0),
    max_positions: Optional[int] = Query(default=None, ge=1, le=20),
    hold_days: int = Query(default=3, ge=1, le=20),
    top_n: int = Query(default=5, ge=1, le=20),
    auto_fill: bool = Query(default=True),
    force: bool = Query(default=False),
    defer: bool = Query(default=_env_flag("QT_BACKTEST_DEFER_MISSES", True)),
    process: bool = Query(default=_env_flag("QT_BACKTEST_PROCESS_ENABLED", True)),
    manual: bool = Query(default=False),
):
    return _quant_backtest_cached_or_deferred(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        auto_fill=auto_fill,
        force=force,
        defer=defer,
        process=process,
        manual=manual,
    )


@app.get("/", include_in_schema=False)
def index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"status": "ok", "message": "frontend/index.html not found"})


@app.get("/index.html", include_in_schema=False)
def index_html():
    return index()


def _admin_index_response():
    admin_file = FRONTEND_DIR / "admin" / "index.html"
    if admin_file.exists():
        return FileResponse(admin_file)
    return JSONResponse({"status": "ok", "message": "frontend/admin/index.html not found"})


@app.get("/{full_path:path}", include_in_schema=False)
def configured_static_entry(full_path: str):
    request_path = "/" + str(full_path or "").strip("/")
    if request_path in {"/api", "/static"} or request_path.startswith(("/api/", "/static/")):
        raise HTTPException(status_code=404, detail="Not Found")
    admin_entry = ensure_admin_entry_path().rstrip("/")
    if request_path in {admin_entry, f"{admin_entry}/index.html"}:
        return _admin_index_response()
    raise HTTPException(status_code=404, detail="Not Found")
