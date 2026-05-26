from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _coerce_int(value: Any, default: int = 0, *, minimum: int = 0, maximum: int = 1000000) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = default
    return max(int(minimum), min(int(parsed), int(maximum)))


def _clamp_seconds(value: Any, *, minimum: int = 60, maximum: int = 86400) -> int:
    seconds = _coerce_float(value, float(minimum))
    return int(max(float(minimum), min(float(seconds), float(maximum))))


def _payload_date_span_days(payload: Dict[str, Any]) -> int:
    start_text = str(payload.get("start_date") or payload.get("requested_start_date") or payload.get("as_of") or "").strip()[:10]
    end_text = str(payload.get("end_date") or payload.get("requested_end_date") or payload.get("as_of") or "").strip()[:10]
    if not start_text or not end_text:
        return 1
    try:
        start = datetime.strptime(start_text, "%Y-%m-%d")
        end = datetime.strptime(end_text, "%Y-%m-%d")
    except Exception:
        return 1
    return max(1, (end - start).days + 1)


def job_initial_estimate_seconds(job_name: str, payload: Any) -> int:
    """Return a conservative initial wall-clock estimate for observable manual jobs."""
    name = str(job_name or "").strip()
    data = payload if isinstance(payload, dict) else {}
    days = _payload_date_span_days(data)
    mode = str(data.get("mode") or "").strip().lower()
    intraday_factor = 1.5 if mode == "intraday" else 1.0

    if name == "strategy_replay":
        targets = _coerce_int(
            data.get("target_count") or data.get("target_strategy_count") or data.get("max_runtime_models"),
            20,
            minimum=1,
            maximum=500,
        )
        return _clamp_seconds(180 + days * targets * 8 * intraday_factor, minimum=300, maximum=86400)

    if name == "strategy_daily_refresh":
        targets = _coerce_int(data.get("target_count") or data.get("target_strategy_count"), 20, minimum=1, maximum=200)
        return _clamp_seconds(60 + targets * 6 * intraday_factor, minimum=120, maximum=7200)

    if name == "strategy_evolution":
        generations = _coerce_int(data.get("generations"), 8, minimum=1, maximum=100)
        population = _coerce_int(data.get("population_size"), 24, minimum=1, maximum=500)
        return _clamp_seconds(300 + generations * population * 10 * intraday_factor + days * 20, minimum=600, maximum=86400)

    if name in {"model_backtest", "quant_timeline", "quant_backtest"}:
        return _clamp_seconds(180 + days * 6 * intraday_factor, minimum=300, maximum=21600)

    if name == "fit_strategy":
        return _clamp_seconds(600 + days * 15 * intraday_factor, minimum=900, maximum=43200)

    if name == "frontend_payload_precompute":
        max_seconds = _coerce_int(data.get("max_seconds"), 0, minimum=0, maximum=86400)
        if max_seconds > 0:
            return _clamp_seconds(max_seconds, minimum=1, maximum=86400)
        users = _coerce_int(data.get("limit_users"), 50, minimum=1, maximum=5000)
        return _clamp_seconds(30 + users * 3, minimum=60, maximum=3600)

    if name == "frontend_account_precompute":
        users = _coerce_int(data.get("limit_users") or data.get("limit"), 50, minimum=1, maximum=5000)
        return _clamp_seconds(60 + users * 8, minimum=120, maximum=7200)

    return 0


def parse_iso_ts(value: Any, *, timezone: str = "Asia/Shanghai") -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
        return parsed
    except Exception:
        return None


def job_timing_snapshot(
    item: Any,
    *,
    now: Optional[datetime] = None,
    timezone: str = "Asia/Shanghai",
) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    started_at = item.get("last_started_at") or item.get("started_at") or item.get("process_started_at")
    started = parse_iso_ts(started_at, timezone=timezone)
    if not started:
        return {}
    effective_now = now or datetime.now(ZoneInfo(timezone))
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=ZoneInfo(timezone))
    elapsed = max(0.0, (effective_now - started).total_seconds())
    try:
        progress = float(item.get("progress_pct") or 0)
    except Exception:
        progress = 0.0
    progress = max(0.0, min(progress, 100.0))
    payload: Dict[str, Any] = {
        "elapsed_seconds": round(elapsed, 1),
        "started_at_effective": str(started_at or ""),
    }
    explicit_estimate = _coerce_float(item.get("estimated_total_seconds"), 0.0)
    if 1.0 < progress < 100.0:
        estimated_total = elapsed * 100.0 / progress
        remaining = max(0.0, estimated_total - elapsed)
        payload["estimated_total_seconds"] = round(estimated_total, 1)
        payload["eta_seconds"] = round(remaining, 1)
        payload["eta_at"] = (effective_now + timedelta(seconds=remaining)).isoformat(timespec="seconds")
        payload["estimate_source"] = "progress"
    elif progress < 100.0 and explicit_estimate > 0:
        remaining = max(0.0, explicit_estimate - elapsed)
        payload["estimated_total_seconds"] = round(explicit_estimate, 1)
        payload["eta_seconds"] = round(remaining, 1)
        payload["eta_at"] = (started + timedelta(seconds=explicit_estimate)).isoformat(timespec="seconds")
        payload["estimate_source"] = str(item.get("estimate_source") or "initial_job_profile")
        if elapsed > explicit_estimate:
            payload["estimate_overdue_seconds"] = round(elapsed - explicit_estimate, 1)
    elif progress >= 100.0:
        payload["estimated_total_seconds"] = round(elapsed, 1)
        payload["eta_seconds"] = 0
        payload["eta_at"] = effective_now.isoformat(timespec="seconds")
        payload["estimate_source"] = "actual_duration"
    return payload


def heavy_process_slots_payload(
    *,
    running_jobs: Iterable[Dict[str, Any]],
    max_concurrent: int,
    limited_jobs: Iterable[str],
    resource_controls: Dict[str, Any],
) -> Dict[str, Any]:
    running = list(running_jobs)
    limit = max(1, int(max_concurrent or 1))
    return {
        "enabled": True,
        "max_concurrent": limit,
        "running_count": len(running),
        "available": max(0, limit - len(running)),
        "running_jobs": running,
        "limited_jobs": sorted(str(name) for name in limited_jobs),
        "resource_controls": resource_controls,
    }


def heavy_process_admission_payload(
    *,
    name: str,
    running_jobs: Iterable[Dict[str, Any]],
    max_concurrent: int,
    resource_controls: Dict[str, Any],
    message: str,
) -> Optional[Dict[str, Any]]:
    running = list(running_jobs)
    limit = max(1, int(max_concurrent or 1))
    if len(running) < limit:
        return None
    return {
        "status": "busy",
        "job": name,
        "process": True,
        "background": True,
        "message": message,
        "heavy_process_limit": limit,
        "running_heavy_jobs": running,
        "resource_controls": resource_controls,
    }


def apply_resource_controls_to_env(env: Dict[str, str], resource_controls: Dict[str, Any]) -> Dict[str, str]:
    if not resource_controls:
        return env
    cpu_threads = str(resource_controls.get("cpu_threads") or "1")
    for key in resource_controls.get("cpu_env_vars") or []:
        env[str(key)] = cpu_threads
    env["QT_HEAVY_JOB_RESOURCE_CONTROLS"] = json.dumps(resource_controls, ensure_ascii=False, default=str)
    return env


def build_process_command(
    *,
    python_executable: str,
    worker_script: Path,
    job_name: str,
    process_payload: Dict[str, Any],
) -> list[str]:
    return [
        str(python_executable),
        str(worker_script),
        "--job",
        str(job_name),
        "--payload-json",
        json.dumps(process_payload, ensure_ascii=False, default=str),
    ]


def build_process_env(
    *,
    base_env: Dict[str, str],
    backend_dir: Path,
    resource_controls: Dict[str, Any],
) -> Dict[str, str]:
    env = dict(base_env)
    backend_path = str(backend_dir)
    env["PYTHONPATH"] = backend_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    apply_resource_controls_to_env(env, resource_controls)
    return env


def build_process_popen_kwargs(
    *,
    project_root: Path,
    env: Dict[str, str],
    windows_no_window_flag: Optional[int] = None,
) -> Dict[str, Any]:
    popen_kwargs: Dict[str, Any] = {
        "cwd": str(project_root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if windows_no_window_flag is not None:
        popen_kwargs["creationflags"] = windows_no_window_flag
    else:
        popen_kwargs["start_new_session"] = True
    return popen_kwargs


def process_memory_snapshot(
    status_path: str = "/proc/self/status",
    *,
    pid: Any = None,
    proc_root: str = "/proc",
) -> Dict[str, Any]:
    clean_pid = 0
    if pid is not None:
        clean_pid = _coerce_int(pid, 0, minimum=0, maximum=99999999)
        if clean_pid <= 0:
            return {"status": "invalid_pid", "pid": pid}
        if os.name != "posix":
            return {"status": "unsupported", "pid": clean_pid}
        status_path = str(Path(proc_root) / str(clean_pid) / "status")
    if not os.path.exists(status_path):
        payload: Dict[str, Any] = {"status": "unsupported"}
        if clean_pid:
            payload["pid"] = clean_pid
        return payload
    keys = {"VmRSS": "rss_kb", "VmHWM": "peak_rss_kb", "VmSize": "vms_kb", "Threads": "threads"}
    out: Dict[str, Any] = {"status": "ok"}
    if clean_pid:
        out["pid"] = clean_pid
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


def memory_guard_snapshot(
    *,
    enabled: bool,
    threshold_pct: float,
    min_available_mb: float,
    meminfo_path: str = "/proc/meminfo",
) -> Dict[str, Any]:
    threshold = max(50.0, min(float(threshold_pct or 88.0), 99.0))
    min_available = max(0.0, float(min_available_mb or 0.0))
    payload: Dict[str, Any] = {
        "enabled": bool(enabled),
        "threshold_pct": threshold,
        "min_available_mb": min_available,
        "pressure": False,
    }
    if not payload["enabled"]:
        return payload
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
                "pressure": used_pct >= threshold or available_mb < min_available,
            }
        )
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = str(exc)
    return payload
