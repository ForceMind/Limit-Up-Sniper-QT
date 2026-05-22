from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime
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
from app.quant.data_transfer import DataPackageError, clear_sample_quant_state, create_safe_data_package, import_data_package
from app.quant.engine import DATA_DIR, DEFAULT_AI_MODEL, quant_engine
from app.quant.evolution import strategy_evolution
from app.quant.lhb_sync import lhb_status
from app.quant.jobs import job_manager
from app.quant.monitoring import ai_failures, ai_records_feed, ai_usage_summary, data_coverage
from app.quant.news_fetcher import news_fetcher
from app.quant.notifier import trade_notifier
from app.quant.security import (
    auth_status,
    frontend_user_summary,
    login,
    register_frontend_user,
    require_request_scope,
    required_scope_for_api,
    runtime_config_form,
    runtime_config_status,
    setup_auth,
    update_runtime_config,
    verify_token,
)


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
PROJECT_ROOT = BASE_DIR.parent
BACKUP_DIR = PROJECT_ROOT / "backups"


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

app = FastAPI(title="A-Share Quant Agent System", version="0.1.0")
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


@app.get("/api/auth/status")
def api_auth_status():
    return auth_status()


@app.post("/api/auth/setup")
def api_auth_setup(payload: Dict[str, Any] = Body(default_factory=dict)):
    return setup_auth(payload)


@app.post("/api/auth/login")
def api_auth_login(payload: Dict[str, Any] = Body(default_factory=dict)):
    return login(payload)


@app.post("/api/auth/register")
def api_auth_register(request: Request, payload: Dict[str, Any] = Body(default_factory=dict)):
    return register_frontend_user(payload, request)


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
    latest_news_time = news_fetcher.latest_history_time()
    data_date = latest_news_time[:10] if latest_news_time else quant_engine.latest_event_date()
    return {
        "status": "ok",
        "system": "quant",
        "data_dir": str(DATA_DIR),
        "current_date": now_cn.strftime("%Y-%m-%d"),
        "current_time": now_cn.isoformat(timespec="seconds"),
        "latest_event_date": data_date,
        "latest_news_time": latest_news_time,
        "data_date": data_date,
        "ai_model": DEFAULT_AI_MODEL,
        "jobs": job_manager.status(),
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


@app.get("/api/front/public_snapshot")
def frontend_public_snapshot(
    as_of: Optional[str] = Query(default=None),
    mobile: bool = Query(default=False),
):
    news_limit = 30 if mobile else 80
    news_payload = quant_engine.news_feed(as_of=as_of, limit=news_limit, fallback_latest=True)
    return {
        "status": "ok",
        "status_payload": status(),
        "jobs": {"scheduler": job_manager.status().get("scheduler", {})},
        "news": news_payload,
        "market_sentiment": _market_sentiment(news_payload),
    }


@app.get("/api/front/snapshot")
def frontend_snapshot(
    as_of: Optional[str] = Query(default=None),
    mobile: bool = Query(default=False),
):
    news_limit = 30 if mobile else 80
    top_n = 12 if mobile else 30
    news_payload = quant_engine.news_feed(as_of=as_of, limit=news_limit, fallback_latest=True)
    return {
        "status": "ok",
        "status_payload": status(),
        "jobs": job_manager.status(),
        "logs": job_manager.logs(limit=12),
        "trading_account": quant_engine.trading_account(as_of=as_of, limit=500),
        "news": news_payload,
        "recommendations": quant_engine.recommendations(as_of=as_of, lookback_days=2, top_n=top_n),
        "daily_plan": quant_engine.daily_plan(as_of=as_of, limit_days=120),
        "strategy_models": strategy_evolution.models(),
        "market_sentiment": _market_sentiment(news_payload),
    }


@app.get("/api/admin/snapshot")
def admin_snapshot(as_of: Optional[str] = Query(default=None)):
    return {
        "status": "ok",
        "status_payload": status(),
        "jobs": job_manager.status(),
        "biying": biying_minute_sync.status(),
        "lhb": lhb_status(),
        "ai_usage": ai_usage_summary(),
        "notification_status": trade_notifier.status(),
        "evolution_status": strategy_evolution.status(),
        "strategy_models": strategy_evolution.models(),
        "access_logs": access_logs(limit=120),
        "frontend_users": frontend_user_summary(),
        "dashboard": quant_engine.dashboard(as_of=as_of, include_heavy=False),
        "trading_account": quant_engine.trading_account(as_of=as_of, limit=1000),
        "news": quant_engine.news_feed(as_of=as_of, limit=120, fallback_latest=True),
        "coverage": data_coverage(as_of=as_of, top_n=100),
        "ai_failures": ai_failures(limit=40),
        "ai_records": ai_records_feed(limit=80),
    }


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
            status_payload = status()
            jobs_payload = job_manager.status()
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


@app.get("/api/quant/models")
def quant_strategy_models():
    return strategy_evolution.models()


@app.post("/api/quant/model/apply")
def quant_apply_strategy_model(model_id: str = Query(...)):
    return strategy_evolution.apply_model(model_id)


@app.post("/api/quant/evolve_strategy")
def quant_evolve_strategy(
    generations: int = Query(default=4, ge=1, le=30),
    population_size: int = Query(default=16, ge=6, le=80),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    apply_best: bool = Query(default=False),
):
    return strategy_evolution.run(
        generations=generations,
        population_size=population_size,
        start_date=start_date,
        end_date=end_date,
        apply_best=apply_best,
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
    return quant_engine.news_feed(
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


@app.post("/api/admin/system/startup")
def admin_system_startup(
    date: Optional[str] = Query(default=None),
    news_hours: int = Query(default=24, ge=1, le=168),
    news_pages: int = Query(default=8, ge=1, le=30),
    ai_items: int = Query(default=20, ge=1, le=80),
    market_codes: int = Query(default=200, ge=1, le=1000),
    notify: bool = Query(default=True),
):
    target_date = str(date or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")).strip()
    payload = {
        "date": target_date,
        "news_hours": news_hours,
        "news_pages": news_pages,
        "ai_items": ai_items,
        "market_codes": market_codes,
        "notify": notify,
    }
    job_manager._append_log("info", "系统启动流程开始", job="system_startup", stage="start", payload=payload)
    steps = []

    news_result = job_manager.run_news_fetch(hours=news_hours, pages=news_pages, page_size=20)
    if news_result.get("status") == "ok":
        quant_engine.events(force=True)
    steps.append({"name": "新闻抓取", "job": "news_fetch", "result": news_result})

    ai_result = job_manager.run_ai_analysis(as_of=target_date, max_items=ai_items, batch_size=4)
    steps.append({"name": "AI 分析", "job": "ai_analysis", "result": ai_result})

    kline_result = job_manager.run_kline_fill(
        start_date="2026-03-01",
        end_date=target_date,
        max_codes=market_codes,
        force=False,
    )
    steps.append({"name": "日K补齐", "job": "kline_fill", "result": kline_result})

    lhb_result = job_manager.run_lhb_sync(
        start_date="2026-03-01",
        end_date=target_date,
        max_stock_days=market_codes,
        force=False,
    )
    steps.append({"name": "龙虎榜同步", "job": "lhb_sync", "result": lhb_result})

    market_result = job_manager.run_market_sync(
        date=target_date,
        source="auto",
        max_codes=market_codes,
        force=False,
        include_latest=True,
    )
    steps.append({"name": "行情同步", "job": "market_sync", "result": market_result})

    trade_result = job_manager.run_trade_cycle(date=target_date, notify=notify)
    steps.append({"name": "交易循环", "job": "trade_cycle", "result": trade_result})

    replay_result = job_manager.run_strategy_replay(start_date="2026-03-01", end_date=target_date, mode="intraday")
    steps.append({"name": "策略复盘", "job": "strategy_replay", "result": replay_result})

    failed = [step for step in steps if (step.get("result") or {}).get("status") not in {"ok", "running"}]
    result = {
        "status": "partial" if failed else "ok",
        "message": "系统启动流程完成" if not failed else "系统启动流程完成，但有步骤未成功，请查看运行日志",
        "date": target_date,
        "steps": steps,
    }
    job_manager._append_log(
        "warning" if failed else "info",
        result["message"],
        job="system_startup",
        stage="finish",
        payload=result,
    )
    return result


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


@app.post("/api/admin/data/import")
async def admin_data_import(request: Request, backup: bool = Query(default=True)):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    max_bytes = int(max(1.0, _env_float("QT_DATA_UPLOAD_MAX_MB", 1024.0)) * 1024 * 1024)
    upload_fd, upload_name = tempfile.mkstemp(prefix="qt_data_upload_", suffix=".tar.gz", dir=str(BACKUP_DIR))
    os.close(upload_fd)
    upload_file = Path(upload_name)
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
        backup_result: Dict[str, Any] = {}
        if backup:
            backup_result = _create_data_backup()
            if backup_result.get("status") != "ok":
                raise HTTPException(status_code=500, detail=f"导入前备份失败：{backup_result.get('message') or 'unknown'}")
        result = await asyncio.to_thread(import_data_package, upload_file, DATA_DIR)
        _refresh_quant_caches()
        result["backup"] = backup_result
        result["received_bytes"] = received
        job_manager._append_log("warning", "后台已导入数据迁移包", job="admin_data_import", stage="finish", payload=result)
        return result
    except DataPackageError as exc:
        job_manager._append_log("error", "后台数据导入被拒绝", job="admin_data_import", stage="rejected", payload={"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
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
    max_codes: int = Query(default=300, ge=1, le=2000),
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
    max_codes: int = Query(default=200, ge=1, le=2000),
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


@app.get("/admin", include_in_schema=False)
def admin_index():
    admin_file = FRONTEND_DIR / "admin" / "index.html"
    if admin_file.exists():
        return FileResponse(admin_file)
    return JSONResponse({"status": "ok", "message": "frontend/admin/index.html not found"})


@app.get("/admin/index.html", include_in_schema=False)
def admin_index_html():
    return admin_index()
