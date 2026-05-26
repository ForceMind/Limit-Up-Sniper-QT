from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from app.quant.fit_strategy_service import FitStrategyService
from app.quant.quant_backtest_service import QuantBacktestService
from app.quant.quant_basic_service import QuantBasicService
from app.quant.quant_strategy_research_service import QuantStrategyResearchService
from app.quant.quant_timeline_service import QuantTimelineService
from app.quant.strategy_evolution_service import StrategyEvolutionService
from app.quant.strategy_model_backtest_service import StrategyModelBacktestService
from app.quant.strategy_model_lookup_service import StrategyModelLookupService


@dataclass(frozen=True)
class QuantResearchServices:
    quant_backtest: QuantBacktestService
    strategy_model_backtest: StrategyModelBacktestService
    strategy_model_lookup: StrategyModelLookupService
    quant_timeline: QuantTimelineService
    fit_strategy: FitStrategyService
    strategy_evolution: StrategyEvolutionService
    quant_strategy_research: QuantStrategyResearchService
    quant_basic: QuantBasicService


def build_quant_research_services(
    *,
    quant_engine: Any,
    job_manager: Any,
    trade_notifier: Any,
    strategy_evolution: Any,
    app_version: Callable[[], str],
    env_flag: Callable[[str, bool], bool],
    cache_env_int: Callable[..., int],
    load_payload_cache: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    save_payload_cache: Callable[[str, Dict[str, Any], Dict[str, Any], int], None],
    deferred_job_response_state: Callable[..., Dict[str, Any]],
    strategy_models_payload: Callable[..., Dict[str, Any]],
    strategy_catalog_items: Callable[[Dict[str, Any]], list[Dict[str, Any]]],
    update_strategy_params: Callable[..., Dict[str, Any]],
    safe_news_feed: Callable[..., Dict[str, Any]],
) -> QuantResearchServices:
    quant_backtest = QuantBacktestService(
        quant_engine=quant_engine,
        job_manager=job_manager,
        load_payload_cache=load_payload_cache,
        save_payload_cache=save_payload_cache,
        cache_env_int=cache_env_int,
        env_flag=env_flag,
        deferred_job_response_state=deferred_job_response_state,
        app_version=app_version,
    )
    strategy_model_backtest = StrategyModelBacktestService(
        quant_engine=quant_engine,
        job_manager=job_manager,
        load_payload_cache=load_payload_cache,
        save_payload_cache=save_payload_cache,
        cache_env_int=cache_env_int,
        env_flag=env_flag,
        deferred_job_response_state=deferred_job_response_state,
        runtime_model_version=lambda model: strategy_evolution.runtime_model_version(model),
        app_version=app_version,
    )
    strategy_model_lookup = StrategyModelLookupService(
        model_lookup=lambda *args, **kwargs: strategy_evolution.model(*args, **kwargs),
        strategy_models_payload=strategy_models_payload,
        strategy_catalog_items=strategy_catalog_items,
    )
    quant_timeline = QuantTimelineService(
        quant_engine=quant_engine,
        job_manager=job_manager,
        find_strategy_model=strategy_model_lookup.find_model,
        load_payload_cache=load_payload_cache,
        save_payload_cache=save_payload_cache,
        cache_env_int=cache_env_int,
        env_flag=env_flag,
        deferred_job_response_state=deferred_job_response_state,
        runtime_model_version=lambda model: strategy_evolution.runtime_model_version(model),
        app_version=app_version,
    )
    fit_strategy = FitStrategyService(
        quant_engine=quant_engine,
        job_manager=job_manager,
        deferred_job_response_state=deferred_job_response_state,
        env_flag=env_flag,
    )
    strategy_evolution_service = StrategyEvolutionService(
        strategy_evolution=strategy_evolution,
        job_manager=job_manager,
    )
    return QuantResearchServices(
        quant_backtest=quant_backtest,
        strategy_model_backtest=strategy_model_backtest,
        strategy_model_lookup=strategy_model_lookup,
        quant_timeline=quant_timeline,
        fit_strategy=fit_strategy,
        strategy_evolution=strategy_evolution_service,
        quant_strategy_research=QuantStrategyResearchService(
            fit_strategy_service=fit_strategy,
            strategy_model_backtest_service=strategy_model_backtest,
            strategy_evolution_service=strategy_evolution_service,
            strategy_evolution=strategy_evolution,
            find_strategy_model=strategy_model_lookup.find_model,
            strategy_models_payload=lambda: strategy_models_payload(include_catalog=True),
            strategy_catalog_items=strategy_catalog_items,
            update_strategy_params=update_strategy_params,
        ),
        quant_basic=QuantBasicService(
            quant_engine=quant_engine,
            trade_notifier=trade_notifier,
            safe_news_feed=safe_news_feed,
        ),
    )
