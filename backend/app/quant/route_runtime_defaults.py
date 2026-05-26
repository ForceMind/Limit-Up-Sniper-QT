from __future__ import annotations

from typing import Callable, Dict


EnvFlag = Callable[[str, bool], bool]


class RouteRuntimeDefaults:
    """Centralizes route-level defaults for light reads and manual heavy jobs."""

    def __init__(self, *, env_flag: EnvFlag) -> None:
        self._env_flag = env_flag

    def frontend_runtime_kwargs(self) -> Dict[str, bool]:
        return {
            "account_defer_default": self._env_flag("QT_FRONT_ACCOUNT_DEFER_MISSES", True),
        }

    def frontend_signal_kwargs(self) -> Dict[str, bool]:
        return {
            "payload_defer_default": self._env_flag("QT_FRONT_PAYLOAD_DEFER_MISSES", True),
        }

    def quant_strategy_kwargs(self) -> Dict[str, bool]:
        return {
            "fit_strategy_defer_default": self._env_flag("QT_FIT_STRATEGY_DEFER_MISSES", True),
            "fit_strategy_process_default": self._env_flag("QT_FIT_STRATEGY_PROCESS_ENABLED", True),
            "model_backtest_defer_default": self._env_flag("QT_MODEL_BACKTEST_DEFER_RECOMPUTE", True),
            "model_backtest_process_default": self._env_flag("QT_MODEL_BACKTEST_PROCESS_ENABLED", True),
            "evolve_process_default": self._env_flag("QT_HEAVY_JOB_PROCESS_ENABLED", True),
        }

    def admin_job_runs_kwargs(self) -> Dict[str, bool]:
        return {
            "news_fetch_process_default": self._env_flag("QT_NEWS_FETCH_PROCESS_ENABLED", True),
            "market_sync_process_default": self._env_flag("QT_MARKET_SYNC_PROCESS_ENABLED", True),
            "ai_analysis_process_default": self._env_flag("QT_AI_ANALYSIS_PROCESS_ENABLED", True),
            "trade_cycle_process_default": self._env_flag("QT_TRADE_CYCLE_PROCESS_ENABLED", True),
            "strategy_daily_refresh_process_default": self._env_flag("QT_STRATEGY_DAILY_REFRESH_PROCESS_ENABLED", True),
            "heavy_job_process_default": self._env_flag("QT_HEAVY_JOB_PROCESS_ENABLED", True),
            "frontend_payload_process_default": self._env_flag(
                "QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED",
                True,
            ),
            "frontend_account_process_default": self._env_flag(
                "QT_FRONT_ACCOUNT_PRECOMPUTE_PROCESS_ENABLED",
                True,
            ),
            "system_startup_process_default": self._env_flag("QT_SYSTEM_STARTUP_PROCESS_ENABLED", True),
            "system_startup_run_strategy_replay_default": self._env_flag(
                "QT_SYSTEM_STARTUP_RUN_STRATEGY_REPLAY",
                False,
            ),
        }

    def quant_timeline_kwargs(self) -> Dict[str, bool]:
        return {
            "defer_default": self._env_flag("QT_TIMELINE_DEFER_MISSES", True),
            "process_default": self._env_flag("QT_TIMELINE_PROCESS_ENABLED", True),
        }

    def data_collection_kwargs(self) -> Dict[str, bool]:
        return {
            "coverage_defer_default": self._env_flag("QT_DATA_COVERAGE_DEFER_MISSES", True),
            "coverage_process_default": self._env_flag("QT_DATA_COVERAGE_PROCESS_ENABLED", True),
            "kline_process_default": self._env_flag("QT_KLINE_FILL_PROCESS_ENABLED", True),
            "lhb_process_default": self._env_flag("QT_LHB_SYNC_PROCESS_ENABLED", True),
            "market_process_default": self._env_flag("QT_MARKET_SYNC_PROCESS_ENABLED", True),
        }

    def quant_backtest_kwargs(self) -> Dict[str, bool]:
        return {
            "defer_default": self._env_flag("QT_BACKTEST_DEFER_MISSES", True),
            "process_default": self._env_flag("QT_BACKTEST_PROCESS_ENABLED", True),
        }
