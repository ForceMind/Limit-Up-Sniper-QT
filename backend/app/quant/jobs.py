from __future__ import annotations

import asyncio
import ctypes
import gc
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from app.quant.ai_analyzer import ai_analyzer
from app.quant.biying_sync import biying_minute_sync
from app.quant.capital_strategy import capital_presets
from app.quant.engine import DATA_DIR, quant_engine, read_json, write_json
from app.quant.evolution import strategy_evolution
from app.quant.frontend_precompute import precompute_frontend_payloads
from app.quant.lhb_sync import lhb_status, sync_lhb
from app.quant.news_fetcher import news_fetcher
from app.quant.notifier import trade_notifier


JOB_STATE_FILE = DATA_DIR / "quant_job_state.json"
JOB_LOG_FILE = DATA_DIR / "quant_runtime_logs.jsonl"
SENSITIVE_KEY_PARTS = ("key", "token", "password", "secret", "license", "authorization", "cookie")
HEAVY_CACHE_TRIM_JOBS = {
    "kline_fill",
    "lhb_sync",
    "trade_cycle",
    "strategy_replay",
    "strategy_evolution",
    "frontend_payload_precompute",
    "system_startup",
}
JOB_LABELS = {
    "scheduler": "调度器",
    "news_fetch": "新闻抓取",
    "ai_analysis": "AI 分析",
    "market_sync": "行情同步",
    "kline_fill": "日K补齐",
    "lhb_sync": "龙虎榜同步",
    "trade_cycle": "交易循环",
    "strategy_replay": "策略复盘",
    "strategy_evolution": "策略进化",
    "frontend_payload_precompute": "前台推荐预计算",
    "system_startup": "系统启动",
    "admin_backup": "数据备份",
    "admin_data_export": "数据导出",
    "admin_data_import": "数据导入",
    "admin_data_clear_sample": "清理样例",
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


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
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

    def _process_start_grace_seconds(self) -> float:
        return max(1.0, min(_env_float("QT_JOB_PROCESS_START_GRACE_SECONDS", 8.0), 120.0))

    def _load_state(self) -> Dict[str, Any]:
        with self._state_lock:
            payload = read_json(self.state_file, {})
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("scheduler", {})
        payload.setdefault("jobs", {})
        payload.setdefault("paused_jobs", {})
        return payload

    def _save_state(self, state: Dict[str, Any]) -> None:
        with self._state_lock:
            write_json(self.state_file, state)

    def _compact_job_item(self, item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        keep_keys = (
            "status",
            "job",
            "stage",
            "process",
            "process_pid",
            "started_at",
            "finished_at",
            "last_started_at",
            "last_finished_at",
            "updated_at",
            "duration_ms",
            "progress_pct",
            "progress_message",
            "message",
            "error",
            "exit_code",
            "stop_requested",
            "stop_requested_at",
            "stop_message",
        )
        compact = {key: item.get(key) for key in keep_keys if key in item}
        summary_keys = {
            "status",
            "message",
            "date",
            "as_of",
            "start_date",
            "end_date",
            "requested_start_date",
            "requested_end_date",
            "mode",
            "hours",
            "pages",
            "page_size",
            "source",
            "max_codes",
            "target_scope",
            "target_count",
            "fetched",
            "inserted",
            "total",
            "selected",
            "records_added",
            "stocks",
            "requested",
            "added_rows",
            "requested_stock_days",
            "seat_rows_fetched",
            "trades",
            "buys",
            "sells",
            "model_count",
            "return_pct",
            "trade_count",
            "closed_trades",
            "signal_count",
        }
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if payload:
            compact["payload_summary"] = {
                key: value
                for key, value in payload.items()
                if key in {"status", "as_of", "date", "start_date", "end_date", "count", "processed", "fetched", "added_rows", "updated_rows"}
            }
        last_payload = item.get("last_payload") if isinstance(item.get("last_payload"), dict) else {}
        if last_payload:
            compact["last_payload"] = {key: value for key, value in last_payload.items() if key in summary_keys}
        last_result = item.get("last_result") if isinstance(item.get("last_result"), dict) else {}
        if last_result:
            compact["last_result"] = {key: value for key, value in last_result.items() if key in summary_keys}
        return compact

    def _compact_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        compact = {
            "scheduler": state.get("scheduler", {}),
            "jobs": {},
            "paused_jobs": state.get("paused_jobs", {}),
            "strategy_replay_cursor": state.get("strategy_replay_cursor", {}),
            "updated_at": state.get("updated_at", ""),
        }
        jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
        compact["jobs"] = {name: self._compact_job_item(item) for name, item in jobs.items()}
        return compact

    def status(self, light: bool = False) -> Dict[str, Any]:
        self.reconcile_process_jobs()
        state = self._load_state()
        if light:
            state = self._compact_state(state)
        with self._lock:
            running = {name: value for name, value in self._running.items() if value}
        state["running"] = running
        state["news_fetcher"] = news_fetcher.status()
        state["ai_analyzer"] = ai_analyzer.status()
        state["biying"] = biying_minute_sync.status()
        state["lhb"] = lhb_status()
        state["runtime"] = {"memory": self._process_memory_snapshot(), "cache": quant_engine.cache_stats()}
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

    def _parse_iso_ts(self, value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return parsed
        except Exception:
            return None

    def _pid_alive(self, pid: Any) -> bool:
        try:
            value = int(pid)
        except Exception:
            return False
        if value <= 0:
            return False
        if os.name == "nt":
            try:
                process_query_limited_information = 0x1000
                still_active = 259
                handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, value)
                if not handle:
                    return False
                exit_code = ctypes.c_ulong()
                try:
                    if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return False
                    return int(exit_code.value) == still_active
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                return True
        try:
            os.kill(value, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    def reconcile_process_jobs(self) -> Dict[str, Any]:
        now = _now_cn()
        grace_seconds = self._process_start_grace_seconds()
        stale_jobs = []
        with self._state_lock:
            state = self._load_state()
            jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
            for name, current in jobs.items():
                if not isinstance(current, dict):
                    continue
                status_text = str(current.get("status") or "")
                if status_text not in {"running", "stop_requested"} or not current.get("process"):
                    continue
                pid = current.get("process_pid")
                started_at = self._parse_iso_ts(current.get("last_started_at") or current.get("started_at"))
                if started_at and (now - started_at).total_seconds() < grace_seconds:
                    continue
                if self._pid_alive(pid):
                    continue
                stopped = bool(current.get("stop_requested")) or status_text == "stop_requested"
                message = "停止请求后的独立进程已退出" if stopped else "独立进程已退出但没有写入完成状态，任务已标记失败"
                current.update(
                    {
                        "status": "stopped" if stopped else "failed",
                        "progress_message": message,
                        "progress_pct": 100 if stopped else current.get("progress_pct") or 1,
                        "last_error": "" if stopped else message,
                        "last_finished_at": _iso_now(),
                        "updated_at": _iso_now(),
                        "process": False,
                        "process_pid": "",
                        "stop_requested": False,
                        "stop_requested_at": "",
                        "stop_message": "",
                    }
                )
                stale_jobs.append({"job": name, "pid": pid, "message": message, "stopped": stopped})
            if stale_jobs:
                self._save_state(state)
        for item in stale_jobs:
            self._append_log(
                "warning" if item.get("stopped") else "error",
                item["message"],
                job=str(item["job"]),
                stage="process_stopped" if item.get("stopped") else "process_stale",
                payload={"pid": item.get("pid")},
            )
        return {"status": "ok", "stale_jobs": stale_jobs, "count": len(stale_jobs)}

    def _process_memory_snapshot(self) -> Dict[str, Any]:
        status_path = "/proc/self/status"
        if not os.path.exists(status_path):
            return {"status": "unsupported"}
        keys = {"VmRSS": "rss_kb", "VmHWM": "peak_rss_kb", "VmSize": "vms_kb", "Threads": "threads"}
        out: Dict[str, Any] = {"status": "ok"}
        try:
            with open(status_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    name, _, rest = line.partition(":")
                    if name not in keys:
                        continue
                    parts = rest.strip().split()
                    if not parts:
                        continue
                    value = float(parts[0])
                    out[keys[name]] = int(value)
                    if name != "Threads":
                        out[keys[name].replace("_kb", "_mb")] = round(value / 1024, 2)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
        return out

    def _post_job_maintenance(self, name: str) -> Dict[str, Any]:
        guard = self._memory_guard()
        aggressive = name in HEAVY_CACHE_TRIM_JOBS or bool(guard.get("pressure"))
        if not aggressive:
            return {"memory": self._process_memory_snapshot(), "cache": quant_engine.cache_stats()}
        try:
            cache_trim = quant_engine.trim_runtime_caches(aggressive=True)
        except Exception as exc:
            cache_trim = {"status": "error", "error": str(exc)}
        collected = gc.collect()
        return {
            "memory_guard": guard,
            "memory": self._process_memory_snapshot(),
            "cache_trim": cache_trim,
            "gc_collected": collected,
        }

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
                    "progress_pct": 1,
                    "progress_message": "任务已开始",
                    "last_started_at": _iso_now(),
                    "last_payload": payload,
                }
            )
            current.pop("stop_requested", None)
            current.pop("stop_requested_at", None)
            current.pop("stop_message", None)
            self._save_state(state)
        self._append_log("info", f"{_job_label(name)}已开始", job=name, stage="start", payload=payload)

    def _record_job_finish(self, name: str, started: float, result: Dict[str, Any], error: str = "") -> Dict[str, Any]:
        maintenance = self._post_job_maintenance(name)
        if isinstance(result, dict):
            result.setdefault("maintenance", maintenance)
        with self._state_lock:
            state = self._load_state()
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {})
            success_count = int(current.get("success_count", 0) or 0)
            failure_count = int(current.get("failure_count", 0) or 0)
            stopped_count = int(current.get("stopped_count", 0) or 0)
            result_status = str(result.get("status") if isinstance(result, dict) else "").strip().lower()
            stop_requested = bool(current.get("stop_requested"))
            if error:
                failure_count += 1
                status = "failed"
                progress_message = "任务失败"
            elif result_status in {"stopped", "cancelled", "canceled", "stop_requested"} or stop_requested:
                stopped_count += 1
                status = "stopped"
                progress_message = str(result.get("message") if isinstance(result, dict) else "") or "任务已停止"
            else:
                success_count += 1
                status = "ok"
                progress_message = "任务完成"
            current.update(
                {
                    "name": name,
                    "status": status,
                    "progress_pct": 100,
                    "progress_message": progress_message,
                    "last_finished_at": _iso_now(),
                    "duration_ms": round((time.time() - started) * 1000, 2),
                    "success_count": success_count,
                    "failure_count": failure_count,
                    "stopped_count": stopped_count,
                    "last_error": error,
                    "last_result": result,
                    "process": False,
                    "process_pid": "",
                    "stop_requested": False,
                    "stop_requested_at": "",
                    "stop_message": "",
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

    def update_progress(self, name: str, progress_pct: float, message: str = "", payload: Optional[Dict[str, Any]] = None) -> None:
        with self._state_lock:
            state = self._load_state()
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {"name": name})
            current["status"] = "running"
            current["progress_pct"] = max(0, min(100, round(float(progress_pct or 0), 2)))
            current["progress_message"] = str(message or current.get("progress_message") or "")
            if payload is not None:
                current["progress_payload"] = _sanitize_for_log(payload)
            current["progress_updated_at"] = _iso_now()
            self._save_state(state)

    def is_paused(self, name: str) -> bool:
        state = self._load_state()
        paused = state.get("paused_jobs") if isinstance(state.get("paused_jobs"), dict) else {}
        return bool(paused.get(name))

    def pause_job(self, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        with self._state_lock:
            state = self._load_state()
            paused = state.setdefault("paused_jobs", {})
            paused[name] = {"paused": True, "paused_at": _iso_now()}
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {"name": name})
            if current.get("status") != "running":
                current["status"] = "paused"
            current["progress_message"] = "已暂停后续调度"
            self._save_state(state)
        self._append_log("warning", f"{_job_label(name)}已暂停后续调度", job=name, stage="pause")
        return {"status": "ok", "job": name, "paused": True}

    def resume_job(self, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        with self._state_lock:
            state = self._load_state()
            paused = state.setdefault("paused_jobs", {})
            paused.pop(name, None)
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {"name": name})
            if current.get("status") == "paused":
                current["status"] = "idle"
            current["progress_message"] = "已恢复调度"
            self._save_state(state)
        self._append_log("info", f"{_job_label(name)}已恢复调度", job=name, stage="resume")
        return {"status": "ok", "job": name, "paused": False}

    def is_stop_requested(self, name: str) -> bool:
        name = str(name or "").strip()
        if not name:
            return False
        state = self._load_state()
        jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
        current = jobs.get(name) if isinstance(jobs.get(name), dict) else {}
        return bool(current.get("stop_requested"))

    def _terminate_process_tree(self, pid: Any) -> Dict[str, Any]:
        try:
            value = int(pid)
        except Exception:
            return {"status": "error", "message": "进程号无效", "pid": pid}
        if value <= 0:
            return {"status": "error", "message": "进程号无效", "pid": value}
        if not self._pid_alive(value):
            return {"status": "ok", "message": "进程已经退出", "pid": value, "alive": False}
        if os.name == "nt":
            try:
                completed = subprocess.run(
                    ["taskkill", "/PID", str(value), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                return {
                    "status": "ok" if completed.returncode == 0 else "error",
                    "pid": value,
                    "return_code": completed.returncode,
                    "stdout": (completed.stdout or "").strip()[:500],
                    "stderr": (completed.stderr or "").strip()[:500],
                    "alive": self._pid_alive(value),
                }
            except Exception as exc:
                return {"status": "error", "message": str(exc), "pid": value, "alive": self._pid_alive(value)}

        errors = []
        try:
            os.killpg(value, signal.SIGTERM)
        except Exception as exc:
            errors.append(f"killpg SIGTERM: {exc}")
            try:
                os.kill(value, signal.SIGTERM)
            except Exception as inner_exc:
                errors.append(f"kill SIGTERM: {inner_exc}")
        time.sleep(0.5)
        if self._pid_alive(value):
            try:
                os.killpg(value, signal.SIGKILL)
            except Exception as exc:
                errors.append(f"killpg SIGKILL: {exc}")
                try:
                    os.kill(value, signal.SIGKILL)
                except Exception as inner_exc:
                    errors.append(f"kill SIGKILL: {inner_exc}")
        return {
            "status": "ok" if not self._pid_alive(value) else "error",
            "pid": value,
            "alive": self._pid_alive(value),
            "errors": errors,
        }

    def stop_job(self, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            return {"status": "error", "message": "任务名称不能为空"}
        self.reconcile_process_jobs()
        with self._state_lock:
            state = self._load_state()
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {"name": name})
            state_running = current.get("status") == "running"
            process_pid = current.get("process_pid")
            process_running = bool(current.get("process") and process_pid)
            with self._lock:
                memory_running = bool(self._running.get(name))
            if not state_running and not memory_running:
                current["progress_message"] = "当前没有正在运行的任务"
                current["updated_at"] = _iso_now()
                self._save_state(state)
                return {"status": "idle", "job": name, "message": "当前没有正在运行的任务"}

            if state_running and not memory_running and not process_running:
                current.update(
                    {
                        "status": "stopped",
                        "progress_pct": 100,
                        "progress_message": "任务状态已清理：当前进程没有运行该任务",
                        "last_finished_at": _iso_now(),
                        "updated_at": _iso_now(),
                    }
                )
                self._save_state(state)
                self._append_log("warning", f"{_job_label(name)}运行状态已清理", job=name, stage="stop")
                return {"status": "stopped", "job": name, "message": "任务状态已清理：当前进程没有运行该任务"}

            current.update(
                {
                    "stop_requested": True,
                    "stop_requested_at": _iso_now(),
                    "stop_message": "已请求停止当前任务",
                    "progress_message": "已请求停止当前任务",
                    "updated_at": _iso_now(),
                }
            )
            self._save_state(state)

        if process_running:
            kill_result = self._terminate_process_tree(process_pid)
            kill_ok = kill_result.get("status") == "ok" and not bool(kill_result.get("alive"))
            with self._state_lock:
                state = self._load_state()
                jobs = state.setdefault("jobs", {})
                current = jobs.setdefault(name, {"name": name})
                stopped_count = int(current.get("stopped_count", 0) or 0) + 1
                current.update(
                    {
                        "name": name,
                        "status": "stopped" if kill_ok else "stop_requested",
                        "progress_pct": 100 if kill_ok else current.get("progress_pct", 1),
                        "progress_message": "当前任务已停止" if kill_ok else "已请求停止当前任务，请稍后查看进程状态",
                        "last_finished_at": _iso_now() if kill_ok else current.get("last_finished_at", ""),
                        "updated_at": _iso_now(),
                        "stopped_count": stopped_count,
                        "last_result": {
                            "status": "stopped" if kill_ok else "stop_requested",
                            "message": "当前任务已停止" if kill_ok else "停止信号已发送，但进程仍需稍后确认",
                            "terminate": kill_result,
                        },
                        "process": False if kill_ok else True,
                        "process_pid": "" if kill_ok else process_pid,
                        "stop_requested": False if kill_ok else True,
                        "stop_requested_at": "" if kill_ok else current.get("stop_requested_at", ""),
                        "stop_message": "" if kill_ok else "已请求停止当前任务",
                    }
                )
                self._save_state(state)
            with self._lock:
                self._running[name] = False
            self._append_log(
                "warning",
                f"{_job_label(name)}{'已停止当前独立进程' if kill_ok else '已发送停止信号'}",
                job=name,
                stage="stop",
                payload=kill_result,
            )
            return {"status": "stopped" if kill_ok else "stop_requested", "job": name, "process": True, "terminate": kill_result}

        self._append_log("warning", f"{_job_label(name)}已请求停止，将在下一个检查点结束", job=name, stage="stop")
        return {
            "status": "stop_requested",
            "job": name,
            "process": False,
            "message": "已请求停止当前任务；非独立进程任务会在下一个检查点结束",
        }

    def is_running(self, name: str) -> bool:
        name = str(name or "").strip()
        with self._lock:
            return bool(self._running.get(name))

    def run_job(self, name: str, fn: Callable[[], Dict[str, Any]], payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = payload or {}
        if self.is_paused(name):
            message = f"{_job_label(name)}已暂停，跳过本次运行"
            self._append_log("warning", message, job=name, stage="paused", payload=payload)
            return {"status": "paused", "message": message}
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

    def run_job_background(
        self,
        name: str,
        fn: Callable[[], Dict[str, Any]],
        payload: Optional[Dict[str, Any]] = None,
        message: str = "",
    ) -> Dict[str, Any]:
        name = str(name or "").strip()
        payload = payload or {}
        if self.is_paused(name):
            paused_message = f"{_job_label(name)}已暂停，跳过本次运行"
            self._append_log("warning", paused_message, job=name, stage="paused", payload=payload)
            return {"status": "paused", "job": name, "background": True, "message": paused_message}
        with self._lock:
            if self._running.get(name):
                running_message = f"{_job_label(name)}正在运行，已跳过重复请求"
                self._append_log("warning", running_message, job=name, stage="skip", payload=payload)
                return {"status": "running", "job": name, "background": True, "message": running_message}
            self._running[name] = True
        started = time.time()
        progress_message = str(message or f"{_job_label(name)}已转入后台运行").strip()
        self._record_job_start(name, payload)
        self.update_progress(name, 1, progress_message, {"background": True})

        def worker() -> None:
            try:
                result = fn()
                if not isinstance(result, dict):
                    result = {"status": "ok", "result": result}
                self._record_job_finish(name, started, result)
            except Exception as exc:
                self._record_job_finish(name, started, {}, error=str(exc))
            finally:
                with self._lock:
                    self._running[name] = False

        threading.Thread(target=worker, name=f"qt-{name}", daemon=True).start()
        return {
            "status": "running",
            "job": name,
            "background": True,
            "progress_pct": 1,
            "message": progress_message,
        }

    def _project_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    def _process_worker_script(self) -> Path:
        return self._project_root() / "scripts" / "run_quant_job.py"

    def _state_job_running(self, name: str) -> bool:
        state = self._load_state()
        jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
        current = jobs.get(name) if isinstance(jobs.get(name), dict) else {}
        return current.get("status") == "running"

    def run_job_process(
        self,
        name: str,
        payload: Optional[Dict[str, Any]] = None,
        message: str = "",
    ) -> Dict[str, Any]:
        name = str(name or "").strip()
        payload = payload or {}
        self.reconcile_process_jobs()
        if self.is_paused(name):
            paused_message = f"{_job_label(name)}已暂停，跳过本次运行"
            self._append_log("warning", paused_message, job=name, stage="paused", payload=payload)
            return {"status": "paused", "job": name, "process": True, "message": paused_message}
        with self._lock:
            if self._running.get(name) or self._state_job_running(name):
                running_message = f"{_job_label(name)}正在运行，已跳过重复请求"
                self._append_log("warning", running_message, job=name, stage="skip", payload=payload)
                return {"status": "running", "job": name, "process": True, "message": running_message}
        worker_script = self._process_worker_script()
        if not worker_script.exists():
            message_text = f"找不到独立任务进程入口：{worker_script}"
            self._append_log("error", message_text, job=name, stage="process_start", payload=payload)
            return {"status": "error", "job": name, "process": True, "message": message_text}
        process_payload = {
            "job": name,
            "payload": payload,
            "request_id": uuid.uuid4().hex[:16],
            "queued_at": _iso_now(),
        }
        progress_message = str(message or f"{_job_label(name)}已转入独立进程运行").strip()
        self._record_job_start(name, payload)
        self.update_progress(name, 1, progress_message, {"process": True})
        command = [
            sys.executable,
            str(worker_script),
            "--job",
            name,
            "--payload-json",
            json.dumps(process_payload, ensure_ascii=False, default=str),
        ]
        env = os.environ.copy()
        backend_dir = str(self._project_root() / "backend")
        env["PYTHONPATH"] = backend_dir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        popen_kwargs: Dict[str, Any] = {
            "cwd": str(self._project_root()),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            popen_kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except Exception as exc:
            self._record_job_finish(name, time.time(), {}, error=str(exc))
            return {"status": "error", "job": name, "process": True, "message": str(exc)}
        with self._state_lock:
            state = self._load_state()
            jobs = state.setdefault("jobs", {})
            current = jobs.setdefault(name, {})
            current.update(
                {
                    "process": True,
                    "process_pid": process.pid,
                    "process_request_id": process_payload["request_id"],
                    "process_started_at": _iso_now(),
                    "progress_message": progress_message,
                    "updated_at": _iso_now(),
                }
            )
            self._save_state(state)
        self._append_log(
            "info",
            f"{_job_label(name)}已启动独立进程",
            job=name,
            stage="process_start",
            payload={"pid": process.pid, "request_id": process_payload["request_id"]},
        )
        return {
            "status": "running",
            "job": name,
            "process": True,
            "process_pid": process.pid,
            "background": True,
            "progress_pct": 1,
            "message": progress_message,
        }

    def run_news_fetch(
        self,
        hours: int = 12,
        pages: int = 5,
        page_size: int = 20,
        refresh_events: bool = False,
        background: bool = False,
    ) -> Dict[str, Any]:
        payload = {"hours": hours, "pages": pages, "page_size": page_size}

        def execute() -> Dict[str, Any]:
            result = news_fetcher.run(hours=hours, pages=pages, page_size=page_size)
            if refresh_events and result.get("status") == "ok":
                quant_engine.events(force=True)
            return result

        if background:
            return self.run_job_background("news_fetch", execute, payload=payload, message="新闻抓取已转入后台运行")
        return self.run_job("news_fetch", execute, payload=payload)

    def run_market_sync(
        self,
        date: Optional[str] = None,
        source: str = "recommendations",
        max_codes: int = 80,
        force: bool = False,
        include_latest: bool = True,
        background: bool = False,
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

        def execute() -> Dict[str, Any]:
            return biying_minute_sync.sync_intraday(
                date=date,
                source="events" if explicit_codes else source,
                max_codes=max_codes,
                codes=explicit_codes,
                force=force,
                include_latest=include_latest,
            )

        if background:
            return self.run_job_background("market_sync", execute, payload=payload, message="行情同步已转入后台运行")
        return self.run_job("market_sync", execute, payload=payload)

    def run_ai_analysis(
        self,
        as_of: Optional[str] = None,
        max_items: int = 8,
        batch_size: int = 4,
        background: bool = False,
    ) -> Dict[str, Any]:
        as_of = str(as_of or _now_cn().strftime("%Y-%m-%d")).strip()
        max_items = max(1, min(int(max_items or 8), 50))
        batch_size = max(1, min(int(batch_size or 4), 10))
        payload = {"as_of": as_of, "max_items": max_items, "batch_size": batch_size}

        def execute() -> Dict[str, Any]:
            return ai_analyzer.run(as_of=as_of, max_items=max_items, batch_size=batch_size)

        if background:
            return self.run_job_background("ai_analysis", execute, payload=payload, message="AI 分析已转入后台运行")
        return self.run_job("ai_analysis", execute, payload=payload)

    def _default_backfill_start_date(self) -> str:
        return str(os.getenv("DATA_BACKFILL_START_DATE") or os.getenv("STRATEGY_REPLAY_START_DATE") or "2026-03-01").strip()

    def run_kline_fill(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_codes: int = 300,
        force: bool = False,
        background: bool = False,
    ) -> Dict[str, Any]:
        start_date = str(start_date or self._default_backfill_start_date()).strip()
        end_date = str(end_date or quant_engine.latest_event_date() or _now_cn().strftime("%Y-%m-%d")).strip()
        max_codes = max(1, min(int(max_codes or 300), 2000))
        payload = {"start_date": start_date, "end_date": end_date, "max_codes": max_codes, "force": bool(force)}

        def execute() -> Dict[str, Any]:
            return quant_engine.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=int(quant_engine.strategy_params().get("max_hold_days", 3)),
                max_codes=max_codes,
                force=force,
            )

        if background:
            return self.run_job_background("kline_fill", execute, payload=payload, message="日K补齐已转入后台运行")
        return self.run_job("kline_fill", execute, payload=payload)

    def run_lhb_sync(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_stock_days: int = 300,
        force: bool = False,
        refresh_events: bool = False,
        background: bool = False,
    ) -> Dict[str, Any]:
        start_date = str(start_date or self._default_backfill_start_date()).strip()
        end_date = str(end_date or quant_engine.latest_event_date() or _now_cn().strftime("%Y-%m-%d")).strip()
        max_stock_days = max(1, min(int(max_stock_days or 300), 2000))
        payload = {"start_date": start_date, "end_date": end_date, "max_stock_days": max_stock_days, "force": bool(force)}

        def execute() -> Dict[str, Any]:
            result = sync_lhb(
                start_date=start_date,
                end_date=end_date,
                max_stock_days=max_stock_days,
                force=force,
            )
            if refresh_events and result.get("status") == "ok":
                quant_engine.events(force=True)
            return result

        if background:
            return self.run_job_background("lhb_sync", execute, payload=payload, message="龙虎榜同步已转入后台运行")
        return self.run_job("lhb_sync", execute, payload=payload)

    def run_trade_cycle(self, date: Optional[str] = None, notify: bool = True, background: bool = False) -> Dict[str, Any]:
        date = str(date or _now_cn().strftime("%Y-%m-%d")).strip()
        payload = {"date": date, "notify": bool(notify)}

        def execute() -> Dict[str, Any]:
            replay_start = self._default_backfill_start_date()
            replay = quant_engine.rebuild_paper_from_replay(start_date=replay_start, end_date=date, mode="daily")
            portfolio = replay.get("portfolio") if isinstance(replay.get("portfolio"), dict) else quant_engine.run_paper_trading(as_of=date)
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
                "replay_start_date": replay_start,
                "replay": replay.get("timeline", {}),
            }

        if background:
            return self.run_job_background("trade_cycle", execute, payload=payload, message="交易循环已转入后台运行")
        return self.run_job("trade_cycle", execute, payload=payload)

    def run_strategy_replay(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mode: str = "intraday",
        background: bool = False,
        batch_days: Optional[int] = None,
        use_cursor: bool = False,
        process: bool = False,
    ) -> Dict[str, Any]:
        requested_start_date = str(start_date or self._default_backfill_start_date()).strip()
        requested_end_date = str(end_date or quant_engine.latest_event_date() or _now_cn().strftime("%Y-%m-%d")).strip()
        mode = str(mode or os.getenv("STRATEGY_REPLAY_MODE") or "intraday").strip().lower()
        if mode not in {"daily", "intraday"}:
            mode = "intraday"
        replay_start_date, replay_end_date, replay_cursor = self._strategy_replay_window(
            requested_start_date,
            requested_end_date,
            mode,
            batch_days=batch_days,
            use_cursor=use_cursor,
        )
        target_preview = self._strategy_replay_targets()
        target_names = [
            {
                "model_id": str(model.get("id") or ""),
                "model_name": str(model.get("name") or model.get("id") or ""),
                "source": str(model.get("source") or ""),
            }
            for model in target_preview[:12]
            if isinstance(model, dict)
        ]
        payload = {
            "start_date": replay_start_date,
            "end_date": replay_end_date,
            "requested_start_date": requested_start_date,
            "requested_end_date": requested_end_date,
            "mode": mode,
            "batch_days": replay_cursor.get("batch_days"),
            "cursor_enabled": replay_cursor.get("enabled"),
            "target_scope": "全部资金档策略和策略库模型",
            "target_count": len(target_preview),
            "target_preview": target_names,
            "description": (
                f"复盘全部资金档策略和策略库模型，共 {len(target_preview)} 个；"
                f"本批窗口 {replay_start_date} 到 {replay_end_date}，请求范围 {requested_start_date} 到 {requested_end_date}"
            ),
        }
        if process:
            return self.run_job_process("strategy_replay", payload=payload, message=payload["description"])

        def execute() -> Dict[str, Any]:
            targets = target_preview
            self.update_progress(
                "strategy_replay",
                3,
                f"准备复盘 {len(targets)} 个策略，窗口 {replay_start_date} 到 {replay_end_date}",
                payload,
            )
            fill_result = quant_engine.ensure_daily_kline_for_events(
                start_date=replay_start_date,
                end_date=replay_end_date,
                hold_days=int(quant_engine.strategy_params().get("max_hold_days", 3)),
                max_codes=self._auto_backfill_max_codes(),
                force=False,
            )
            model_results = []
            aggregate = {
                "signal_count": 0,
                "trade_count": 0,
                "position_count": 0,
                "settlement_count": 0,
                "snapshot_count": 0,
                "day_count": 0,
                "closed_trades": 0,
            }
            first_result: Dict[str, Any] = {}
            total_targets = max(1, len(targets))
            stopped = self.is_stop_requested("strategy_replay")
            for index, model in enumerate(targets, start=1):
                if stopped or self.is_stop_requested("strategy_replay"):
                    stopped = True
                    self.update_progress(
                        "strategy_replay",
                        5 + (index - 1) / total_targets * 90,
                        "已请求停止策略复盘，正在结束当前批次",
                        {
                            "processed_models": len(model_results),
                            "target_count": len(targets),
                            "start_date": replay_start_date,
                            "end_date": replay_end_date,
                        },
                    )
                    break
                params = model.get("params") if isinstance(model.get("params"), dict) else {}
                self.update_progress(
                    "strategy_replay",
                    5 + (index - 1) / total_targets * 90,
                    f"复盘 {index}/{len(targets)}：{model.get('name') or model.get('id')}，{replay_start_date} 到 {replay_end_date}",
                    {
                        "model_id": model.get("id"),
                        "model_name": model.get("name"),
                        "start_date": replay_start_date,
                        "end_date": replay_end_date,
                        "mode": mode,
                    },
                )
                with quant_engine.temporary_strategy_params(params):
                    if mode == "daily":
                        result = quant_engine.walk_forward(
                            start_date=replay_start_date,
                            end_date=replay_end_date,
                            initial_cash=params.get("account_initial_cash"),
                            max_positions=int(params.get("max_positions", 5)),
                            hold_days=int(params.get("max_hold_days", 3)),
                            top_n=int(params.get("top_n", 5)),
                            auto_fill=False,
                        )
                    else:
                        result = quant_engine.walk_forward_intraday(
                            start_date=replay_start_date,
                            end_date=replay_end_date,
                            initial_cash=params.get("account_initial_cash"),
                            max_positions=int(params.get("max_positions", 5)),
                            hold_days=int(params.get("max_hold_days", 3)),
                            top_n=int(params.get("top_n", 5)),
                            auto_fill=False,
                            use_daily_fallback=True,
                        )
                if not first_result:
                    first_result = result
                persisted = strategy_evolution.save_daily_runtime(
                    model=model,
                    params=params,
                    timeline=result,
                    start_date=replay_start_date,
                    end_date=replay_end_date,
                    mode=mode,
                    source="strategy_replay",
                )
                days = result.get("days") if isinstance(result.get("days"), list) else []
                trades = result.get("trades") if isinstance(result.get("trades"), list) else []
                row = {
                    "model_id": model.get("id"),
                    "model_name": model.get("name"),
                    "source": model.get("source"),
                    "mode": result.get("mode") or mode,
                    "start_date": result.get("start_date") or replay_start_date,
                    "end_date": result.get("end_date") or replay_end_date,
                    "initial_cash": result.get("initial_cash", 0),
                    "final_value": result.get("final_value", 0),
                    "return_pct": result.get("return_pct", 0),
                    "max_drawdown_pct": result.get("max_drawdown_pct", 0),
                    "closed_trades": result.get("closed_trades", 0),
                    "win_rate": result.get("win_rate", 0),
                    "day_count": len(days),
                    "trade_count": len(trades),
                    "runtime_persist": persisted,
                }
                model_results.append(row)
                aggregate["signal_count"] += int(persisted.get("signal_count", 0)) if isinstance(persisted, dict) else 0
                aggregate["trade_count"] += int(persisted.get("trade_count", 0)) if isinstance(persisted, dict) else 0
                aggregate["position_count"] += int(persisted.get("position_count", 0)) if isinstance(persisted, dict) else 0
                aggregate["settlement_count"] += int(persisted.get("settlement_count", 0)) if isinstance(persisted, dict) else 0
                aggregate["snapshot_count"] += int(persisted.get("snapshot_count", 0)) if isinstance(persisted, dict) else 0
                aggregate["day_count"] += len(days)
                aggregate["closed_trades"] += int(result.get("closed_trades", 0) or 0)
            result = first_result or {}
            days = result.get("days") if isinstance(result.get("days"), list) else []
            trades = result.get("trades") if isinstance(result.get("trades"), list) else []
            output = {
                "status": "stopped" if stopped else "ok",
                "message": "策略复盘已按请求停止" if stopped else "策略复盘完成",
                "mode": result.get("mode") or mode,
                "start_date": result.get("start_date") or replay_start_date,
                "end_date": result.get("end_date") or replay_end_date,
                "requested_start_date": requested_start_date,
                "requested_end_date": requested_end_date,
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
                "data_fill": fill_result,
                "model_count": len(model_results),
                "models": model_results,
                "runtime_tables": aggregate,
                "batch": replay_cursor,
                "target_scope": payload["target_scope"],
                "target_count": len(targets),
                "target_preview": target_names,
            }
            if not stopped:
                self._advance_strategy_replay_cursor(output)
            return output

        if background:
            return self.run_job_background("strategy_replay", execute, payload=payload, message=payload["description"])
        return self.run_job("strategy_replay", execute, payload=payload)

    def run_strategy_evolution(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mode: str = "intraday",
        generations: Optional[int] = None,
        population_size: Optional[int] = None,
        apply_best: Optional[bool] = None,
        process: bool = False,
    ) -> Dict[str, Any]:
        start_date = str(start_date or self._default_backfill_start_date()).strip()
        end_date = str(end_date or quant_engine.latest_event_date() or _now_cn().strftime("%Y-%m-%d")).strip()
        mode = str(mode or os.getenv("STRATEGY_EVOLUTION_MODE") or "intraday").strip().lower()
        if mode not in {"daily", "intraday"}:
            mode = "intraday"
        generations = max(1, min(int(generations or self._strategy_evolution_generations()), 30))
        population_size = max(6, min(int(population_size or self._strategy_evolution_population_size()), 80))
        apply_best = _env_bool("STRATEGY_EVOLUTION_APPLY_BEST", False) if apply_best is None else bool(apply_best)
        payload = {
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode,
            "generations": generations,
            "population_size": population_size,
            "apply_best": apply_best,
        }
        memory_guard = self._memory_guard()
        payload["memory_guard"] = memory_guard
        if memory_guard.get("pressure"):
            message = "服务器内存压力过高，已跳过策略进化"
            self._append_log("warning", message, job="strategy_evolution", stage="memory_guard", payload=payload)
            return {"status": "skipped", "message": message, "memory_guard": memory_guard}
        if process:
            return self.run_job_process("strategy_evolution", payload=payload, message="策略进化已转入独立进程运行")

        def execute() -> Dict[str, Any]:
            fill_result = quant_engine.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=int(quant_engine.strategy_params().get("max_hold_days", 3)),
                max_codes=self._auto_backfill_max_codes(),
                force=False,
            )
            result = strategy_evolution.run(
                generations=generations,
                population_size=population_size,
                start_date=start_date,
                end_date=end_date,
                apply_best=apply_best,
                mode=mode,
            )
            return {
                **result,
                "data_fill": fill_result,
            }

        return self.run_job("strategy_evolution", execute, payload=payload)

    def run_frontend_payload_precompute(
        self,
        as_of: Optional[str] = None,
        usernames: Optional[Any] = None,
        limit_users: Optional[int] = None,
        force: bool = False,
        background: bool = True,
        process: bool = False,
        lookback_days: int = 2,
        top_n: int = 30,
        limit_days: int = 120,
    ) -> Dict[str, Any]:
        limit_users = max(1, min(int(limit_users or self._frontend_payload_precompute_limit_users()), 500))
        payload = {
            "as_of": as_of,
            "usernames": usernames,
            "limit_users": limit_users,
            "force": bool(force),
            "lookback_days": max(1, min(int(lookback_days or 2), 20)),
            "top_n": max(1, min(int(top_n or 30), 100)),
            "limit_days": max(1, min(int(limit_days or 120), 500)),
        }
        if process:
            return self.run_job_process("frontend_payload_precompute", payload=payload, message="前台推荐和日计划预计算已转入独立进程运行")

        def execute() -> Dict[str, Any]:
            return precompute_frontend_payloads(**payload)

        if background:
            return self.run_job_background("frontend_payload_precompute", execute, payload=payload, message="前台推荐和日计划预计算已转入后台运行")
        return self.run_job("frontend_payload_precompute", execute, payload=payload)

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

    def _strategy_replay_batch_days(self) -> int:
        return max(1, min(_env_int("QT_STRATEGY_REPLAY_BATCH_DAYS", 15), 366))

    def _strategy_replay_max_models(self) -> int:
        return max(1, min(_env_int("QT_STRATEGY_REPLAY_MAX_MODELS", 24), 200))

    def _frontend_payload_precompute_interval_seconds(self) -> int:
        return _env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_INTERVAL_SECONDS", 1800)

    def _frontend_payload_precompute_limit_users(self) -> int:
        return max(1, min(_env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS", 50), 500))

    def _parse_date(self, value: str) -> Optional[datetime]:
        try:
            return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
        except Exception:
            return None

    def _format_date(self, value: datetime) -> str:
        return value.strftime("%Y-%m-%d")

    def _strategy_replay_window(
        self,
        start_date: str,
        end_date: str,
        mode: str,
        batch_days: Optional[int] = None,
        use_cursor: bool = False,
    ) -> tuple[str, str, Dict[str, Any]]:
        batch_days = max(1, min(int(batch_days or self._strategy_replay_batch_days()), 366))
        start_dt = self._parse_date(start_date)
        end_dt = self._parse_date(end_date)
        if not start_dt or not end_dt or start_dt > end_dt:
            return start_date, end_date, {
                "enabled": False,
                "reason": "invalid_date_range",
                "batch_days": batch_days,
                "requested_start_date": start_date,
                "requested_end_date": end_date,
                "mode": mode,
            }

        cursor_key = f"{start_date}|{end_date}|{mode}|{self._strategy_replay_max_models()}"
        current_dt = start_dt
        if use_cursor:
            state = self._load_state()
            cursor = state.get("strategy_replay_cursor") if isinstance(state.get("strategy_replay_cursor"), dict) else {}
            next_start = str(cursor.get("next_start_date") or "")
            next_dt = self._parse_date(next_start)
            if cursor.get("key") == cursor_key and next_dt and start_dt <= next_dt <= end_dt:
                current_dt = next_dt
        batch_end_dt = min(end_dt, current_dt + timedelta(days=batch_days - 1))
        next_dt = batch_end_dt + timedelta(days=1)
        next_start = self._format_date(start_dt if next_dt > end_dt else next_dt)
        return self._format_date(current_dt), self._format_date(batch_end_dt), {
            "enabled": bool(use_cursor),
            "key": cursor_key,
            "batch_days": batch_days,
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "mode": mode,
            "start_date": self._format_date(current_dt),
            "end_date": self._format_date(batch_end_dt),
            "next_start_date": next_start,
            "completed_range": next_dt > end_dt,
        }

    def _advance_strategy_replay_cursor(self, result: Dict[str, Any]) -> None:
        batch = result.get("batch") if isinstance(result.get("batch"), dict) else {}
        if not batch.get("enabled"):
            return
        with self._state_lock:
            state = self._load_state()
            state["strategy_replay_cursor"] = {
                "key": batch.get("key"),
                "mode": batch.get("mode"),
                "requested_start_date": batch.get("requested_start_date"),
                "requested_end_date": batch.get("requested_end_date"),
                "last_start_date": batch.get("start_date"),
                "last_end_date": batch.get("end_date"),
                "next_start_date": batch.get("next_start_date"),
                "batch_days": batch.get("batch_days"),
                "completed_range": bool(batch.get("completed_range")),
                "updated_at": _iso_now(),
            }
            self._save_state(state)

    def _strategy_replay_targets(self) -> list[Dict[str, Any]]:
        max_models = self._strategy_replay_max_models()
        base_params = quant_engine.strategy_params()
        targets: list[Dict[str, Any]] = []
        seen: set[str] = set()

        def add(item: Dict[str, Any]) -> None:
            if not isinstance(item, dict):
                return
            model_id = str(item.get("id") or item.get("model_id") or "").strip()
            if not model_id or model_id in seen or len(targets) >= max_models:
                return
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
            targets.append({**item, "id": model_id, "params": quant_engine.strategy_params(params)})
            seen.add(model_id)

        for preset in capital_presets(base_params):
            add(preset)
        models_payload = strategy_evolution.models(limit=max_models, include_records=False)
        for model in models_payload.get("items") if isinstance(models_payload.get("items"), list) else []:
            if isinstance(model, dict) and model.get("reusable", True):
                add(model)
        if not targets:
            add(
                {
                    "id": "active",
                    "name": "系统默认基础参数（非跟随策略）",
                    "source": "baseline",
                    "reusable": False,
                    "params": base_params,
                }
            )
        return targets

    def _strategy_evolution_interval_seconds(self) -> int:
        return _env_int("STRATEGY_EVOLUTION_INTERVAL_SECONDS", 6 * 3600)

    def _strategy_evolution_generations(self) -> int:
        max_generations = max(1, min(_env_int("QT_STRATEGY_EVOLUTION_MAX_GENERATIONS", 8), 30))
        return max(1, min(_env_int("STRATEGY_EVOLUTION_GENERATIONS", 1), max_generations))

    def _strategy_evolution_population_size(self) -> int:
        max_population = max(6, min(_env_int("QT_STRATEGY_EVOLUTION_MAX_POPULATION", 32), 80))
        return max(6, min(_env_int("STRATEGY_EVOLUTION_POPULATION_SIZE", 16), max_population))

    def _kline_fill_interval_seconds(self) -> int:
        return _env_int("KLINE_FILL_INTERVAL_SECONDS", 6 * 3600)

    def _lhb_sync_interval_seconds(self) -> int:
        return _env_int("LHB_SYNC_INTERVAL_SECONDS", 12 * 3600)

    def _auto_backfill_max_codes(self) -> int:
        return max(1, min(_env_int("DATA_BACKFILL_MAX_CODES", 160), 2000))

    def _memory_guard(self) -> Dict[str, Any]:
        threshold_pct = max(50.0, min(_env_float("QT_MEMORY_GUARD_PERCENT", 88.0), 99.0))
        min_available_mb = max(0.0, _env_float("QT_MEMORY_GUARD_AVAILABLE_MB", 1024.0))
        payload: Dict[str, Any] = {
            "enabled": _env_bool("QT_MEMORY_GUARD_ENABLED", True),
            "threshold_pct": threshold_pct,
            "min_available_mb": min_available_mb,
            "pressure": False,
        }
        if not payload["enabled"]:
            return payload
        meminfo_path = "/proc/meminfo"
        if not os.path.exists(meminfo_path):
            payload["status"] = "unsupported"
            return payload
        try:
            values: Dict[str, float] = {}
            with open(meminfo_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    parts = line.split()
                    if len(parts) >= 2:
                        values[parts[0].rstrip(":")] = float(parts[1])
            total_kb = values.get("MemTotal", 0.0)
            available_kb = values.get("MemAvailable", values.get("MemFree", 0.0))
            if total_kb <= 0:
                payload["status"] = "unknown"
                return payload
            used_pct = max(0.0, min(100.0, (1 - available_kb / total_kb) * 100))
            available_mb = available_kb / 1024
            payload.update(
                {
                    "status": "ok",
                    "used_pct": round(used_pct, 2),
                    "available_mb": round(available_mb, 2),
                    "pressure": used_pct >= threshold_pct or available_mb < min_available_mb,
                }
            )
        except Exception as exc:
            payload["status"] = "error"
            payload["error"] = str(exc)
        return payload

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
        next_kline_fill = time.time() + 5
        next_lhb_sync = time.time() + 20
        next_market_sync = time.time() + 40
        next_trade_cycle = time.time() + 50
        next_strategy_replay = time.time() + 70
        next_strategy_evolution = time.time() + 90
        next_frontend_payload_precompute = time.time() + 100
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
            if now_ts >= next_kline_fill:
                await asyncio.to_thread(
                    self.run_kline_fill,
                    self._default_backfill_start_date(),
                    os.getenv("DATA_BACKFILL_END_DATE") or None,
                    self._auto_backfill_max_codes(),
                    False,
                )
                next_kline_fill = time.time() + self._kline_fill_interval_seconds()
                ran_task = True
            if now_ts >= next_lhb_sync:
                await asyncio.to_thread(
                    self.run_lhb_sync,
                    self._default_backfill_start_date(),
                    os.getenv("DATA_BACKFILL_END_DATE") or None,
                    self._auto_backfill_max_codes(),
                    False,
                )
                next_lhb_sync = time.time() + self._lhb_sync_interval_seconds()
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
            if _env_bool("STRATEGY_REPLAY_ENABLED", False) and now_ts >= next_strategy_replay:
                await asyncio.to_thread(
                    self.run_strategy_replay,
                    None,
                    None,
                    os.getenv("STRATEGY_REPLAY_MODE", "intraday"),
                    False,
                    self._strategy_replay_batch_days(),
                    True,
                )
                next_strategy_replay = time.time() + self._strategy_replay_interval_seconds()
                ran_task = True
            elif not _env_bool("STRATEGY_REPLAY_ENABLED", False):
                next_strategy_replay = time.time() + self._strategy_replay_interval_seconds()
            if _env_bool("STRATEGY_EVOLUTION_ENABLED", False) and now_ts >= next_strategy_evolution:
                evolution_state = strategy_evolution.status()
                if evolution_state.get("status") not in {"running", "paused"}:
                    await asyncio.to_thread(
                        self.run_strategy_evolution,
                        None,
                        None,
                        os.getenv("STRATEGY_EVOLUTION_MODE", "intraday"),
                        self._strategy_evolution_generations(),
                        self._strategy_evolution_population_size(),
                        None,
                    )
                    next_strategy_evolution = time.time() + self._strategy_evolution_interval_seconds()
                    ran_task = True
                else:
                    next_strategy_evolution = time.time() + 300
            elif not _env_bool("STRATEGY_EVOLUTION_ENABLED", False):
                next_strategy_evolution = time.time() + self._strategy_evolution_interval_seconds()
            if _env_bool("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", True) and now_ts >= next_frontend_payload_precompute:
                await asyncio.to_thread(
                    self.run_frontend_payload_precompute,
                    None,
                    None,
                    self._frontend_payload_precompute_limit_users(),
                    False,
                    False,
                    _env_bool("QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED", True),
                )
                next_frontend_payload_precompute = time.time() + self._frontend_payload_precompute_interval_seconds()
                ran_task = True
            elif not _env_bool("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", True):
                next_frontend_payload_precompute = time.time() + self._frontend_payload_precompute_interval_seconds()
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
                    "next_kline_fill_at": datetime.fromtimestamp(next_kline_fill, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "kline_fill_interval_seconds": self._kline_fill_interval_seconds(),
                    "next_lhb_sync_at": datetime.fromtimestamp(next_lhb_sync, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "lhb_sync_interval_seconds": self._lhb_sync_interval_seconds(),
                    "data_backfill_start_date": self._default_backfill_start_date(),
                    "data_backfill_end_date": os.getenv("DATA_BACKFILL_END_DATE") or "",
                    "data_backfill_max_codes": self._auto_backfill_max_codes(),
                    "next_trade_cycle_at": datetime.fromtimestamp(next_trade_cycle, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "trade_interval_seconds": self._trade_interval_seconds(),
                    "next_strategy_replay_at": datetime.fromtimestamp(next_strategy_replay, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "strategy_replay_interval_seconds": self._strategy_replay_interval_seconds(),
                    "strategy_replay_start_date": self._default_backfill_start_date(),
                    "strategy_replay_batch_days": self._strategy_replay_batch_days(),
                    "strategy_replay_enabled": _env_bool("STRATEGY_REPLAY_ENABLED", False),
                    "next_strategy_evolution_at": datetime.fromtimestamp(next_strategy_evolution, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "strategy_evolution_interval_seconds": self._strategy_evolution_interval_seconds(),
                    "strategy_evolution_start_date": self._default_backfill_start_date(),
                    "strategy_evolution_generations": self._strategy_evolution_generations(),
                    "strategy_evolution_population_size": self._strategy_evolution_population_size(),
                    "strategy_evolution_enabled": _env_bool("STRATEGY_EVOLUTION_ENABLED", False),
                    "next_frontend_payload_precompute_at": datetime.fromtimestamp(next_frontend_payload_precompute, ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                    "frontend_payload_precompute_interval_seconds": self._frontend_payload_precompute_interval_seconds(),
                    "frontend_payload_precompute_limit_users": self._frontend_payload_precompute_limit_users(),
                    "frontend_payload_precompute_enabled": _env_bool("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", True),
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
