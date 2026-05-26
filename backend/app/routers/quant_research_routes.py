from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.routers.quant_backtest import build_quant_backtest_router
from app.routers.quant_basic import build_quant_basic_router
from app.routers.quant_strategy import build_quant_strategy_router
from app.routers.quant_timeline import build_quant_timeline_router


def register_quant_research_routes(
    app: FastAPI,
    *,
    basic_service: Any,
    strategy_research_service: Any,
    timeline_service: Any,
    backtest_service: Any,
    route_defaults: Any,
) -> None:
    app.include_router(
        build_quant_basic_router(
            dashboard_payload=basic_service.dashboard_payload,
            recommendations_payload=basic_service.recommendations_payload,
            daily_plan_payload=basic_service.daily_plan_payload,
            strategy_params_payload=basic_service.strategy_params_payload,
            strategy_params_update_payload=basic_service.update_strategy_params_payload,
            strategy_params_reset_payload=basic_service.reset_strategy_params_payload,
            events_payload=basic_service.events_payload,
            news_payload=basic_service.news_payload,
            correlation_payload=basic_service.correlation_payload,
            portfolio_payload=basic_service.portfolio_payload,
            trading_account_payload=basic_service.trading_account_payload,
            run_payload=basic_service.run_payload,
            news_history_payload=basic_service.news_history_payload,
        )
    )
    app.include_router(
        build_quant_strategy_router(
            fit_strategy_payload=strategy_research_service.fit_strategy_payload,
            evolution_status_payload=strategy_research_service.evolution_status_payload,
            evolution_trace_payload=strategy_research_service.evolution_trace_payload,
            evolution_pause_payload=strategy_research_service.evolution_pause_payload,
            evolution_resume_payload=strategy_research_service.evolution_resume_payload,
            models_payload=strategy_research_service.models_payload,
            model_backtest_payload=strategy_research_service.model_backtest_payload,
            apply_model_payload=strategy_research_service.apply_model_payload,
            evolve_strategy_payload=strategy_research_service.evolve_strategy_payload,
            **route_defaults.quant_strategy_kwargs(),
        )
    )
    app.include_router(
        build_quant_timeline_router(
            timeline_payload=timeline_service.route_payload,
            **route_defaults.quant_timeline_kwargs(),
        )
    )
    app.include_router(
        build_quant_backtest_router(
            backtest_payload=backtest_service.route_payload,
            **route_defaults.quant_backtest_kwargs(),
        )
    )
