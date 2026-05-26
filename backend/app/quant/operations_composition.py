from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from app.quant.ai_monitoring_read_service import AiMonitoringReadService
from app.quant.data_collection_service import DataCollectionService
from app.quant.data_coverage_service import DataCoverageService


@dataclass(frozen=True)
class OperationsServices:
    data_coverage: DataCoverageService
    data_collection: DataCollectionService
    ai_monitoring: AiMonitoringReadService


def build_operations_services(
    *,
    app_version: Callable[[], str],
    data_coverage: Callable[..., Dict[str, Any]],
    job_manager: Any,
    load_payload_cache: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    save_payload_cache: Callable[[str, Dict[str, Any], Dict[str, Any], int], None],
    memory_cache_get: Callable[[str, Dict[str, Any], int], Dict[str, Any] | None],
    memory_cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    cache_env_int: Callable[..., int],
    env_flag: Callable[[str, bool], bool],
    resolve_as_of: Callable[..., str],
    deferred_job_response_state: Callable[..., Dict[str, Any]],
    biying_status: Callable[[], Dict[str, Any]],
    lhb_status: Callable[[], Dict[str, Any]],
    ai_usage_summary: Callable[[], Dict[str, Any]],
    ai_records_feed: Callable[..., Dict[str, Any]],
    ai_failures: Callable[..., Dict[str, Any]],
) -> OperationsServices:
    return OperationsServices(
        data_coverage=DataCoverageService(
            data_coverage=data_coverage,
            job_manager=job_manager,
            load_payload_cache=load_payload_cache,
            save_payload_cache=save_payload_cache,
            memory_cache_get=memory_cache_get,
            memory_cache_set=memory_cache_set,
            cache_env_int=cache_env_int,
            env_flag=env_flag,
            resolve_as_of=resolve_as_of,
            deferred_job_response_state=deferred_job_response_state,
            app_version=app_version,
        ),
        data_collection=DataCollectionService(
            biying_status=biying_status,
            lhb_status=lhb_status,
            job_manager=job_manager,
        ),
        ai_monitoring=AiMonitoringReadService(
            app_version=app_version,
            cache_env_int=cache_env_int,
            cache_get=memory_cache_get,
            cache_set=memory_cache_set,
            usage_summary=ai_usage_summary,
            records_feed=ai_records_feed,
            failures_feed=ai_failures,
        ),
    )
