from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException

from app.quant.strategy_evolution_service import StrategyModelApplyNotFound
from app.quant.strategy_model_lookup_service import StrategyModelLookupNotFound


class QuantStrategyResearchService:
    def __init__(
        self,
        *,
        fit_strategy_service: Any,
        strategy_model_backtest_service: Any,
        strategy_evolution_service: Any,
        strategy_evolution: Any,
        find_strategy_model: Callable[[str], Dict[str, Any]],
        strategy_models_payload: Callable[[], Dict[str, Any]],
        strategy_catalog_items: Callable[[Dict[str, Any]], list[Dict[str, Any]]],
        update_strategy_params: Callable[..., Dict[str, Any]],
    ) -> None:
        self._fit_strategy_service = fit_strategy_service
        self._strategy_model_backtest_service = strategy_model_backtest_service
        self._strategy_evolution_service = strategy_evolution_service
        self._strategy_evolution = strategy_evolution
        self._find_strategy_model = find_strategy_model
        self._strategy_models_payload = strategy_models_payload
        self._strategy_catalog_items = strategy_catalog_items
        self._update_strategy_params = update_strategy_params

    def fit_strategy_payload(
        self,
        as_of: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        apply_best: bool,
        defer: bool,
        process: bool,
        manual: bool,
    ) -> Dict[str, Any]:
        return self._fit_strategy_service.deferred_or_sync(
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            apply_best=apply_best,
            defer=defer,
            process=process,
            manual=manual,
        )

    def evolution_status_payload(self) -> Dict[str, Any]:
        return self._strategy_evolution.status()

    def evolution_trace_payload(
        self,
        run_id: Optional[str],
        generation: Optional[int],
        limit: int,
    ) -> Dict[str, Any]:
        return self._strategy_evolution.trace(
            run_id=run_id,
            generation=generation,
            limit=limit,
        )

    def evolution_pause_payload(self) -> Dict[str, Any]:
        return self._strategy_evolution.pause()

    def evolution_resume_payload(self) -> Dict[str, Any]:
        return self._strategy_evolution.resume()

    def models_payload(self) -> Dict[str, Any]:
        return self._strategy_evolution.models()

    def model_backtest_payload(
        self,
        model_id: str,
        start_date: Optional[str],
        end_date: Optional[str],
        mode: str,
        limit: int,
        recompute: bool,
        force: bool,
        defer: bool,
        manual: bool,
        process: bool,
    ) -> Dict[str, Any]:
        try:
            model = self._find_strategy_model(model_id)
        except StrategyModelLookupNotFound as exc:
            raise HTTPException(status_code=404, detail="strategy model not found") from exc
        if not recompute:
            return self._strategy_model_backtest_service.stored_payload(model, limit=limit)
        return self._strategy_model_backtest_service.recompute_payload(
            model,
            start_date,
            end_date,
            mode,
            limit,
            force=force,
            defer=defer,
            manual=manual,
            process=process,
        )

    def apply_model_payload(self, model_id: str) -> Dict[str, Any]:
        try:
            return self._strategy_evolution_service.apply_model_payload(
                model_id=model_id,
                models_payload=self._strategy_models_payload(),
                strategy_catalog_items=self._strategy_catalog_items,
                update_strategy_params=self._update_strategy_params,
            )
        except StrategyModelApplyNotFound as exc:
            raise HTTPException(status_code=404, detail="strategy model not found") from exc

    def evolve_strategy_payload(
        self,
        generations: int,
        population_size: int,
        start_date: Optional[str],
        end_date: Optional[str],
        apply_best: bool,
        mode: str,
        background: bool,
        process: bool,
    ) -> Dict[str, Any]:
        return self._strategy_evolution_service.run_payload(
            generations=generations,
            population_size=population_size,
            start_date=start_date,
            end_date=end_date,
            apply_best=apply_best,
            mode=mode,
            background=background,
            process=process,
        )
