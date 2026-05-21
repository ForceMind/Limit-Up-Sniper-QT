from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.quant.engine import DATA_DIR, quant_engine, read_json, safe_float, write_json


EVOLUTION_STATE_FILE = DATA_DIR / "strategy_evolution_state.json"


GENES: Dict[str, tuple[float, float]] = {
    "buy_threshold": (55, 90),
    "watch_threshold": (45, 80),
    "avoid_sell_threshold": (55, 92),
    "avoid_buy_ceiling": (45, 85),
    "sell_score_threshold": (55, 92),
    "stop_loss_pct": (-12, -2),
    "take_profit_pct": (3, 20),
    "max_hold_days": (1, 10),
    "max_positions": (2, 10),
    "top_n": (3, 20),
    "sentiment_weight": (0.10, 0.55),
    "event_weight": (0.10, 0.55),
    "technical_weight": (0.10, 0.55),
    "risk_weight": (0.05, 0.40),
    "sentiment_coef": (20, 90),
    "ai_score_coef": (1, 10),
    "event_impact_weight": (0.35, 0.85),
    "history_score_weight": (0.15, 0.65),
    "history_return_coef": (150, 700),
    "history_win_coef": (10, 100),
}


class StrategyEvolution:
    def __init__(self) -> None:
        self.state_file = EVOLUTION_STATE_FILE
        self._lock = threading.Lock()

    def status(self) -> Dict[str, Any]:
        payload = read_json(self.state_file, {})
        return payload if isinstance(payload, dict) else {"status": "idle"}

    def run(
        self,
        generations: int = 4,
        population_size: int = 16,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        apply_best: bool = False,
    ) -> Dict[str, Any]:
        generations = max(1, min(int(generations or 4), 30))
        population_size = max(6, min(int(population_size or 16), 80))
        started_ts = time.time()
        started_at = datetime.now().isoformat(timespec="seconds")
        if not self._lock.acquire(blocking=False):
            return {"status": "running", "message": "strategy evolution is already running"}
        try:
            base = quant_engine.strategy_params()
            population = self._initial_population(base, population_size)
            history = []
            best: Optional[Dict[str, Any]] = None
            for generation in range(1, generations + 1):
                evaluated = [self._evaluate(candidate, start_date=start_date, end_date=end_date) for candidate in population]
                evaluated.sort(key=lambda item: item["objective"], reverse=True)
                if best is None or evaluated[0]["objective"] > best["objective"]:
                    best = evaluated[0]
                history.append(
                    {
                        "generation": generation,
                        "best_objective": evaluated[0]["objective"],
                        "best_return_pct": evaluated[0]["return_pct"],
                        "best_drawdown_pct": evaluated[0]["max_drawdown_pct"],
                        "best_win_rate": evaluated[0]["win_rate"],
                        "population": len(evaluated),
                    }
                )
                population = self._next_generation(evaluated, population_size)

            applied = False
            if apply_best and best:
                quant_engine.update_strategy_params(best["params"])
                applied = True

            result = {
                "status": "ok",
                "started_at": started_at,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "duration_ms": round((time.time() - started_ts) * 1000, 2),
                "generations": generations,
                "population_size": population_size,
                "start_date": start_date,
                "end_date": end_date,
                "applied": applied,
                "best": best,
                "history": history,
            }
            write_json(self.state_file, result)
            return result
        finally:
            self._lock.release()

    def _initial_population(self, base: Dict[str, float], population_size: int) -> List[Dict[str, float]]:
        population = [quant_engine.strategy_params(base)]
        while len(population) < population_size:
            population.append(self._mutate(base, scale=0.35))
        return population

    def _next_generation(self, evaluated: List[Dict[str, Any]], population_size: int) -> List[Dict[str, float]]:
        elite_count = max(2, population_size // 5)
        elites = [item["params"] for item in evaluated[:elite_count]]
        population = [dict(item) for item in elites]
        while len(population) < population_size:
            parent_a = random.choice(elites)
            parent_b = random.choice(evaluated[: max(elite_count + 2, population_size // 2)])["params"]
            child = {}
            for key in GENES:
                child[key] = parent_a[key] if random.random() < 0.5 else parent_b[key]
            population.append(self._mutate(child, scale=0.18))
        return population[:population_size]

    def _mutate(self, params: Dict[str, Any], scale: float) -> Dict[str, float]:
        mutated = dict(params)
        for key, bounds in GENES.items():
            low, high = bounds
            current = safe_float(mutated.get(key), (low + high) / 2)
            if random.random() < 0.72:
                current += random.gauss(0, (high - low) * scale)
            mutated[key] = max(low, min(high, current))
        return quant_engine.strategy_params(mutated)

    def _evaluate(self, params: Dict[str, float], start_date: Optional[str], end_date: Optional[str]) -> Dict[str, Any]:
        with quant_engine.temporary_strategy_params(params):
            result = quant_engine.walk_forward(
                start_date=start_date,
                end_date=end_date,
                initial_cash=1_000_000.0,
                max_positions=int(params["max_positions"]),
                hold_days=int(params["max_hold_days"]),
                top_n=int(params["top_n"]),
            )
        return_pct = safe_float(result.get("return_pct"), 0)
        max_drawdown_pct = safe_float(result.get("max_drawdown_pct"), 0)
        win_rate = safe_float(result.get("win_rate"), 0)
        closed_trades = safe_float(result.get("closed_trades"), 0)
        trade_penalty = 8.0 if closed_trades < 5 else 0.0
        objective = return_pct - max_drawdown_pct * 0.8 + win_rate * 0.04 - trade_penalty
        return {
            "objective": round(objective, 4),
            "return_pct": round(return_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "win_rate": round(win_rate, 4),
            "closed_trades": int(closed_trades),
            "params": params,
        }


strategy_evolution = StrategyEvolution()
