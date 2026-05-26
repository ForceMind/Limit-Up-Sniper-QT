from __future__ import annotations

from typing import Any, MutableMapping

from app.quant.quant_research_composition import build_quant_research_services


def build_quant_research_partition(state: MutableMapping[str, Any]) -> None:
    get = state.__getitem__
    quant_research_services = build_quant_research_services(
        quant_engine=get("quant_engine"),
        job_manager=get("job_manager"),
        trade_notifier=get("trade_notifier"),
        strategy_evolution=get("strategy_evolution"),
        app_version=lambda: get("APP_VERSION"),
        load_payload_cache=lambda payload_type, parts, ttl: get("load_payload_cache")(payload_type, parts, ttl),
        save_payload_cache=lambda payload_type, parts, payload, ttl: get("save_payload_cache")(
            payload_type,
            parts,
            payload,
            ttl,
        ),
        cache_env_int=lambda name, default, **kwargs: get("cache_env_int")(name, default, **kwargs),
        env_flag=lambda name, default: get("_APP_CONFIG").env_flag(name, default),
        deferred_job_response_state=get("_FRONTEND_PAYLOAD_READ_SERVICE").deferred_job_response_state,
        strategy_models_payload=lambda **kwargs: get("_FRONTEND_STRATEGY_MODELS_SERVICE").payload(**kwargs),
        strategy_catalog_items=get("_strategy_catalog_items"),
        update_strategy_params=lambda *args, **kwargs: get("quant_engine").update_strategy_params(*args, **kwargs),
        safe_news_feed=lambda **kwargs: get("_FRONTEND_NEWS_READ_SERVICE").safe_news_feed(**kwargs),
    )
    state.update(
        {
            "_QUANT_RESEARCH_SERVICES": quant_research_services,
            "_QUANT_BACKTEST_SERVICE": quant_research_services.quant_backtest,
            "_STRATEGY_MODEL_BACKTEST_SERVICE": quant_research_services.strategy_model_backtest,
            "_STRATEGY_MODEL_LOOKUP_SERVICE": quant_research_services.strategy_model_lookup,
            "_QUANT_TIMELINE_SERVICE": quant_research_services.quant_timeline,
            "_FIT_STRATEGY_SERVICE": quant_research_services.fit_strategy,
            "_STRATEGY_EVOLUTION_SERVICE": quant_research_services.strategy_evolution,
            "_QUANT_STRATEGY_RESEARCH_SERVICE": quant_research_services.quant_strategy_research,
            "_QUANT_BASIC_SERVICE": quant_research_services.quant_basic,
        }
    )
