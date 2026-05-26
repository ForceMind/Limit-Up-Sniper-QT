from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional

from app.quant.capital_strategy import DEFAULT_FRONTEND_STRATEGY_ID, recommended_strategy_id
from app.quant.engine_utils import safe_float


def strategy_catalog_items(models_payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    for item in models_payload.get("capital_presets") if isinstance(models_payload.get("capital_presets"), list) else []:
        if isinstance(item, dict):
            items.append(item)
    active = models_payload.get("active") if isinstance(models_payload.get("active"), dict) else {}
    if active:
        items.append({**active, "id": str(active.get("id") or "active")})
    for item in models_payload.get("items") if isinstance(models_payload.get("items"), list) else []:
        if isinstance(item, dict):
            items.append(item)
    seen = set()
    unique = []
    for item in items:
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        unique.append(item)
    return unique


def resolve_front_profile_updates(
    updates: Dict[str, Any],
    previous: Dict[str, Any],
    include_catalog: bool,
    models_loader: Callable[[bool], Dict[str, Any]],
    model_loader: Callable[..., Optional[Dict[str, Any]]],
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    resolved = dict(updates) if isinstance(updates, dict) else {}
    resolved_model: Optional[Dict[str, Any]] = None
    cash = max(
        10_000.0,
        min(
            10_000_000.0,
            safe_float(resolved.get("simulated_cash"), safe_float((previous or {}).get("simulated_cash"), 10_000.0)),
        ),
    )
    models_payload: Optional[Dict[str, Any]] = None

    def load_models() -> Dict[str, Any]:
        nonlocal models_payload
        if models_payload is None:
            models_payload = models_loader(include_catalog)
        return models_payload

    if resolved.get("auto_recommend"):
        items = strategy_catalog_items(load_models())
        resolved["strategy_model_id"] = recommended_strategy_id(cash, items)
        return resolved, None

    if "strategy_model_id" not in resolved:
        return resolved, None

    requested_id = str(resolved.get("strategy_model_id") or "").strip()
    items = strategy_catalog_items(load_models())
    selected = next((item for item in items if str(item.get("id") or "") == requested_id), None)
    if not include_catalog and requested_id and requested_id != "active" and selected is None:
        models_payload = models_loader(True)
        items = strategy_catalog_items(models_payload)
        selected = next((item for item in items if str(item.get("id") or "") == requested_id), None)
        if isinstance(selected, dict):
            resolved_model = {**selected, "frontend_target_verified": True}
    if not requested_id or requested_id == "active" or selected is None:
        resolved["strategy_model_id"] = recommended_strategy_id(cash, items) or DEFAULT_FRONTEND_STRATEGY_ID
        resolved_model = None
    return resolved, resolved_model
