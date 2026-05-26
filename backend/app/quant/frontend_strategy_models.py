from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional

from app.quant.capital_strategy import CAPITAL_BANDS, capital_presets
from app.quant.engine_utils import safe_float


DEFAULT_ACTIVE_STRATEGY_NAME = "系统默认基础参数（非跟随策略）"


class FrontendStrategyModelsService:
    def __init__(
        self,
        *,
        app_version: Callable[[], str],
        cache_ttl_seconds: Callable[[], int],
        cache_get: Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]],
        cache_set: Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        strategy_params: Callable[..., Dict[str, Any]],
        strategy_source: Callable[[], Dict[str, Any]],
        catalog_payload: Callable[..., Dict[str, Any]],
        runtime_model_summaries: Callable[[list[Dict[str, Any]]], Dict[str, Any]],
        target_strategy_count: Callable[[], int],
    ) -> None:
        self._app_version = app_version
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_get = cache_get
        self._cache_set = cache_set
        self._strategy_params = strategy_params
        self._strategy_source = strategy_source
        self._catalog_payload = catalog_payload
        self._runtime_model_summaries = runtime_model_summaries
        self._target_strategy_count = target_strategy_count

    def active_model(self) -> Dict[str, Any]:
        return active_strategy_model(
            strategy_params=self._strategy_params,
            strategy_source=self._strategy_source,
        )

    def payload(self, include_catalog: bool = True) -> Dict[str, Any]:
        return frontend_strategy_models_payload(
            include_catalog=include_catalog,
            app_version=self._app_version(),
            cache_ttl_seconds=self._cache_ttl_seconds(),
            cache_get=self._cache_get,
            cache_set=self._cache_set,
            strategy_params=self._strategy_params,
            strategy_source=self._strategy_source,
            catalog_payload=self._catalog_payload,
            runtime_model_summaries=self._runtime_model_summaries,
            target_strategy_count=self._target_strategy_count(),
        )


def active_strategy_model(
    *,
    strategy_params: Callable[..., Dict[str, Any]],
    strategy_source: Callable[[], Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "id": "active",
        "name": DEFAULT_ACTIVE_STRATEGY_NAME,
        "source": "baseline",
        "reusable": False,
        "description": "用于人工调参、诊断和生成新策略的默认参数模板；每个策略模型仍保存自己的独立基础参数。",
        "params": strategy_params(),
        "strategy_source": strategy_source(),
    }


def _clean_target_strategy_count(value: Any) -> int:
    return max(1, min(int(safe_float(value, 20)), 200))


def _runtime_follow_readiness(item: Dict[str, Any], *, include_catalog: bool) -> Dict[str, Any]:
    status = str(item.get("runtime_data_status") or item.get("runtime_status") or "").strip()
    has_runtime = bool(item.get("has_runtime_data"))
    blocking_reasons: list[str] = []
    if not include_catalog:
        ready = False
        status = status or "not_loaded"
    elif has_runtime:
        ready = True
        status = status or "ready"
    else:
        ready = False
        status = status or "missing"
        blocking_reasons.append("missing_runtime")
    return {
        "ready_for_follow": ready,
        "runtime_ready_for_follow": ready,
        "follow_readiness": {
            "status": "ready" if ready else status,
            "ready_for_follow": ready,
            "blocking_reasons": blocking_reasons,
            "runtime_data_status": status,
            "runtime_start_date": str(item.get("runtime_start_date") or ""),
            "runtime_end_date": str(item.get("runtime_end_date") or ""),
            "signal_count": int(safe_float(item.get("signal_count"), 0)),
            "trade_count": int(safe_float(item.get("trade_count"), 0)),
        },
    }


def _enrich_runtime_item(
    item: Dict[str, Any],
    *,
    summary: Optional[Dict[str, Any]],
    include_catalog: bool,
) -> Dict[str, Any]:
    if summary:
        enriched = {**item, **summary}
        enriched["runtime_data_note"] = (
            f"已复盘 {summary.get('runtime_start_date') or '-'} 至 "
            f"{summary.get('runtime_end_date') or '-'}，"
            f"{int(safe_float(summary.get('trade_count'), 0))} 笔成交"
        )
    elif include_catalog:
        enriched = {
            **item,
            "runtime_data_status": "missing",
            "has_runtime_data": False,
            "runtime_data_note": "等待本地或服务器策略复盘生成数据",
        }
    else:
        enriched = {
            **item,
            "runtime_data_status": "not_loaded",
            "runtime_data_summary_loaded": False,
        }
    enriched.update(_runtime_follow_readiness(enriched, include_catalog=include_catalog))
    return enriched


def frontend_strategy_models_payload(
    *,
    include_catalog: bool = True,
    app_version: str = "",
    cache_ttl_seconds: int = 0,
    cache_get: Optional[Callable[[str, Dict[str, Any], int], Optional[Dict[str, Any]]]] = None,
    cache_set: Optional[Callable[[str, Dict[str, Any], Dict[str, Any]], Dict[str, Any]]] = None,
    strategy_params: Callable[..., Dict[str, Any]],
    strategy_source: Callable[[], Dict[str, Any]],
    catalog_payload: Callable[..., Dict[str, Any]],
    runtime_model_summaries: Callable[[list[Dict[str, Any]]], Dict[str, Any]],
    target_strategy_count: int = 20,
) -> Dict[str, Any]:
    clean_target_count = _clean_target_strategy_count(target_strategy_count)
    cache_parts = {
        "include_catalog": bool(include_catalog),
        "target_strategy_count": clean_target_count,
        "version": str(app_version or ""),
    }
    if cache_get is not None and cache_ttl_seconds > 0:
        cached = cache_get("strategy_models", cache_parts, cache_ttl_seconds)
        if cached:
            return cached

    active_model = active_strategy_model(
        strategy_params=strategy_params,
        strategy_source=strategy_source,
    )
    base_params = strategy_params()
    presets = capital_presets(base_params)[:clean_target_count]
    catalog_limit = max(0, clean_target_count - len(presets))
    if include_catalog and catalog_limit > 0:
        payload = catalog_payload(limit=catalog_limit, include_records=False)
    else:
        payload = {"status": "ok", "active": active_model, "items": [], "count": 0}
    if not isinstance(payload, dict):
        payload = {"status": "ok", "active": active_model, "items": [], "count": 0}

    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    payload["items"] = [
        dict(item)
        for item in raw_items
        if isinstance(item, dict) and item.get("reusable", True)
    ][:catalog_limit]
    payload["count"] = len(payload["items"])

    summary_targets = [*presets, *payload["items"]]
    summaries = runtime_model_summaries(summary_targets) if include_catalog else {}
    if not isinstance(summaries, dict):
        summaries = {}

    enriched_presets: list[Dict[str, Any]] = []
    for preset in presets:
        model_id = str(preset.get("id") or "")
        summary = summaries.get(model_id) if isinstance(summaries.get(model_id), dict) else None
        enriched_presets.append(_enrich_runtime_item(preset, summary=summary, include_catalog=include_catalog))

    enriched_items: list[Dict[str, Any]] = []
    for item in payload["items"]:
        model_id = str(item.get("id") or item.get("model_id") or "")
        summary = summaries.get(model_id) if isinstance(summaries.get(model_id), dict) else None
        enriched_items.append(_enrich_runtime_item(item, summary=summary, include_catalog=include_catalog))
    payload["items"] = enriched_items

    payload["active"] = {**active_model, **(payload.get("active") if isinstance(payload.get("active"), dict) else {})}
    payload["active"]["name"] = DEFAULT_ACTIVE_STRATEGY_NAME
    payload["capital_presets"] = enriched_presets
    payload["capital_bands"] = CAPITAL_BANDS
    payload["catalog_included"] = bool(include_catalog)
    payload["target_strategy_count"] = clean_target_count
    payload["target_catalog_limit"] = catalog_limit
    payload["capital_runtime_summary"] = {
        "total": len(enriched_presets),
        "ready": sum(1 for item in enriched_presets if item.get("has_runtime_data")),
        "missing": sum(1 for item in enriched_presets if not item.get("has_runtime_data")),
    }
    target_runtime_items = [*enriched_presets, *enriched_items]
    payload["target_runtime_summary"] = {
        "total": len(target_runtime_items),
        "ready": sum(1 for item in target_runtime_items if item.get("ready_for_follow")),
        "missing": sum(1 for item in target_runtime_items if not item.get("ready_for_follow")),
    }
    payload["count"] = len(payload["items"]) + len(enriched_presets)
    if cache_set is not None:
        return cache_set("strategy_models", cache_parts, payload)
    return payload
