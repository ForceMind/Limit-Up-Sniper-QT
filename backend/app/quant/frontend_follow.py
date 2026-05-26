from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.quant.capital_strategy import (
    DEFAULT_FRONTEND_STRATEGY_ID,
    apply_capital_constraints,
    recommended_strategy_id,
)
from app.quant.engine_utils import safe_float
from app.quant.front_profile import strategy_catalog_items


def _find_model(model_items: list[Dict[str, Any]], model_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in model_items if str(item.get("id") or "") == model_id), None)


def _append_model(models_payload: Dict[str, Any], model_items: list[Dict[str, Any]], model: Dict[str, Any]) -> None:
    model_id = str(model.get("id") or "").strip()
    if not model_id or _find_model(model_items, model_id) is not None:
        return
    model_items.append(model)
    items = models_payload.setdefault("items", [])
    if isinstance(items, list) and not any(str(item.get("id") or "") == model_id for item in items if isinstance(item, dict)):
        items.append(model)


def request_username_from_state(request: Any) -> str:
    auth_payload = getattr(getattr(request, "state", None), "auth_payload", None)
    username = str((auth_payload or {}).get("sub") or "").strip() if isinstance(auth_payload, dict) else ""
    if not username:
        raise HTTPException(status_code=401, detail="missing or invalid token")
    return username


def resolve_frontend_follow_context(
    username: str,
    *,
    include_catalog: bool = True,
    fallback_catalog_on_missing: bool = True,
    profile_payload: Optional[Dict[str, Any]] = None,
    resolved_model: Optional[Dict[str, Any]] = None,
    frontend_user_profile: Callable[[str], Dict[str, Any]],
    strategy_models_payload: Callable[..., Dict[str, Any]],
    model_lookup: Callable[..., Optional[Dict[str, Any]]],
    active_strategy_model: Callable[[], Dict[str, Any]],
    strategy_params: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    update_user_profile: Callable[[str, Dict[str, Any]], Any],
) -> Dict[str, Any]:
    username = str(username or "").strip()
    if not isinstance(profile_payload, dict):
        profile_payload = frontend_user_profile(username)
    if not isinstance(profile_payload, dict):
        profile_payload = {}

    profile = profile_payload.get("profile") if isinstance(profile_payload.get("profile"), dict) else {}
    simulated_cash = max(10_000.0, min(10_000_000.0, safe_float(profile.get("simulated_cash"), 10_000.0)))
    original_selected_id = str(profile.get("strategy_model_id") or "").strip()
    selected_id = original_selected_id

    models_payload = strategy_models_payload(include_catalog=include_catalog)
    model_items = strategy_catalog_items(models_payload)
    selected = _find_model(model_items, selected_id)

    if (
        selected is None
        and isinstance(resolved_model, dict)
        and resolved_model.get("frontend_target_verified")
        and str(resolved_model.get("id") or "") == selected_id
    ):
        selected = dict(resolved_model)
        selected.pop("frontend_target_verified", None)
        _append_model(models_payload, model_items, selected)

    if not include_catalog and selected_id and selected_id != "active" and selected is None and fallback_catalog_on_missing:
        models_payload = strategy_models_payload(include_catalog=True)
        model_items = strategy_catalog_items(models_payload)
        selected = _find_model(model_items, selected_id)

    recommended_id = recommended_strategy_id(simulated_cash, model_items)
    should_recommend = not selected_id or selected_id == "active" or selected is None
    if should_recommend:
        selected_id = recommended_id or DEFAULT_FRONTEND_STRATEGY_ID
        selected = _find_model(model_items, selected_id)

    if not selected:
        selected = active_strategy_model()
        selected_id = "active"
        profile["strategy_model_id"] = selected_id

    base_params = (selected or {}).get("params") if isinstance((selected or {}).get("params"), dict) else {}
    params = strategy_params(base_params)
    params = apply_capital_constraints(params, simulated_cash)

    profile["simulated_cash"] = round(simulated_cash, 2)
    profile["recommended_strategy_model_id"] = recommended_id
    profile["capital_mode"] = str(params.get("capital_mode") or "")
    profile["capital_label"] = str(params.get("capital_label") or "")
    if original_selected_id != selected_id:
        profile["strategy_model_id"] = selected_id
        try:
            update_user_profile(
                username,
                {
                    "simulated_cash": profile["simulated_cash"],
                    "strategy_model_id": selected_id,
                },
            )
        except Exception:
            pass

    models_payload["selected_model_id"] = selected_id
    models_payload["recommended_model_id"] = recommended_id
    return {
        "username": username,
        "created_at": str(profile_payload.get("created_at") or ""),
        "profile_updated_at": str(profile_payload.get("profile_updated_at") or ""),
        "profile": profile,
        "models_payload": models_payload,
        "followed_model": selected or {},
        "strategy_params": params,
    }


class FrontendProfileReadService:
    def __init__(
        self,
        *,
        request_username: Callable[[Any], str],
        frontend_user_profile: Callable[[str], Dict[str, Any]],
        strategy_models_payload: Callable[..., Dict[str, Any]],
        model_lookup: Callable[..., Optional[Dict[str, Any]]],
        active_strategy_model: Callable[[], Dict[str, Any]],
        strategy_params: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
        update_user_profile: Callable[[str, Dict[str, Any]], Any],
    ) -> None:
        self._request_username = request_username
        self._frontend_user_profile = frontend_user_profile
        self._strategy_models_payload = strategy_models_payload
        self._model_lookup = model_lookup
        self._active_strategy_model = active_strategy_model
        self._strategy_params = strategy_params
        self._update_user_profile = update_user_profile

    def profile_payload(self, request: Any) -> Dict[str, Any]:
        return self._frontend_user_profile(self._request_username(request))

    def profile_context_for_username(
        self,
        username: str,
        include_catalog: bool = True,
        fallback_catalog_on_missing: bool = True,
        profile_payload: Optional[Dict[str, Any]] = None,
        resolved_model: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return resolve_frontend_follow_context(
            username,
            include_catalog=include_catalog,
            fallback_catalog_on_missing=fallback_catalog_on_missing,
            profile_payload=profile_payload,
            resolved_model=resolved_model,
            frontend_user_profile=self._frontend_user_profile,
            strategy_models_payload=self._strategy_models_payload,
            model_lookup=self._model_lookup,
            active_strategy_model=self._active_strategy_model,
            strategy_params=self._strategy_params,
            update_user_profile=self._update_user_profile,
        )

    def profile_context(
        self,
        request: Any,
        include_catalog: bool = True,
        fallback_catalog_on_missing: bool = True,
        profile_payload: Optional[Dict[str, Any]] = None,
        resolved_model: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.profile_context_for_username(
            self._request_username(request),
            include_catalog=include_catalog,
            fallback_catalog_on_missing=fallback_catalog_on_missing,
            profile_payload=profile_payload,
            resolved_model=resolved_model,
        )

    def strategy_models_route_payload(self, request: Any) -> Dict[str, Any]:
        context = self.profile_context(request, include_catalog=True)
        return {
            "status": "ok",
            "frontend_profile": context["profile"],
            "followed_model": context["followed_model"],
            "strategy_models": context["models_payload"],
            "strategy_catalog_included": True,
        }
