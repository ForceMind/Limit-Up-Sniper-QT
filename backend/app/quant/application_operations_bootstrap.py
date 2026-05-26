from __future__ import annotations

from typing import Any, MutableMapping

from app.quant.memory_payload_cache import MemoryPayloadCache
from app.quant.monitoring import ai_failures, ai_records_feed, ai_usage_summary
from app.quant.operations_composition import build_operations_services


def build_operations_partition(
    state: MutableMapping[str, Any],
    memory_payload_cache_service: MemoryPayloadCache,
) -> None:
    get = state.__getitem__
    operations_services = build_operations_services(
        app_version=lambda: get("APP_VERSION"),
        data_coverage=lambda **kwargs: get("data_coverage")(**kwargs),
        job_manager=get("job_manager"),
        load_payload_cache=lambda payload_type, parts, ttl: get("load_payload_cache")(payload_type, parts, ttl),
        save_payload_cache=lambda payload_type, parts, payload, ttl: get("save_payload_cache")(
            payload_type,
            parts,
            payload,
            ttl,
        ),
        memory_cache_get=memory_payload_cache_service.get,
        memory_cache_set=memory_payload_cache_service.set,
        cache_env_int=lambda name, default, **kwargs: get("cache_env_int")(name, default, **kwargs),
        env_flag=lambda name, default: get("_APP_CONFIG").env_flag(name, default),
        resolve_as_of=get("_FRONTEND_DATE_SERVICE").account_as_of,
        deferred_job_response_state=get("_FRONTEND_PAYLOAD_READ_SERVICE").deferred_job_response_state,
        biying_status=lambda: get("biying_minute_sync").status(),
        lhb_status=lambda: get("lhb_status")(),
        ai_usage_summary=lambda: ai_usage_summary(),
        ai_records_feed=lambda **kwargs: ai_records_feed(**kwargs),
        ai_failures=lambda **kwargs: ai_failures(**kwargs),
    )
    state.update(
        {
            "_OPERATIONS_SERVICES": operations_services,
            "_DATA_COVERAGE_SERVICE": operations_services.data_coverage,
            "_DATA_COLLECTION_SERVICE": operations_services.data_collection,
            "_AI_MONITORING_READ_SERVICE": operations_services.ai_monitoring,
        }
    )
