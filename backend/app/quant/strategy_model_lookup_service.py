from __future__ import annotations

from typing import Any, Callable, Dict


class StrategyModelLookupNotFound(Exception):
    pass


class StrategyModelLookupService:
    def __init__(
        self,
        *,
        model_lookup: Callable[..., Dict[str, Any] | None],
        strategy_models_payload: Callable[..., Dict[str, Any]],
        strategy_catalog_items: Callable[[Dict[str, Any]], list[Dict[str, Any]]],
    ) -> None:
        self._model_lookup = model_lookup
        self._strategy_models_payload = strategy_models_payload
        self._strategy_catalog_items = strategy_catalog_items

    def find_model(self, model_id: str, include_records: bool = True) -> Dict[str, Any]:
        clean_model_id = str(model_id or "active").strip() or "active"
        model = self._model_lookup(clean_model_id, include_records=include_records)
        if model:
            return model
        models_payload = self._strategy_models_payload(include_catalog=True)
        catalog_model = next(
            (
                item
                for item in self._strategy_catalog_items(models_payload)
                if str(item.get("id") or "") == clean_model_id
            ),
            None,
        )
        if catalog_model:
            return catalog_model
        raise StrategyModelLookupNotFound(clean_model_id)
