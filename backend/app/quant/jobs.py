from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from app.quant.ai_analyzer import ai_analyzer
from app.quant.biying_sync import biying_minute_sync
from app.quant.engine import DATA_DIR, quant_engine, read_json, write_json
from app.quant.news_fetcher import news_fetcher
from app.quant.notifier import trade_notifier


JOB_STATE_FILE = DATA_DIR / "quant_job_state.json"
JOB_LOG_FILE = DATA_DIR / "quant_runtime_logs.jsonl"
SENSITIVE_KEY_PARTS = ("key", "token", "password", "secret", "license", "authorization", "cookie")
JOB_LABELS = {
    "scheduler": "调度器",
    "news_fetch": "新闻抓取",
    "ai_analysis": "AI 分析",
    "market_sync": "行情同步",
    "trade_cycle": "交易循环",
    "strategy_replay": "策略复盘",
    "system_startup": "系统启动",
    "admin_backup": "数据备份",
    "admin_data_export": "数据导出",
    "admin_data_import": "数据导入",
    "admin_restart": "服务重启",
    "admin_config": "配置保存",
}


def _now_cn() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _iso_now() -> str:
    return _now_cn().isoformat(timespec="seconds")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(float(os.getenv(name, "") or default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _job_label(name: str) -> str:
    return JOB_LABELS.get(str(name or ""), str(name or "任务"))


def _sanitize_for_log(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "<truncated>"
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in SENSITIVE_KEY_PARTS):
                sanitized[key_text] = "***"
            else:
                sanitized[key_text] = _sanitize_for_log(item, depth + 1)
        return sanitized
    if isinstance(value, list):
        items = [_sanitize_for_log(item, depth + 1) for item in value[:30]]
        if len(value) > 30:
            items.append({"truncated_count": len(value) - 30})
        return items
    if isinstance(value, tuple):
        return [_sanitize_for_log(item, depth + 1) for item in value[:30]]
    if isinstance(value, str):
        return value if len(value) <= 800 else f"{value[:800]}..."
    return value


class QuantJobManager:
    def __init__(self) -> None:
        self.state_file = JOB_STATE_FILE
        self._lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._log_lock = threading.RLock()
        self._running: Dict[str, bool] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    def _load_state(self) -> Dict[str, Any]:
        with self._state_lock:
            payload = read_json(self.state_file, {})
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("scheduler", {})
        payload.setdefault("jobs", {})
        return payload

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            write_json(self.state_file, state)

    def status(self) -> Dict[str, Any]:
        state = self._load_state()
        with self._lock:
            running = {name: value for name, value in self._running.items() if value}
        state["running"] = running
        state["news_fetcher"] = news_fetcher.status()
        state["ai_analyzer"] = ai_analyzer.status()
        state["biying"] = biying_minute_sync.status()
        return {"status": "ok", **state}

    def _append_log(
        self,
        level: str,
        message: str,
        job: str = "",
        stage: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry = {
            "ts": _iso_now(),
            "level": str(level or "info").lower(),
            "job": str(job or ""),
            "stage": str(stage or ""),
            "message": str(message or ""),
            "payload": _sanitize_for_log(payload or {}),
        }
        with self._log_lock:
            JOB_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with JOB_LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def logs(self, limit: int = 200, level: Optional[str] = None, job: Optional[str] = None) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 200), 1000))
        level_filter = str(level or "").strip().lower()
        job_filter = str(job or "").strip()
        rows: deque[Dict[str, Any]] = deque(maxlen=limit)
        with self._log_lock:
            if JOB_LOG_FILE.exists():
                with JOB_LOG_FILE.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if level_filter and str(item.get("level") or "").lower() != level_filter:
                            continue
                        if job_filter and str(item.get("job") or "") != job_filter:
                            continue
                        rows.append(item)
        items = list(rows)
        items.reverse()
        return {"status": "ok", "items": items, "count": len(items), "limit": limit}

    def _record_job_start(self, name: str, payload: Dict[str, Any]) -> None:
        with self._state_lock:
            state = self._load_state()
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {})
            current.update(
                {
                    "name": name,
                    "status": "running",
                    "last_started_at": _iso_now(),
                    "last_payload": payload,
                }
            )
            self._save_state(state)
        self._append_log("info", f"{_job_label(name)}已开始", job=name, stage="start", payload=payload)

    def _record_job_finish(self, name: str, started: float, result: Dict[str, Any], error: str = "") -> Dict[str, Any]:
        with self._state_lock:
            state = self._load_state()
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {})
            success_count = int(current.get("success_count", 0) or 0)
            failure_count = int(current.get("failure_count", 0) or 0)
            if error:
                failure_count += 1
                status = "failed"
            else:
                success_count += 1
                status = "ok"
            current.update(
                {
                    "name": name,
                    "status": status,
                    "last_finished_at": _iso_now(),
                    "duration_ms": round((time.time() - started) * 1000, 2),
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "last_error": error,
                    "last_result": result,
                }
            )
            self._save_state(state)
            result_payload = {
                "duration_ms": current.get("duration_ms"),
                "success_count": success_count,
                "failure_count": failure_count,
                "result": result,
                "error": error,
            }
            self._append_log(
                "error" if error else "info",
                f"{_job_label(name)}{'失败' if error else '完成'}",
                job=name,
                stage="finish" if not error else "error",
                payload=result_payload,
            )
            return {"status": status, "job": current}

    def run_job(self, name: str, fn: Callable[[], Dict[str, Any]], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        with self._lock:
            if self._running.get(name):
                message = f"{_job_label(name)}正在运行，已跳过重复请求"
                self._append_log("warning", message, job=name, stage="skip", payload=payload)
                return {"status": "running", "message": message}
            self._running[name] = True
        started = time.time()
        self._record_job_start(name, payload)
        try:
            result = fn()
            return self._record_job_finish(name, started, result)
        except Exception as exc:
            return self._record_job_finish(name, started, {}, error=str(exc))
        finally:
            with self._lock:
                self._running[name] = False

    def run_news_fetch(self, hours: int = 12, pages: int = 5, page_size: int = 20) -> Dict[str, Any]:
        payload = {"hours": hours, "pages": pages, "page_size": page_size}
        return self.run_job(
            "news_fetch",
            lambda: news_fetcher.run(hours=hours, pages=pages, page_size=page_size),
            payload=payload,
        )

    def run_market_sync(
        self,
        date: Optional[str] = None,
        source: str = "recommendations",
        max_codes: int = 80,
        force: bool = False,
        include_latest: bool = True,
    ) -> Dict[str, Any]:
        date = str(date or _now_cn().strftime("%Y-%m-%d")).strip()
        source = str(source or "recommendations").strip() or "recommendations"
        max_codes = max(1, min(int(max_codes or 80), 500))
        explicit_codes = self._auto_market_codes(date=date, max_codes=max_codes) if source == "auto" else None
        payload = {
            "date": date,
            "source": source,
            "max_codes": max_codes,
            "force": bool(force),
            "include_latest": bool(include_latest),
            "codes_count": len(explicit_codes.split(",")) if explicit_codes else 0,
        }
        return self.run_job(
            "market_sync",
            lambda: biying_minute_sync.sync_intraday(
                date=date,
                source="events" if explicit_codes else source,
                max_codes=max_codes,
                codes=explicit_codes,
                force=force,
                include_latest=include_latest,
            ),
            payload=payload,
        )

    def run_ai_analysis(
        self,
        as_of: Optional[str] = None,
        max_items: int = 8,
        batch_size: int = 4,
    ) -> Dict[str, Any]:
        as_of = str(as_of or _now_cn().strftime("%Y-%m-%d")).strip()
        max_items = max(1, min(int(max_items or 8), 50))
        batch_size = max(1, min(int(batch_size or 4), 10))
        payload = {"as_of": as_of, "max_items": max_items, "batch_size": batch_size}
        return self.run_job(
            "ai_analysis",
            lambda: ai_analyzer.run(as_of=as_of, max_items=max_items, batch_size=batch_size),
            payload=payload,
        )

    def run_trade_cycle(self, date: Optional[str] = None, notify: bool = True) -> Dict[str, Any]:
        date = str(date or _now_cn().strftime("%Y-%m-%d")).strip()
        payload = {"date": date, "notify": bool(notify)}

        def execute() -> Dict[str, Any]:
            portfolio = quant_engine.run_paper_trading(as_of=date)
            trades = portfolio.get("trades", []) if isinstance(portfolio.get("trades"), list) else []
            day_trades = [trade for trade in trades if isinstance(trade, dict) and str(trade.get("date") or "") == date]
            notification = trade_notifier.notify_trade_events(day_trades, as_of=date, source="paper_trading") if notify else {"status": "disabled", "sent": 0}
            return {
                "status": "ok",
                "date": date,
                "trades": len(day_trades),
                "buys": sum(1 for trade in day_trades if str(trade.get("side") or "").upper() == "BUY"),
                "sells": sum(1 for trade in day_trades if str(trade.get("side") or "").upper() == "SELL"),
                "notification": notification,
                "cash": portfolio.get("cash", 0),
                "positions": len(portfolio.get("positions", []) or []),
                "total_value": portfolio.get("total_value", 0),
            }

        return self.run_job("trade_cycle", execute, payload=payload)

    def run_strategy_replay(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mode: str = "intraday",
    ) -> Dict[str, Any]:
        start_date = str(start_date or os.getenv("STRATEGY_REPLAY_START_DATE") or "2026-03-01").strip()
        end_date = str(end_date or quant_engine.latest_event_date() or _now_cn().strftime("%Y-%m-%d")).strip()
        mode = str(mode or os.getenv("STRATEGY_REPLAY_MODE") or "intraday").strip().lower()
        if mode not in {"daily", "intraday"}:
            mode = "intraday"
        payload = {"start_date": start_date, "end_date": end_date, "mode": mode}

        def execute() -> Dict[str, Any]:
            if mode == "daily":
                result = quant_engine.walk_forward(start_date=start_date, end_date=end_date)
            else:
                result = quant_engine.walk_forward_intraday(
                    start_date=start_date,
                    end_date=end_date,
                    use_daily_fallback=True,
                )
            days = result.get("days") if isinstance(result.get("days"), list) else []
            trades = result.get("trades") if isinstance(result.get("trades"), list) else []
            return {
                "status": "ok",
                "mode": result.get("mode") or mode,
                "start_date": result.get("start_date") or start_date,
                "end_date": result.get("end_date") or end_date,
                "initial_cash": result.get("initial_cash", 0),
                "final_value": result.get("final_value", 0),
                "return_pct": result.get("return_pct", 0),
                "max_drawdown_pct": result.get("max_drawdown_pct", 0),
                "closed_trades": result.get("closed_trades", 0),
                "win_rate": result.get("win_rate", 0),
                "day_count": len(days),
                "trade_count": len(trades),
                "latest_day": days[-1] if days else {},
                "generated_at": _iso_now(),
            }

        return self.run_job("strategy_replay", execute, payload=payload)

    def _auto_market_codes(self, date: str, max_codes: int = 80) -> str:
        from app.quant.engine import digits6, quant_engine

        max_codes = max(1, min(int(max_codes or 80), 500))
        seen = set()
        codes = []

        def add(code: Any) -> None:
            clean = digits6(code)
            if clean and clean not in seen and quant_engine.universe.is_tradeable_a_share(clean):
                seen.add(clean)
                codes.append(clean)

        portfolio = quant_engine.trading_account(as_of=date, limit=200)
        for pos in portfolio.get("positions", []):
            add(pos.get("code"))

        recs = quant_engine.recommendations(as_of=date, lookback_days=2, top_n=max_codes)
        for item in recs.get("items", []):
            add(item.get("code"))
            if len(codes) >= max_codes:
                break

        if len(codes) < max_codes:
            events = [event for event in quant_engine.events() if event.date <= date]
            events.sort(key=lambda event: (event.date, event.impact_score, event.timestamp), reverse=True)
            for event in events:
                add(event.code)
                if len(codes) >= max_codes:
                    break

        return ",".join(codes[:max_codes])

    def _news_interval_seconds(self) -> int:
        return _env_int("NEWS_FETCH_INTERVAL_SECONDS", 3600)

    def _market_interval_seconds(self) -> int:
        return _env_int("MARKET_SYNC_INTERVAL_SECONDS", 300)

    def _ai_interval_seconds(self) -> int:
        return _env_int("AI_ANALYSIS_INTERVAL_SECONDS", 3600)

    def _trade_interval_seconds(self) -> int:
        return _env_int("TRADE_CYCLE_INTERVAL_SECONDS", 300 if self._is_market_open() else 3600)

    def _strategy_replay_interval_seconds(self) -> int:
        return _env_int("STRATEGY_REPLAY_INTERVAL_SECONDS", 3600)

    def _is_trading_day(self, now: Optional[datetime] = None) -> bool:
        now = now or _now_cn()
        date = now.strftime("%Y-%m-%d")
        holidays = {item.strip() for item in str(os.getenv("TRADING_HOLIDAYS", "") or "").split(",") if item.strip()}
        extra_days = {item.strip() for item in str(os.getenv("TRADING_EXTRA_DAYS", "") or "").split(",") if item.strip()}
        if date in holidays:
            return False
        if date in extra_days:
            return True
        return now.weekday() < 5

    def _is_market_open(self, now: Optional[datetime] = None) -> bool:
        now = now or _now_cn()
        if not self._is_trading_day(now):
            return False
        current_minutes = now.hour * 60 + now.minute
        return (9 * 60 + 30) <= current_minutes <= (11 * 60 + 30) or (13 * 60) <= current_minutes <= (15 * 60)

    async def _scheduler_loop(self) -> None:
        assert self._stop_event is not None
        next_news_fetch = 0.0
        next_ai_analysis = time.time() + 30
        next_market_sync = time.time() + 40
        next_trade_cycle = time.time() + 50
        next_strategy_replay = time.time() + 70
        while not self._stop_event.is_set():
            now_ts = time.time()
            now_cn = _now_cn()
            ran_task = False
            if now_ts >= next_news_fetch:
                await asyncio.to_thread(self.run_news_fetch, 24, 6, 20)
                next_news_fetch = time.time() + self._news_interval_seconds()
                ran_task = True
            if now_ts >= next_ai_analysis:
                await asyncio.to_thread(self.run_ai_analysis, None, 8, 4)
                next_ai_analysis = time.time() + self._ai_interval_seconds()
                ran_task = True
            if now_ts >= next_market_sync:
                if self._is_market_open(now_cn):
                    await asyncio.to_thread(self.run_market_sync, None, "auto", 80, False, True)
                    next_market_sync = time.time() + self._market_interval_seconds()
                else:
                    self._append_log(
                        "info",
                        "非 A 股交易时段，跳过行情同步",
                        job="market_sync",
                        stage="skip",
                        payload={"now": now_cn.isoformat(timespec="seconds"), "trading_day": self._is_trading_day(now_cn), "market_open": False},
                    )
                    next_market_sync = time.time() + 60
                ran_task = True
            if now_ts >= next_trade_cycle:
                if self._is_market_open(now_cn):
                    await asyncio.to_thread(self.run_trade_cycle, None, True)
                    next_trade_cycle = time.time() + self._trade_interval_seconds()
                else:
                    self._append_log(
                        "info",
                        "非 A 股交易时段，跳过交易循环",
                        job="trade_cycle",
                        stage="skip",
                        payload={"now": now_cn.isoformat(timespec="seconds"), "trading_day": self._is_trading_day(now_cn), "market_open": False},
                    )
                    next_trade_cycle = time.time() + 60
                ran_task = True
            if _env_bool("STRATEGY_REPLAY_ENABLED", True) and now_ts >= next_strategy_replay:
                await asyncio.to_thread(self.run_strategy_replay, None, None, os.getenv("STRATEGY_REPLAY_MODE", "intraday"))
                next_strategy_replay = time.time() + self._strategy_replay_interval_seconds()
                ran_task = True
            elif not _env_bool("STRATEGY_REPLAY_ENABLED", True):
                next_strategy_replay = time.time() + self._strategy_replay_interval_seconds()
            if ran_task:
                state = self._load_state()
                state["scheduler"] = {
                    "enabled": True,
                    "status": "running",
                    "last_tick_at": _iso_now(),
                    "market_open": self._is_market_open(),
                    "trading_day": self._is_trading_day(),
                    "next_news_fetch_at": datetime.fromtimestamp(next_news_fetch, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "news_interval_seconds": self._news_interval_seconds(),
                    "next_ai_analysis_at": datetime.fromtimestamp(next_ai_analysis, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "ai_interval_seconds": self._ai_interval_seconds(),
                    "next_market_sync_at": datetime.fromtimestamp(next_market_sync, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "market_interval_seconds": self._market_interval_seconds(),
                    "next_trade_cycle_at": datetime.fromtimestamp(next_trade_cycle, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "trade_interval_seconds": self._trade_interval_seconds(),
                    "next_strategy_replay_at": datetime.fromtimestamp(next_strategy_replay, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "strategy_replay_interval_seconds": self._strategy_replay_interval_seconds(),
                    "strategy_replay_start_date": os.getenv("STRATEGY_REPLAY_START_DATE") or "2026-03-01",
                    "strategy_replay_enabled": _env_bool("STRATEGY_REPLAY_ENABLED", True),
                }
                self._save_state(state)
                self._append_log(
                    "info",
                    "调度器心跳已更新",
                    job="scheduler",
                    stage="tick",
                    payload=state["scheduler"],
                )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    def start(self) -> Dict[str, Any]:
        if self._scheduler_task and not self._scheduler_task.done():
            return {"status": "ok", "scheduler": "already_running"}
        self._stop_event = asyncio.Event()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        state = self._load_state()
        state["scheduler"] = {"enabled": True, "status": "starting", "started_at": _iso_now()}
        self._save_state(state)
        self._append_log("info", "调度器已启动", job="scheduler", stage="start", payload=state["scheduler"])
        return {"status": "ok", "scheduler": "started"}

    def mark_scheduler_disabled(self, reason: str = "disabled") -> Dict[str, Any]:
        state = self._load_state()
        state["scheduler"] = {
            "enabled": False,
            "status": "disabled",
            "reason": reason,
            "updated_at": _iso_now(),
        }
        self._save_state(state)
        self._append_log("info", "调度器已禁用", job="scheduler", stage="disabled", payload=state["scheduler"])
        return {"status": "ok", "scheduler": "disabled"}

    async def stop(self) -> Dict[str, Any]:
        if self._stop_event:
            self._stop_event.set()
        if self._scheduler_task:
            try:
                await asyncio.wait_for(self._scheduler_task, timeout=20)
            except asyncio.TimeoutError:
                self._scheduler_task.cancel()
        state = self._load_state()
        state["scheduler"] = {"enabled": False, "status": "stopped", "stopped_at": _iso_now()}
        self._save_state(state)
        self._append_log("info", "调度器已停止", job="scheduler", stage="stop", payload=state["scheduler"])
        return {"status": "ok", "scheduler": "stopped"}


job_manager = QuantJobManager()
