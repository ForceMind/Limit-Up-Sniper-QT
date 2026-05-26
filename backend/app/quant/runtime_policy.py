from __future__ import annotations

import os
from typing import Any, Dict, Iterable


DATA_COLLECTION_JOBS = (
    "news_fetch",
    "ai_analysis",
    "market_sync",
    "kline_fill",
    "lhb_sync",
)
DAILY_STRATEGY_JOBS = ("strategy_daily_refresh", "trade_cycle")
USER_RUNTIME_JOBS = ("frontend_account_precompute",)
FRONTEND_CACHE_JOBS = ("frontend_payload_precompute",)
RESEARCH_JOBS = (
    "strategy_replay",
    "strategy_evolution",
    "model_backtest",
    "quant_timeline",
    "quant_backtest",
    "fit_strategy",
)
DIAGNOSTIC_JOBS = ("data_coverage",)
SYSTEM_MAINTENANCE_JOBS = (
    "system_startup",
    "admin_backup",
    "admin_data_export",
    "admin_data_import",
    "admin_data_clear_sample",
    "admin_restart",
    "admin_config",
)

DAILY_RUNTIME_JOBS = DATA_COLLECTION_JOBS + DAILY_STRATEGY_JOBS
RESEARCH_PROCESS_JOBS = RESEARCH_JOBS
FRONTEND_RUNTIME_PROCESS_JOBS = FRONTEND_CACHE_JOBS + USER_RUNTIME_JOBS
HEAVY_PROCESS_JOBS = RESEARCH_PROCESS_JOBS
HEAVY_CACHE_TRIM_JOBS = tuple(
    dict.fromkeys(
        DATA_COLLECTION_JOBS
        + DAILY_STRATEGY_JOBS
        + RESEARCH_JOBS
        + FRONTEND_CACHE_JOBS
        + USER_RUNTIME_JOBS
        + DIAGNOSTIC_JOBS
        + ("system_startup",)
    )
)
STOP_CHECKPOINT_JOBS = ("strategy_replay", "strategy_daily_refresh", "system_startup")
PROCESS_TERMINABLE_JOBS = tuple(
    dict.fromkeys(
        HEAVY_PROCESS_JOBS
        + FRONTEND_RUNTIME_PROCESS_JOBS
        + ("strategy_daily_refresh", "system_startup")
    )
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "") or "").strip().lower()
    if not value:
        return default
    return value not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 200) -> int:
    try:
        value = int(float(os.getenv(name, "") or default))
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1000000.0) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def target_strategy_count() -> int:
    return _env_int("QT_TARGET_STRATEGY_COUNT", 20, minimum=1, maximum=200)


def research_tasks_manual_only() -> bool:
    return _env_bool("QT_RESEARCH_TASKS_MANUAL_ONLY", True)


def heavy_job_cpu_threads() -> int:
    default = max(1, min((os.cpu_count() or 2) - 1, 8))
    return _env_int("QT_HEAVY_JOB_CPU_THREADS", default, minimum=1, maximum=64)


def frontend_runtime_cpu_threads() -> int:
    default = max(1, min(os.cpu_count() or 2, 2))
    return _env_int("QT_FRONT_RUNTIME_JOB_CPU_THREADS", default, minimum=1, maximum=16)


def daily_strategy_cpu_threads() -> int:
    default = max(1, min(os.cpu_count() or 2, 2))
    return _env_int("QT_DAILY_STRATEGY_CPU_THREADS", default, minimum=1, maximum=16)


def heavy_resource_controls(heavy_process_limit: int) -> Dict[str, Any]:
    limit = _env_int("QT_HEAVY_JOB_MAX_CONCURRENT", int(heavy_process_limit or 1), minimum=1, maximum=8)
    return {
        "process_isolation_required": True,
        "job_class": "research_manual",
        "max_concurrent": limit,
        "cpu_threads": heavy_job_cpu_threads(),
        "cpu_env_vars": ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"],
        "memory_guard_enabled": _env_bool("QT_MEMORY_GUARD_ENABLED", True),
        "memory_guard_threshold_pct": _env_float("QT_MEMORY_GUARD_PERCENT", 88.0, minimum=50.0, maximum=99.0),
        "memory_guard_min_available_mb": _env_float("QT_MEMORY_GUARD_AVAILABLE_MB", 1024.0, minimum=0.0, maximum=1048576.0),
        "process_start_grace_seconds": _env_float("QT_JOB_PROCESS_START_GRACE_SECONDS", 8.0, minimum=1.0, maximum=120.0),
        "manual_only": research_tasks_manual_only(),
    }


def frontend_runtime_resource_controls(frontend_runtime_process_limit: int) -> Dict[str, Any]:
    limit = _env_int(
        "QT_FRONT_RUNTIME_JOB_MAX_CONCURRENT",
        int(frontend_runtime_process_limit or 1),
        minimum=1,
        maximum=4,
    )
    return {
        "process_isolation_required": True,
        "job_class": "frontend_runtime",
        "max_concurrent": limit,
        "cpu_threads": frontend_runtime_cpu_threads(),
        "cpu_env_vars": ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"],
        "memory_guard_enabled": _env_bool("QT_MEMORY_GUARD_ENABLED", True),
        "memory_guard_threshold_pct": _env_float("QT_MEMORY_GUARD_PERCENT", 88.0, minimum=50.0, maximum=99.0),
        "memory_guard_min_available_mb": _env_float("QT_MEMORY_GUARD_AVAILABLE_MB", 1024.0, minimum=0.0, maximum=1048576.0),
        "manual_only": False,
    }


def daily_strategy_resource_controls() -> Dict[str, Any]:
    return {
        "process_isolation_required": True,
        "job_class": "daily_strategy_runtime",
        "max_concurrent": 1,
        "cpu_threads": daily_strategy_cpu_threads(),
        "cpu_env_vars": ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"],
        "memory_guard_enabled": _env_bool("QT_MEMORY_GUARD_ENABLED", True),
        "memory_guard_threshold_pct": _env_float("QT_MEMORY_GUARD_PERCENT", 88.0, minimum=50.0, maximum=99.0),
        "memory_guard_min_available_mb": _env_float("QT_MEMORY_GUARD_AVAILABLE_MB", 1024.0, minimum=0.0, maximum=1048576.0),
        "manual_only": False,
    }


def job_zone(job_name: str) -> str:
    name = str(job_name or "").strip()
    if name in DATA_COLLECTION_JOBS:
        return "data_collection"
    if name in DAILY_STRATEGY_JOBS:
        return "daily_strategy_runtime"
    if name in USER_RUNTIME_JOBS:
        return "user_follow_runtime"
    if name in FRONTEND_CACHE_JOBS:
        return "frontend_cache"
    if name in RESEARCH_JOBS:
        return "research_manual"
    if name in DIAGNOSTIC_JOBS:
        return "diagnostics"
    if name in SYSTEM_MAINTENANCE_JOBS:
        return "system_maintenance"
    return "other"


def job_stop_policy(job_name: str) -> Dict[str, Any]:
    name = str(job_name or "").strip()
    zone = job_zone(name)
    checkpoint_supported = name in STOP_CHECKPOINT_JOBS
    process_supported = name in PROCESS_TERMINABLE_JOBS
    stop_allowed = process_supported or checkpoint_supported
    return {
        "job": name,
        "zone": zone,
        "stop_allowed": stop_allowed,
        "manual_research": name in RESEARCH_JOBS,
        "daily_runtime": name in DAILY_RUNTIME_JOBS,
        "process_termination_supported": process_supported,
        "checkpoint_stop_supported": checkpoint_supported,
        "stop_scope": "current_process" if process_supported else ("checkpoint" if checkpoint_supported else "unsupported"),
    }


def jobs_by_zone(job_names: Iterable[str]) -> Dict[str, list[str]]:
    zones: Dict[str, list[str]] = {}
    for name in job_names:
        zone = job_zone(name)
        zones.setdefault(zone, []).append(str(name))
    return {key: sorted(value) for key, value in sorted(zones.items())}


def runtime_architecture_policy(
    *,
    heavy_process_limit: int,
    running_heavy_jobs: list[Dict[str, Any]] | None = None,
    frontend_runtime_process_limit: int | None = None,
    running_frontend_runtime_jobs: list[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    running = running_heavy_jobs if isinstance(running_heavy_jobs, list) else []
    frontend_limit = int(frontend_runtime_process_limit or 1)
    running_frontend = running_frontend_runtime_jobs if isinstance(running_frontend_runtime_jobs, list) else []
    return {
        "target_strategy_count": target_strategy_count(),
        "research_tasks_manual_only": research_tasks_manual_only(),
        "trade_cycle_legacy_paper_enabled": _env_bool("QT_TRADE_CYCLE_LEGACY_PAPER_ENABLED", False),
        "daily_runtime_jobs": list(DAILY_RUNTIME_JOBS),
        "data_collection_jobs": list(DATA_COLLECTION_JOBS),
        "daily_strategy_jobs": list(DAILY_STRATEGY_JOBS),
        "user_runtime_jobs": list(USER_RUNTIME_JOBS),
        "frontend_cache_jobs": list(FRONTEND_CACHE_JOBS),
        "research_manual_jobs": list(RESEARCH_JOBS),
        "diagnostic_jobs": list(DIAGNOSTIC_JOBS),
        "research_process_jobs": list(RESEARCH_PROCESS_JOBS),
        "frontend_runtime_process_jobs": list(FRONTEND_RUNTIME_PROCESS_JOBS),
        "heavy_process_jobs": list(HEAVY_PROCESS_JOBS),
        "heavy_process_limit": heavy_process_limit,
        "running_heavy_jobs": running,
        "frontend_runtime_process_limit": frontend_limit,
        "running_frontend_runtime_jobs": running_frontend,
        "resource_controls": heavy_resource_controls(heavy_process_limit),
        "frontend_runtime_resource_controls": frontend_runtime_resource_controls(frontend_limit),
        "daily_strategy_resource_controls": daily_strategy_resource_controls(),
        "process_pools": {
            "research_manual": {
                "jobs": list(RESEARCH_PROCESS_JOBS),
                "max_concurrent": int(heavy_process_limit or 1),
                "running_jobs": running,
                "resource_controls": heavy_resource_controls(heavy_process_limit),
            },
            "frontend_runtime": {
                "jobs": list(FRONTEND_RUNTIME_PROCESS_JOBS),
                "max_concurrent": frontend_limit,
                "running_jobs": running_frontend,
                "resource_controls": frontend_runtime_resource_controls(frontend_limit),
            },
            "daily_strategy_runtime": {
                "jobs": ["strategy_daily_refresh"],
                "max_concurrent": 1,
                "resource_controls": daily_strategy_resource_controls(),
            },
        },
        "stop_controls": {
            name: job_stop_policy(name)
            for name in dict.fromkeys(
                DAILY_RUNTIME_JOBS
                + RESEARCH_JOBS
                + FRONTEND_CACHE_JOBS
                + USER_RUNTIME_JOBS
                + DIAGNOSTIC_JOBS
                + SYSTEM_MAINTENANCE_JOBS
            )
        },
        "invariants": [
            "日常链路只负责新闻、AI、行情、补数、交易循环和必要缓存",
            "策略复盘、策略进化、模型回测、通用回测和参数拟合默认只手动触发",
            "前台读取用户跟随结果时不应同步触发训练、进化或大回测",
            "重任务受独立进程和并发闸门控制，避免影响 API 进程",
            "自动日常策略刷新使用独立轻量资源预算，并默认避让正在运行的手动重任务",
        ],
        "zones": {
            "data_collection": {
                "label": "公共数据层",
                "jobs": list(DATA_COLLECTION_JOBS),
            },
            "daily_strategy_runtime": {
                "label": "日常策略运行层",
                "jobs": list(DAILY_STRATEGY_JOBS),
            },
            "user_follow_runtime": {
                "label": "用户跟随层",
                "jobs": list(USER_RUNTIME_JOBS),
            },
            "research_manual": {
                "label": "研究优化层",
                "jobs": list(RESEARCH_JOBS),
            },
            "frontend_cache": {
                "label": "前台缓存层",
                "jobs": list(FRONTEND_CACHE_JOBS),
            },
            "diagnostics": {
                "label": "诊断层",
                "jobs": list(DIAGNOSTIC_JOBS),
            },
        },
    }
