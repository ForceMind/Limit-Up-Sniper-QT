import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.front_profile import resolve_front_profile_updates, strategy_catalog_items


def test_strategy_catalog_items_dedupes_presets_active_and_models():
    items = strategy_catalog_items(
        {
            "capital_presets": [{"id": "capital_10000"}, {"id": "model-a", "name": "Preset A"}],
            "active": {"params": {"max_positions": 1}},
            "items": [{"id": "model-a", "name": "Model A"}, {"id": "model-b", "name": "Model B"}],
        }
    )

    assert [item["id"] for item in items] == ["capital_10000", "model-a", "active", "model-b"]
    assert items[1]["name"] == "Preset A"


def test_resolve_front_profile_updates_uses_single_model_loader_for_light_model():
    model_calls = []
    catalog_calls = []

    def models_loader(include_catalog):
        catalog_calls.append(include_catalog)
        return {
            "capital_presets": [{"id": "capital_10000"}, {"id": "capital_20000_50000"}],
            "items": [],
            "active": {},
        }

    def model_loader(model_id, include_records=False):
        model_calls.append((model_id, include_records))
        return {"id": model_id, "params": {"max_positions": 2}}

    updates, resolved_model = resolve_front_profile_updates(
        {"simulated_cash": 25000, "strategy_model_id": "model-b"},
        {"simulated_cash": 10000, "strategy_model_id": "capital_10000"},
        False,
        models_loader,
        model_loader,
    )

    assert updates["strategy_model_id"] == "model-b"
    assert resolved_model and resolved_model["id"] == "model-b"
    assert catalog_calls == [False]
    assert model_calls == [("model-b", False)]


def test_resolve_front_profile_updates_recommends_capital_band_for_missing_model():
    def models_loader(include_catalog):
        assert include_catalog is False
        return {
            "capital_presets": [
                {"id": "capital_10000", "capital_min": 10000, "capital_max": 20000},
                {"id": "capital_20000_50000", "capital_min": 20000, "capital_max": 50000},
            ],
            "items": [],
            "active": {},
        }

    updates, resolved_model = resolve_front_profile_updates(
        {"simulated_cash": 30000, "strategy_model_id": "stale-model"},
        {},
        False,
        models_loader,
        lambda *_args, **_kwargs: {},
    )

    assert updates["strategy_model_id"] == "capital_20000_50000"
    assert resolved_model is None
