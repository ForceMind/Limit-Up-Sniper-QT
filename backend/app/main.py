from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.quant.access_audit import access_logs, record_access
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
from app.quant.lhb_sync import lhb_status
from app.quant.jobs import job_manager
from app.quant.monitoring import ai_failures, ai_records_feed, ai_usage_summary, data_coverage
from app.quant.news_fetcher import news_fetcher
from app.quant.news_repository import latest_news_time as latest_sqlite_news_time
from app.quant.news_repository import lightweight_news_feed
from app.quant.notifier import trade_notifier
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


def _latest_news_time() -> str:
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
    if _env_flag("QUANT_SCHEDULER_ENABLED", default=True):
        job_manager.start()
    else:
        job_manager.mark_scheduler_disabled("QUANT_SCHEDULER_ENABLED=0")


@app.on_event("shutdown")
async def shutdown_jobs():
    await job_manager.stop()


@app.get("/api/version")
def api_version():
    return app_version_payload()


@app.get("/api/auth/status")
def api_auth_status():
    return auth_status()


@app.get("/api/debug/status")
def api_debug_status(request: Request):
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


@app.get("/api/debug/routes")
def api_debug_routes():
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


@app.post("/api/auth/setup")
def api_auth_setup(payload: Dict[str, Any] = Body(default_factory=dict)):
    return setup_auth(payload)


@app.post("/api/auth/login")
def api_auth_login(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
    return login(payload, request)


@app.post("/api/auth/register")
def api_auth_register(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
    return register_frontend_user(payload, request)


def _request_username(request: Request) -> str:
    payload = getattr(request.state, "auth_payload", None)
    if not isinstance(payload, dict):
        payload = require_request_scope(request, "frontend")
    return str(payload.get("sub") or "").strip()


@app.get("/api/front/profile")
def api_front_profile(request: Request):
    return frontend_user_profile(_request_username(request))


@app.post("/api/front/profile")
def api_update_front_profile(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
    username = _request_username(request)
    updates = dict(payload) if isinstance(payload, dict) else {}
    if updates.get("auto_recommend"):
        cash = max(10_000.0, min(10_000_000.0, safe_float(updates.get("simulated_cash"), 10_000.0)))
        models_payload = _frontend_strategy_models_payload(include_catalog=True)
        updates["strategy_model_id"] = recommended_strategy_id(cash, _strategy_catalog_items(models_payload))
    result = update_frontend_user_profile(username, updates)
    _frontend_account_cache_clear()
    context = _frontend_profile_context(request, include_catalog=True)
    return {
        **result,
        "profile": context["profile"],
        "followed_model": context["followed_model"],
        "strategy_models": context["models_payload"],
        "strategy_params": context["strategy_params"],
        "account_cache_cleared": True,
    }


@app.get("/api/config/status")
def api_config_status():
    return runtime_config_status()


@app.get("/api/config/runtime")
def api_config_runtime():
    return runtime_config_form()


@app.post("/api/config/runtime")
def api_update_config_runtime(payload: Dict[str, Any] = Body(default_factory=dict)):
    result = update_runtime_config(payload)
    job_manager._append_log("warning", "后台运行配置已保存", job="admin_config", stage="saved")
    return result


@app.get("/api/status")
def status():
    now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
    latest_news_time = _latest_news_time()
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
        "ai_model": DEFAULT_AI_MODEL,
        "jobs": job_manager.status(),
    }


def _light_status_payload(as_of: Optional[str] = None, jobs_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
    latest_news_time = _latest_news_time()
    data_date = str(as_of or "").strip() or (latest_news_time[:10] if latest_news_time else "")
    jobs = jobs_payload if isinstance(jobs_payload, dict) else {}
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
        "ai_model": DEFAULT_AI_MODEL,
        "jobs": jobs,
    }


def _frontend_light_jobs(jobs_payload: Dict[str, Any]) -> Dict[str, Any]:
    jobs = jobs_payload if isinstance(jobs_payload, dict) else {}
    return {
        "scheduler": jobs.get("scheduler", {}),
        "running": jobs.get("running", {}),
        "paused_jobs": jobs.get("paused_jobs", {}),
    }


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


def _strategy_catalog_items(models_payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    for item in models_payload.get("capital_presets") if isinstance(models_payload.get("capital_presets"), list) else []:
        if isinstance(item, dict):
            items.append(item)
    active = models_payload.get("active") if isinstance(models_payload.get("active"), dict) else {}
    if active:
        items.append({**active, "id": str(active.get("id") or "active")})
    for item in models_payload.get("items") if isinstance(models_payload.get("items"), list) else []:
        if isinstance(item, dict):
            items.append(item)
    seen = set()
    unique = []
    for item in items:
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        unique.append(item)
    return unique


def _active_strategy_model() -> Dict[str, Any]:
    return {
        "id": "active",
        "name": "系统运行策略（当前参数）",
        "source": "runtime",
        "reusable": True,
        "description": "后台任务正在使用的全局参数，会驱动系统运行账户；策略库模型是训练/回测后保存的参数组合。",
        "params": quant_engine.strategy_params(),
        "strategy_source": quant_engine.strategy_source(),
    }


def _frontend_strategy_models_payload(include_catalog: bool = True) -> Dict[str, Any]:
    if include_catalog:
        payload = strategy_evolution.models(limit=40, include_records=False)
    else:
        payload = {"status": "ok", "active": _active_strategy_model(), "items": [], "count": 0}
    if not isinstance(payload, dict):
        payload = {"status": "ok", "active": _active_strategy_model(), "items": [], "count": 0}
    base_params = quant_engine.strategy_params()
    presets = capital_presets(base_params)
    payload["active"] = {**_active_strategy_model(), **(payload.get("active") if isinstance(payload.get("active"), dict) else {})}
    payload["active"]["name"] = "系统运行策略（当前参数）"
    payload["capital_presets"] = presets
    payload["capital_bands"] = CAPITAL_BANDS
    payload["count"] = int(safe_float(payload.get("count"), 0)) + len(presets)
    return payload


def _frontend_profile_context(request: Request, include_catalog: bool = True) -> Dict[str, Any]:
    username = _request_username(request)
    profile_payload = frontend_user_profile(username)
    profile = profile_payload.get("profile") if isinstance(profile_payload.get("profile"), dict) else {}
    simulated_cash = max(10_000.0, min(10_000_000.0, safe_float(profile.get("simulated_cash"), 10_000.0)))
    original_selected_id = str(profile.get("strategy_model_id") or "").strip()
    selected_id = original_selected_id
    models_payload = _frontend_strategy_models_payload(include_catalog=include_catalog or selected_id not in {"", "active"})
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
        "profile": profile,
        "models_payload": models_payload,
        "followed_model": selected or {},
        "strategy_params": params,
    }


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


def _frontend_account_as_of(as_of: Optional[str]) -> Optional[str]:
    latest = str(quant_engine.latest_event_date() or "").strip()
    requested = str(as_of or "").strip()
    if requested and latest and requested > latest:
        return latest
    return requested or latest or None


def _frontend_replay_start_date(end_date: Optional[str]) -> Optional[str]:
    first = str(quant_engine.first_data_date() or "").strip()
    if not end_date:
        return first or None
    try:
        start = datetime.strptime(end_date[:10], "%Y-%m-%d") - timedelta(days=_FRONTEND_ACCOUNT_REPLAY_DAYS)
        start_text = start.strftime("%Y-%m-%d")
        return max(first, start_text) if first else start_text
    except Exception:
        return first or None


def _frontend_strategy_account(context: Dict[str, Any], as_of: Optional[str], limit: int, force: bool = False) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    followed_id = str(profile.get("strategy_model_id") or "active").strip() or "active"
    params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
    target_cash = safe_float(params.get("account_initial_cash"), safe_float(profile.get("simulated_cash"), 10_000))
    effective_as_of = _frontend_account_as_of(as_of)
    replay_start_date = _frontend_replay_start_date(effective_as_of)

    if followed_id != "active":
        model = _frontend_full_model(followed_id)
        raw_records = model.get("trade_records") if isinstance(model.get("trade_records"), list) else []
        if raw_records:
            trade_records = _scale_model_trades_for_cash(model, target_cash)
            account = quant_engine.account_from_trades(
                trade_records,
                initial_cash=target_cash,
                as_of=effective_as_of,
                limit=limit,
            )
            account["strategy_account_source"] = "model_records"
            return account

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
                limit=limit,
            )
        account["strategy_account_source"] = "strategy_replay"
        account["strategy_account_cache"] = "miss"
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
        return _frontend_account_cache_set(cache_key, account)

    with quant_engine.temporary_strategy_params(params):
        account = quant_engine.trading_account(as_of=effective_as_of, limit=limit)
    account["strategy_account_source"] = "runtime_state"
    return account


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
    next_account["follow_model_name"] = str((context.get("followed_model") or {}).get("name") or "系统运行策略（当前参数）")
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
    return next_payload


def _find_strategy_model(model_id: str) -> Dict[str, Any]:
    model_id = str(model_id or "active").strip() or "active"
    model = strategy_evolution.model(model_id, include_records=True)
    if model:
        return model
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


@app.get("/api/front/public_snapshot")
def frontend_public_snapshot(
    as_of: Optional[str] = Query(default=None),
    mobile: bool = Query(default=False),
    light: bool = Query(default=True),
):
    news_limit = 12 if mobile or light else 80
    jobs_payload = job_manager.status()
    light_jobs = _frontend_light_jobs(jobs_payload)
    news_payload = _safe_news_feed(as_of=as_of, limit=news_limit, fallback_latest=True)
    return {
        "status": "ok",
        "status_payload": _light_status_payload(as_of=as_of, jobs_payload=light_jobs),
        "jobs": light_jobs,
        "news": news_payload,
        "market_sentiment": _market_sentiment(news_payload),
    }


@app.get("/api/front/snapshot")
def frontend_snapshot(
    request: Request,
    as_of: Optional[str] = Query(default=None),
    mobile: bool = Query(default=False),
    light: bool = Query(default=True),
):
    news_limit = 12 if mobile or light else 80
    top_n = 12 if mobile else 30
    jobs_payload = job_manager.status()
    visible_jobs = _frontend_light_jobs(jobs_payload) if light else jobs_payload
    news_payload = _safe_news_feed(as_of=as_of, limit=news_limit, fallback_latest=True)
    context = _frontend_profile_context(request, include_catalog=True)
    trading_account: Dict[str, Any] = {}
    recommendations: Dict[str, Any] = {}
    daily_plan: Dict[str, Any] = {}
    if not light:
        with quant_engine.temporary_strategy_params(context["strategy_params"]):
            recommendations = quant_engine.recommendations(as_of=as_of, lookback_days=2, top_n=top_n)
            daily_plan = quant_engine.daily_plan(as_of=as_of, limit_days=120)
        trading_account = _frontend_strategy_account(context, as_of, limit=500)
        trading_account = _frontend_trading_account(trading_account, context)
    payload = {
        "status": "ok",
        "status_payload": _light_status_payload(as_of=as_of, jobs_payload=visible_jobs),
        "jobs": visible_jobs,
        "logs": job_manager.logs(limit=12),
        "frontend_profile": context["profile"],
        "followed_model": context["followed_model"],
        "news": news_payload,
        "strategy_models": context["models_payload"],
        "market_sentiment": _market_sentiment(news_payload),
    }
    if trading_account:
        payload["trading_account"] = trading_account
    if recommendations:
        payload["recommendations"] = recommendations
    if daily_plan:
        payload["daily_plan"] = daily_plan
    return payload


@app.get("/api/front/trading_account")
def frontend_trading_account(
    request: Request,
    as_of: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    force: bool = Query(default=False),
):
    context = _frontend_profile_context(request, include_catalog=False)
    account = _frontend_strategy_account(context, as_of, limit=limit, force=force)
    return _frontend_trading_account(account, context)


@app.get("/api/front/recommendations")
def frontend_recommendations(
    request: Request,
    as_of: Optional[str] = Query(default=None),
    lookback_days: int = Query(default=2, ge=1, le=20),
    top_n: int = Query(default=30, ge=1, le=100),
):
    context = _frontend_profile_context(request, include_catalog=False)
    with quant_engine.temporary_strategy_params(context["strategy_params"]):
        payload = quant_engine.recommendations(as_of=as_of, lookback_days=lookback_days, top_n=top_n)
    return _affordable_payload(payload, context, as_of)


@app.get("/api/front/daily_plan")
def frontend_daily_plan(
    request: Request,
    as_of: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    limit_days: int = Query(default=120, ge=1, le=500),
):
    context = _frontend_profile_context(request, include_catalog=False)
    effective_as_of = _frontend_account_as_of(as_of)
    effective_start = start_date or _frontend_replay_start_date(effective_as_of)
    with quant_engine.temporary_strategy_params(context["strategy_params"]):
        payload = quant_engine.daily_plan(as_of=effective_as_of, start_date=effective_start, limit_days=limit_days)
    return _affordable_payload(payload, context, effective_as_of)


@app.get("/api/admin/snapshot")
def admin_snapshot(as_of: Optional[str] = Query(default=None), light: bool = Query(default=True)):
    if light:
        jobs_payload = job_manager.status()
        dashboard = {
            "status": "ok",
            "as_of": as_of,
            "strategy_params": quant_engine.strategy_params(),
            "strategy_source": quant_engine.strategy_source(),
            "timeline": {},
        }
        return {
            "status": "ok",
            "status_payload": _light_status_payload(as_of=as_of, jobs_payload=jobs_payload),
            "jobs": jobs_payload,
            "biying": biying_minute_sync.status(),
            "lhb": lhb_status(),
            "notification_status": trade_notifier.status(),
            "evolution_status": strategy_evolution.status(),
            "strategy_models": _frontend_strategy_models_payload(include_catalog=True),
            "frontend_users": frontend_user_summary(),
            "dashboard": dashboard,
        }
    return {
        "status": "ok",
        "status_payload": status(),
        "jobs": job_manager.status(),
        "biying": biying_minute_sync.status(),
        "lhb": lhb_status(),
        "ai_usage": ai_usage_summary(),
        "notification_status": trade_notifier.status(),
        "evolution_status": strategy_evolution.status(),
        "strategy_models": _frontend_strategy_models_payload(include_catalog=True),
        "access_logs": access_logs(limit=120),
        "frontend_users": frontend_user_summary(),
        "dashboard": quant_engine.dashboard(as_of=as_of, include_heavy=False),
        "trading_account": quant_engine.trading_account(as_of=as_of, limit=1000),
        "news": quant_engine.news_feed(as_of=as_of, limit=120, fallback_latest=True),
        "coverage": data_coverage(as_of=as_of, top_n=100),
        "ai_failures": ai_failures(limit=40),
        "ai_records": ai_records_feed(limit=80),
    }


@app.get("/api/admin/trading_account")
def admin_trading_account(
    as_of: Optional[str] = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=2000),
):
    payload = quant_engine.trading_account(as_of=as_of, limit=limit)
    payload["strategy_account_source"] = "runtime_state"
    payload["strategy_scope"] = "system_runtime"
    payload["strategy_name"] = "系统运行账户（系统运行策略）"
    return payload


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
            jobs_payload = job_manager.status()
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


@app.get("/api/quant/dashboard")
def quant_dashboard(as_of: Optional[str] = Query(default=None), light: bool = Query(default=False)):
    return quant_engine.dashboard(as_of=as_of, include_heavy=not light)


@app.get("/api/quant/recommendations")
def quant_recommendations(
    as_of: Optional[str] = Query(default=None),
    lookback_days: int = Query(default=2, ge=1, le=20),
    top_n: int = Query(default=30, ge=1, le=100),
):
    return quant_engine.recommendations(as_of=as_of, lookback_days=lookback_days, top_n=top_n)


@app.get("/api/quant/daily_plan")
def quant_daily_plan(
    as_of: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    limit_days: int = Query(default=80, ge=1, le=500),
):
    return quant_engine.daily_plan(as_of=as_of, start_date=start_date, limit_days=limit_days)


@app.get("/api/quant/strategy_params")
def quant_strategy_params():
    return {
        "status": "ok",
        "strategy_params": quant_engine.strategy_params(),
        "strategy_source": quant_engine.strategy_source(),
        "model_weights": quant_engine.model_weights(),
    }


@app.post("/api/quant/strategy_params")
def quant_update_strategy_params(payload: Dict[str, Any] = Body(default_factory=dict)):
    return quant_engine.update_strategy_params(payload)


@app.post("/api/quant/strategy_params/reset")
def quant_reset_strategy_params():
    return quant_engine.reset_strategy_params()


@app.post("/api/quant/fit_strategy")
def quant_fit_strategy(
    as_of: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    apply_best: bool = Query(default=True),
):
    return quant_engine.fit_strategy(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        apply_best=apply_best,
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
):
    return _model_backtest_payload(
        model=_find_strategy_model(model_id),
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        limit=limit,
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
            "description": "来自资金档策略应用。" if source_type == "capital_preset" else "来自策略库模型应用。",
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
):
    if background:
        current = strategy_evolution.status()
        if current.get("status") == "running":
            return current

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


@app.get("/api/quant/events")
def quant_events(as_of: Optional[str] = Query(default=None), limit: int = Query(default=200, ge=1, le=1000)):
    events = quant_engine.events()
    if as_of:
        events = [event for event in events if event.date <= as_of]
    return {"items": [event.compact() for event in events[:limit]], "count": len(events)}


@app.get("/api/quant/news")
def quant_news(
    as_of: Optional[str] = Query(default=None),
    limit: int = Query(default=120, ge=1, le=1000),
    fallback_latest: bool = Query(default=True),
    source: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    code: Optional[str] = Query(default=None),
):
    return _safe_news_feed(
        as_of=as_of,
        limit=limit,
        fallback_latest=fallback_latest,
        source=source,
        keyword=keyword,
        code=code,
    )


@app.get("/api/jobs/status")
def jobs_status():
    return job_manager.status()


@app.get("/api/jobs/logs")
def jobs_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    level: Optional[str] = Query(default=None),
    job: Optional[str] = Query(default=None),
):
    return job_manager.logs(limit=limit, level=level, job=job)


@app.post("/api/jobs/{job_name}/pause")
def jobs_pause(job_name: str):
    return job_manager.pause_job(job_name)


@app.post("/api/jobs/{job_name}/resume")
def jobs_resume(job_name: str):
    return job_manager.resume_job(job_name)


@app.get("/api/logs/runtime")
def runtime_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    level: Optional[str] = Query(default=None),
    job: Optional[str] = Query(default=None),
):
    return job_manager.logs(limit=limit, level=level, job=job)


@app.post("/api/jobs/news/fetch")
def jobs_news_fetch(
    hours: int = Query(default=12, ge=1, le=168),
    pages: int = Query(default=5, ge=1, le=30),
    page_size: int = Query(default=20, ge=10, le=100),
):
    result = job_manager.run_news_fetch(hours=hours, pages=pages, page_size=page_size)
    if result.get("status") == "ok":
        quant_engine.events(force=True)
    return result


@app.post("/api/jobs/market/sync")
def jobs_market_sync(
    date: Optional[str] = Query(default=None),
    source: str = Query(default="auto"),
    max_codes: int = Query(default=80, ge=1, le=500),
    force: bool = Query(default=False),
    include_latest: bool = Query(default=True),
):
    return job_manager.run_market_sync(
        date=date,
        source=source,
        max_codes=max_codes,
        force=force,
        include_latest=include_latest,
    )


@app.post("/api/jobs/ai/analyze")
def jobs_ai_analyze(
    as_of: Optional[str] = Query(default=None),
    max_items: int = Query(default=8, ge=1, le=50),
    batch_size: int = Query(default=4, ge=1, le=10),
):
    return job_manager.run_ai_analysis(as_of=as_of, max_items=max_items, batch_size=batch_size)


@app.post("/api/jobs/trading/run")
def jobs_trading_run(
    date: Optional[str] = Query(default=None),
    notify: bool = Query(default=True),
):
    return job_manager.run_trade_cycle(date=date, notify=notify)


@app.post("/api/jobs/strategy/replay")
def jobs_strategy_replay(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    mode: str = Query(default="intraday"),
):
    return job_manager.run_strategy_replay(start_date=start_date, end_date=end_date, mode=mode)


@app.post("/api/jobs/daily/run")
def jobs_daily_run(
    date: Optional[str] = Query(default=None),
    notify: bool = Query(default=True),
):
    return job_manager.run_trade_cycle(date=date, notify=notify)


def _run_system_startup_flow(
    target_date: str,
    replay_start_date: str,
    news_hours: int,
    news_pages: int,
    ai_items: int,
    market_codes: int,
    notify: bool,
) -> Dict[str, Any]:
    steps = []

    job_manager.update_progress("system_startup", 8, "抓取新闻", {"step": "news_fetch"})
    news_result = job_manager.run_news_fetch(hours=news_hours, pages=news_pages, page_size=20)
    if news_result.get("status") == "ok":
        quant_engine.events(force=True)
    steps.append({"name": "新闻抓取", "job": "news_fetch", "result": news_result})

    job_manager.update_progress("system_startup", 22, "AI 分析", {"step": "ai_analysis"})
    ai_result = job_manager.run_ai_analysis(as_of=target_date, max_items=ai_items, batch_size=4)
    steps.append({"name": "AI 分析", "job": "ai_analysis", "result": ai_result})

    job_manager.update_progress("system_startup", 38, "补齐日K", {"step": "kline_fill", "start_date": replay_start_date, "end_date": target_date})
    kline_result = job_manager.run_kline_fill(
        start_date=replay_start_date,
        end_date=target_date,
        max_codes=market_codes,
        force=False,
    )
    steps.append({"name": "日K补齐", "job": "kline_fill", "result": kline_result})

    job_manager.update_progress("system_startup", 54, "同步龙虎榜", {"step": "lhb_sync", "start_date": replay_start_date, "end_date": target_date})
    lhb_result = job_manager.run_lhb_sync(
        start_date=replay_start_date,
        end_date=target_date,
        max_stock_days=market_codes,
        force=False,
    )
    steps.append({"name": "龙虎榜同步", "job": "lhb_sync", "result": lhb_result})

    job_manager.update_progress("system_startup", 68, "同步分时行情", {"step": "market_sync"})
    market_result = job_manager.run_market_sync(
        date=target_date,
        source="auto",
        max_codes=market_codes,
        force=False,
        include_latest=True,
    )
    steps.append({"name": "行情同步", "job": "market_sync", "result": market_result})

    job_manager.update_progress("system_startup", 82, "从数据起点重建模拟交易", {"step": "trade_cycle", "start_date": replay_start_date})
    trade_result = job_manager.run_trade_cycle(date=target_date, notify=notify)
    steps.append({"name": "交易循环", "job": "trade_cycle", "result": trade_result})

    job_manager.update_progress("system_startup", 94, "策略复盘", {"step": "strategy_replay", "start_date": replay_start_date})
    replay_result = job_manager.run_strategy_replay(start_date=replay_start_date, end_date=target_date, mode="intraday")
    steps.append({"name": "策略复盘", "job": "strategy_replay", "result": replay_result})

    failed = [step for step in steps if (step.get("result") or {}).get("status") not in {"ok", "running"}]
    return {
        "status": "partial" if failed else "ok",
        "message": "系统启动流程完成" if not failed else "系统启动流程完成，但有步骤未成功，请查看运行日志",
        "start_date": replay_start_date,
        "date": target_date,
        "steps": steps,
    }


@app.post("/api/admin/system/startup")
def admin_system_startup(
    date: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    news_hours: int = Query(default=24, ge=1, le=168),
    news_pages: int = Query(default=8, ge=1, le=30),
    ai_items: int = Query(default=20, ge=1, le=80),
    market_codes: int = Query(default=200, ge=1, le=1000),
    notify: bool = Query(default=True),
):
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
    }
    return job_manager.run_job(
        "system_startup",
        lambda: _run_system_startup_flow(
            target_date=target_date,
            replay_start_date=replay_start_date,
            news_hours=news_hours,
            news_pages=news_pages,
            ai_items=ai_items,
            market_codes=market_codes,
            notify=notify,
        ),
        payload=payload,
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


@app.get("/api/admin/database/tables")
def admin_database_tables():
    return database_overview()


@app.get("/api/admin/database/table/{table_name}")
def admin_database_table(
    table_name: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        return database_table_rows(table_name, limit=limit, offset=offset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.get("/api/admin/access_logs")
def admin_access_logs(
    limit: int = Query(default=220, ge=1, le=1000),
    username: Optional[str] = Query(default=None),
    ip: Optional[str] = Query(default=None),
    path: Optional[str] = Query(default=None),
):
    return access_logs(limit=limit, username=username, ip=ip, path=path)


@app.get("/api/admin/frontend_users")
def admin_frontend_users():
    return frontend_user_summary()


@app.post("/api/admin/frontend_users")
def admin_create_frontend_user_api(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
    return admin_create_frontend_user(payload, request)


@app.patch("/api/admin/frontend_users/{username}")
def admin_update_frontend_user_api(username: str, payload: Dict[str, Any] = Body(default_factory=dict)):
    return admin_update_frontend_user(username, payload)


@app.post("/api/admin/frontend_users/{username}/password")
def admin_reset_frontend_user_password_api(username: str, payload: Dict[str, Any] = Body(default_factory=dict)):
    return admin_reset_frontend_user_password(username, payload)


@app.post("/api/admin/frontend_users/{username}/ban")
def admin_ban_frontend_user_api(username: str, payload: Dict[str, Any] = Body(default_factory=dict)):
    return admin_set_frontend_user_disabled(username, True, str((payload or {}).get("reason") or ""))


@app.post("/api/admin/frontend_users/{username}/unban")
def admin_unban_frontend_user_api(username: str):
    return admin_set_frontend_user_disabled(username, False)


@app.delete("/api/admin/frontend_users/{username}")
def admin_delete_frontend_user_api(username: str):
    return admin_delete_frontend_user(username)


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


@app.get("/api/quant/correlation")
def quant_correlation(as_of: Optional[str] = Query(default=None), hold_days: int = Query(default=3, ge=1, le=20)):
    return quant_engine.correlation(as_of=as_of, hold_days=hold_days)


@app.get("/api/quant/timeline")
def quant_timeline(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    initial_cash: Optional[float] = Query(default=None, gt=0),
    max_positions: Optional[int] = Query(default=None, ge=1, le=20),
    hold_days: Optional[int] = Query(default=None, ge=1, le=20),
    top_n: Optional[int] = Query(default=None, ge=1, le=20),
    auto_fill: bool = Query(default=True),
):
    return quant_engine.walk_forward(
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        auto_fill=auto_fill,
    )


@app.get("/api/quant/intraday_timeline")
def quant_intraday_timeline(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    initial_cash: Optional[float] = Query(default=None, gt=0),
    max_positions: Optional[int] = Query(default=None, ge=1, le=20),
    hold_days: Optional[int] = Query(default=None, ge=1, le=20),
    top_n: Optional[int] = Query(default=None, ge=1, le=20),
    use_daily_fallback: bool = Query(default=True),
    auto_fill: bool = Query(default=True),
):
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


@app.get("/api/data/biying/status")
def biying_status():
    return biying_minute_sync.status()


@app.get("/api/data/coverage")
def quant_data_coverage(
    as_of: Optional[str] = Query(default=None),
    top_n: int = Query(default=80, ge=1, le=300),
):
    return data_coverage(as_of=as_of, top_n=top_n)


@app.post("/api/data/kline/fill")
def data_kline_fill(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    max_codes: int = Query(default=300, ge=1, le=5000),
    force: bool = Query(default=False),
):
    return job_manager.run_kline_fill(
        start_date=start_date,
        end_date=end_date,
        max_codes=max_codes,
        force=force,
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
):
    result = job_manager.run_lhb_sync(
        start_date=start_date,
        end_date=end_date,
        max_stock_days=max_stock_days,
        force=force,
    )
    if result.get("status") == "ok":
        quant_engine.events(force=True)
    return result


@app.post("/api/data/biying/sync_intraday")
def biying_sync_intraday(
    date: Optional[str] = Query(default=None),
    source: str = Query(default="events"),
    max_codes: int = Query(default=200, ge=1, le=5000),
    codes: Optional[str] = Query(default=None),
    force: bool = Query(default=False),
    include_latest: bool = Query(default=True),
):
    return biying_minute_sync.sync_intraday(
        date=date,
        source=source,
        max_codes=max_codes,
        codes=codes,
        force=force,
        include_latest=include_latest,
    )


@app.get("/api/ai/usage")
def quant_ai_usage():
    return ai_usage_summary()


@app.get("/api/ai/records")
def quant_ai_records(
    limit: int = Query(default=100, ge=1, le=500),
    code: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
):
    return ai_records_feed(limit=limit, code=code, source=source)


@app.get("/api/ai/failures")
def quant_ai_failures(limit: int = Query(default=100, ge=1, le=500)):
    return ai_failures(limit=limit)


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
):
    return quant_engine.backtest(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        auto_fill=auto_fill,
    )


@app.get("/api/quant/portfolio")
def quant_portfolio(as_of: Optional[str] = Query(default=None)):
    return quant_engine.paper_portfolio(as_of=as_of)


@app.get("/api/quant/trading_account")
def quant_trading_account(
    as_of: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
):
    return quant_engine.trading_account(as_of=as_of, limit=limit)


@app.post("/api/quant/run")
def quant_run(as_of: Optional[str] = Query(default=None), calibrate: bool = Query(default=True)):
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


@app.get("/api/news_history")
def news_history(limit: int = Query(default=200, ge=1, le=2000)):
    items = quant_engine.load_news_history()[:limit]
    return {"items": items, "count": len(items)}


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
