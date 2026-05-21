from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Body, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.quant.biying_sync import biying_minute_sync
from app.quant.engine import DATA_DIR, DEFAULT_AI_MODEL, quant_engine
from app.quant.evolution import strategy_evolution
from app.quant.jobs import job_manager
from app.quant.monitoring import ai_failures, ai_records_feed, ai_usage_summary, data_coverage
from app.quant.news_fetcher import news_fetcher
from app.quant.notifier import trade_notifier


BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
PROJECT_ROOT = BASE_DIR.parent
BACKUP_DIR = PROJECT_ROOT / "backups"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


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


@app.on_event("startup")
async def startup_jobs():
    if _env_flag("QUANT_SCHEDULER_ENABLED", default=True):
        job_manager.start()
    else:
        job_manager.mark_scheduler_disabled("QUANT_SCHEDULER_ENABLED=0")


@app.on_event("shutdown")
async def shutdown_jobs():
    await job_manager.stop()


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


@app.post("/api/jobs/daily/run")
def jobs_daily_run(
    date: Optional[str] = Query(default=None),
    notify: bool = Query(default=True),
):
    return job_manager.run_trade_cycle(date=date, notify=notify)


@app.post("/api/admin/backup")
def admin_backup():
    result = _create_data_backup()
    job_manager._append_log("info", "admin backup requested", job="admin_backup", stage="finish", payload=result)
    return result


@app.post("/api/admin/restart")
def admin_restart(background_tasks: BackgroundTasks):
    if not _env_flag("QUANT_ALLOW_API_RESTART", default=False):
        result = {
            "status": "disabled",
            "message": "Set QUANT_ALLOW_API_RESTART=1 on the server to enable API-triggered restart.",
        }
        job_manager._append_log("warning", "admin restart blocked", job="admin_restart", stage="blocked", payload=result)
        return result
    script = PROJECT_ROOT / "scripts" / "restart_server.sh"
    if not script.exists() or not shutil.which("bash"):
        result = {
            "status": "unavailable",
            "message": "restart script or bash runtime is not available on this host.",
        }
        job_manager._append_log("error", "admin restart unavailable", job="admin_restart", stage="unavailable", payload=result)
        return result
    background_tasks.add_task(_restart_service_after_response)
    result = {"status": "ok", "message": "restart scheduled"}
    job_manager._append_log("warning", "admin restart scheduled", job="admin_restart", stage="scheduled", payload=result)
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
    initial_cash: float = Query(default=1_000_000.0, gt=0),
    max_positions: Optional[int] = Query(default=None, ge=1, le=20),
    hold_days: Optional[int] = Query(default=None, ge=1, le=20),
    top_n: Optional[int] = Query(default=None, ge=1, le=20),
):
    return quant_engine.walk_forward(
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
    )


@app.get("/api/quant/intraday_timeline")
def quant_intraday_timeline(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    initial_cash: float = Query(default=1_000_000.0, gt=0),
    max_positions: Optional[int] = Query(default=None, ge=1, le=20),
    hold_days: Optional[int] = Query(default=None, ge=1, le=20),
    top_n: Optional[int] = Query(default=None, ge=1, le=20),
    use_daily_fallback: bool = Query(default=True),
):
    return quant_engine.walk_forward_intraday(
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        max_positions=max_positions,
        hold_days=hold_days,
        top_n=top_n,
        use_daily_fallback=use_daily_fallback,
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
    hold_days: int = Query(default=3, ge=1, le=20),
    top_n: int = Query(default=5, ge=1, le=20),
):
    return quant_engine.backtest(
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
        hold_days=hold_days,
        top_n=top_n,
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
