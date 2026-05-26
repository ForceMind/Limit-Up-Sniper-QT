from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional


BackgroundStarter = Callable[[Callable[[], None]], None]


def _default_background_starter(task: Callable[[], None]) -> None:
    threading.Thread(target=task, name="strategy-evolution", daemon=True).start()


class StrategyModelApplyNotFound(Exception):
    pass


class StrategyEvolutionService:
    def __init__(
        self,
        *,
        strategy_evolution: Any,
        job_manager: Any,
        background_starter: BackgroundStarter = _default_background_starter,
    ) -> None:
        self._strategy_evolution = strategy_evolution
        self._job_manager = job_manager
        self._background_starter = background_starter

    def run_payload(
        self,
        *,
        generations: int,
        population_size: int,
        start_date: Optional[str],
        end_date: Optional[str],
        apply_best: bool,
        mode: str,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        current = self._strategy_evolution.status()
        if current.get("status") == "running":
            return current
        if process:
            return self._job_manager.run_strategy_evolution(
                start_date=start_date,
                end_date=end_date,
                mode=mode,
                generations=generations,
                population_size=population_size,
                apply_best=apply_best,
                process=True,
            )
        if background:
            def worker() -> None:
                self._job_manager.run_strategy_evolution(
                    start_date=start_date,
                    end_date=end_date,
                    mode=mode,
                    generations=generations,
                    population_size=population_size,
                    apply_best=apply_best,
                )

            self._background_starter(worker)
            return {
                "status": "running",
                "progress_pct": 1,
                "progress_message": "strategy evolution started in the background",
                "generations": generations,
                "population_size": population_size,
                "start_date": start_date,
                "end_date": end_date,
                "mode": mode,
                "background": True,
            }
        return self._strategy_evolution.run(
            generations=generations,
            population_size=population_size,
            start_date=start_date,
            end_date=end_date,
            apply_best=apply_best,
            mode=mode,
        )

    def apply_model_payload(
        self,
        *,
        model_id: str,
        models_payload: Dict[str, Any],
        strategy_catalog_items: Callable[[Dict[str, Any]], list[Dict[str, Any]]],
        update_strategy_params: Callable[..., Dict[str, Any]],
    ) -> Dict[str, Any]:
        clean_model_id = str(model_id or "").strip()
        model = next(
            (
                item
                for item in strategy_catalog_items(models_payload)
                if str(item.get("id") or "") == clean_model_id
            ),
            None,
        )
        if not model:
            raise StrategyModelApplyNotFound("strategy model not found")

        params = model.get("params") if isinstance(model.get("params"), dict) else {}
        source_type = "capital_preset" if model.get("is_capital_preset") else "strategy_model"
        result = update_strategy_params(
            params,
            source={
                "type": source_type,
                "model_id": str(model.get("id") or ""),
                "name": str(model.get("name") or model.get("id") or ""),
                "description": (
                    "Applied from capital preset to system default strategy parameters."
                    if source_type == "capital_preset"
                    else "Applied from strategy model to system default strategy parameters."
                ),
                "objective": model.get("objective"),
                "return_pct": model.get("return_pct"),
                "max_drawdown_pct": model.get("max_drawdown_pct"),
                "win_rate": model.get("win_rate"),
            },
        )
        if source_type == "strategy_model":
            self._strategy_evolution.mark_applied_model(model)
        return {
            "status": "ok",
            "model": model,
            "strategy_params": result.get("strategy_params"),
            "strategy_source": result.get("strategy_source"),
        }
