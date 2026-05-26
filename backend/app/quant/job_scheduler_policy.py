from __future__ import annotations

from typing import Any, Callable, Dict


EnvBool = Callable[[str, bool], bool]
EnvInt = Callable[[str, int], int]
EnvNonnegativeInt = Callable[[str, int, int], int]


class JobSchedulerPolicy:
    def __init__(
        self,
        *,
        env_bool: EnvBool,
        env_int: EnvInt,
        env_nonnegative_int: EnvNonnegativeInt,
        is_market_open: Callable[[], bool],
        target_strategy_count: Callable[[], int],
        research_tasks_manual_only: Callable[[], bool],
    ) -> None:
        self._env_bool = env_bool
        self._env_int = env_int
        self._env_nonnegative_int = env_nonnegative_int
        self._is_market_open = is_market_open
        self._target_strategy_count = target_strategy_count
        self._research_tasks_manual_only = research_tasks_manual_only

    def news_interval_seconds(self) -> int:
        return self._env_int("NEWS_FETCH_INTERVAL_SECONDS", 3600)

    def market_interval_seconds(self) -> int:
        return self._env_int("MARKET_SYNC_INTERVAL_SECONDS", 300)

    def ai_interval_seconds(self) -> int:
        return self._env_int("AI_ANALYSIS_INTERVAL_SECONDS", 3600)

    def trade_interval_seconds(self) -> int:
        return self._env_int("TRADE_CYCLE_INTERVAL_SECONDS", 300 if self._is_market_open() else 3600)

    def strategy_daily_refresh_enabled(self) -> bool:
        return self._env_bool("QT_STRATEGY_DAILY_REFRESH_ENABLED", True)

    def strategy_daily_refresh_process_enabled(self) -> bool:
        return self._env_bool("QT_STRATEGY_DAILY_REFRESH_PROCESS_ENABLED", True)

    def strategy_daily_refresh_waits_for_heavy_jobs(self) -> bool:
        return self._env_bool("QT_STRATEGY_DAILY_REFRESH_WAIT_HEAVY_JOBS", True)

    def strategy_daily_refresh_interval_seconds(self) -> int:
        return self._env_int("QT_STRATEGY_DAILY_REFRESH_INTERVAL_SECONDS", 1800 if self._is_market_open() else 3600)

    def strategy_daily_refresh_initial_delay_seconds(self) -> int:
        return self._env_nonnegative_int("QT_STRATEGY_DAILY_REFRESH_INITIAL_DELAY_SECONDS", 45, 86400)

    def strategy_replay_interval_seconds(self) -> int:
        return self._env_int("STRATEGY_REPLAY_INTERVAL_SECONDS", 3600)

    def strategy_replay_batch_days(self) -> int:
        return max(1, min(self._env_int("QT_STRATEGY_REPLAY_BATCH_DAYS", 15), 366))

    def strategy_replay_max_models(self) -> int:
        return max(1, min(self._env_int("QT_STRATEGY_REPLAY_MAX_MODELS", self._target_strategy_count()), 200))

    def strategy_evolution_interval_seconds(self) -> int:
        return self._env_int("STRATEGY_EVOLUTION_INTERVAL_SECONDS", 6 * 3600)

    def strategy_evolution_generations(self) -> int:
        max_generations = max(1, min(self._env_int("QT_STRATEGY_EVOLUTION_MAX_GENERATIONS", 8), 30))
        return max(1, min(self._env_int("STRATEGY_EVOLUTION_GENERATIONS", 1), max_generations))

    def strategy_evolution_population_size(self) -> int:
        max_population = max(6, min(self._env_int("QT_STRATEGY_EVOLUTION_MAX_POPULATION", 32), 80))
        return max(6, min(self._env_int("STRATEGY_EVOLUTION_POPULATION_SIZE", 16), max_population))

    def kline_fill_interval_seconds(self) -> int:
        return self._env_int("KLINE_FILL_INTERVAL_SECONDS", 6 * 3600)

    def lhb_sync_interval_seconds(self) -> int:
        return self._env_int("LHB_SYNC_INTERVAL_SECONDS", 12 * 3600)

    def auto_backfill_max_codes(self) -> int:
        return max(1, min(self._env_int("DATA_BACKFILL_MAX_CODES", 160), 2000))

    def frontend_payload_precompute_interval_seconds(self) -> int:
        return self._env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_INTERVAL_SECONDS", 1800)

    def frontend_payload_precompute_initial_delay_seconds(self) -> int:
        return self._env_nonnegative_int(
            "QT_FRONT_PAYLOAD_PRECOMPUTE_INITIAL_DELAY_SECONDS",
            self.frontend_payload_precompute_interval_seconds(),
            86400,
        )

    def frontend_payload_precompute_limit_users(self) -> int:
        return max(1, min(self._env_int("QT_FRONT_PAYLOAD_PRECOMPUTE_LIMIT_USERS", 8), 500))

    def research_tasks_manual_only(self) -> bool:
        return self._research_tasks_manual_only()

    def strategy_replay_auto_enabled(self) -> bool:
        return (not self.research_tasks_manual_only()) and self._env_bool("STRATEGY_REPLAY_ENABLED", False)

    def strategy_evolution_auto_enabled(self) -> bool:
        return (not self.research_tasks_manual_only()) and self._env_bool("STRATEGY_EVOLUTION_ENABLED", False)

    def frontend_payload_policy(self) -> Dict[str, Any]:
        precompute_enabled = self._env_bool("QT_FRONT_PAYLOAD_PRECOMPUTE_ENABLED", False)
        auto_on_miss_requested = self._env_bool("QT_FRONT_PAYLOAD_AUTO_PRECOMPUTE_ON_MISS", False)
        return {
            "mode": "scheduled" if precompute_enabled else "manual",
            "precompute_enabled": precompute_enabled,
            "scheduled_precompute_enabled": precompute_enabled,
            "auto_precompute_on_miss": precompute_enabled and auto_on_miss_requested,
            "auto_precompute_on_miss_requested": auto_on_miss_requested,
            "process_enabled": self._env_bool("QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED", True),
            "interval_seconds": self.frontend_payload_precompute_interval_seconds(),
            "initial_delay_seconds": self.frontend_payload_precompute_initial_delay_seconds(),
            "limit_users": self.frontend_payload_precompute_limit_users(),
            "max_seconds": self._env_nonnegative_int("QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS", 20, 86400),
            "recommendations_cache_ttl_seconds": self._env_nonnegative_int(
                "QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS",
                1800,
                86400,
            ),
            "daily_plan_cache_ttl_seconds": self._env_nonnegative_int(
                "QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS",
                1800,
                86400,
            ),
        }
