from typing import Any, Dict, Iterable, Mapping

from app.quant.engine import safe_float
from app.quant.front_profile import strategy_catalog_items


def clean_strategy_runtime_matrix_limit(limit_models: Any, default: int = 80) -> int:
    return max(1, min(int(safe_float(limit_models, default)), 200))


def strategy_runtime_catalog_items(models_payload: Mapping[str, Any], limit_models: Any = 80) -> list[Dict[str, Any]]:
    clean_limit = clean_strategy_runtime_matrix_limit(limit_models)
    items: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in strategy_catalog_items(models_payload):
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("model_id") or "").strip()
        if not model_id or model_id == "active" or model_id in seen:
            continue
        seen.add(model_id)
        items.append(item)
        if len(items) >= clean_limit:
            break
    return items


def build_strategy_runtime_matrix_payload(
    *,
    effective_as_of: str,
    catalog_items: Iterable[Mapping[str, Any]],
    runtime_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    signal_feed: Mapping[str, Any] | None = None,
    include_signals: bool = True,
) -> Dict[str, Any]:
    summaries = runtime_summaries if isinstance(runtime_summaries, Mapping) else {}
    feed: Mapping[str, Any] = signal_feed if isinstance(signal_feed, Mapping) else {"status": "skipped", "items": [], "data_date": ""}
    signal_groups = feed.get("items") if isinstance(feed.get("items"), list) else []
    signal_by_model = {
        str(group.get("model_id") or ""): group
        for group in signal_groups
        if isinstance(group, Mapping)
    }

    rows: list[Dict[str, Any]] = []
    for index, model in enumerate(catalog_items, start=1):
        if not isinstance(model, Mapping):
            continue
        model_id = str(model.get("id") or model.get("model_id") or "").strip()
        if not model_id:
            continue
        summary = summaries.get(model_id) if isinstance(summaries, Mapping) else None
        if not isinstance(summary, Mapping):
            summary = {}
        signal_group = signal_by_model.get(model_id) or {}
        signals = signal_group.get("signals") if isinstance(signal_group.get("signals"), list) else []
        latest_signal = next((signal for signal in signals if isinstance(signal, Mapping)), {})
        has_runtime = bool(summary.get("has_runtime_data") or model.get("has_runtime_data"))
        signal_count = int(safe_float(signal_group.get("signal_count"), len(signals)))
        params = model.get("params") if isinstance(model.get("params"), Mapping) else {}
        rows.append(
            {
                "rank": index,
                "model_id": model_id,
                "name": str(model.get("name") or model.get("model_name") or model_id),
                "source": str(model.get("source") or ""),
                "runtime_status": "ready" if has_runtime else ("signals_only" if signal_count > 0 else "missing"),
                "has_runtime_data": has_runtime,
                "runtime_source": str(summary.get("runtime_source") or model.get("runtime_source") or ""),
                "runtime_start_date": str(summary.get("runtime_start_date") or model.get("runtime_start_date") or ""),
                "runtime_end_date": str(summary.get("runtime_end_date") or model.get("runtime_end_date") or ""),
                "runtime_day_count": int(safe_float(summary.get("runtime_day_count"), safe_float(model.get("runtime_day_count"), 0))),
                "trade_count": int(
                    safe_float(
                        summary.get("trade_count"),
                        safe_float(summary.get("deal_count"), safe_float(model.get("trade_count"), 0)),
                    )
                ),
                "closed_trades": int(safe_float(summary.get("closed_trades"), safe_float(model.get("closed_trades"), 0))),
                "position_count": int(safe_float(summary.get("position_count"), safe_float(model.get("position_count"), 0))),
                "win_rate": round(safe_float(summary.get("win_rate"), safe_float(model.get("win_rate"), 0)), 4),
                "return_pct": round(safe_float(summary.get("return_pct"), safe_float(model.get("return_pct"), 0)), 4),
                "max_drawdown_pct": round(
                    safe_float(summary.get("max_drawdown_pct"), safe_float(model.get("max_drawdown_pct"), 0)),
                    4,
                ),
                "final_value": round(safe_float(summary.get("final_value"), safe_float(model.get("final_value"), 0)), 2),
                "initial_cash": round(
                    safe_float(
                        params.get("account_initial_cash"),
                        safe_float(model.get("initial_cash"), 0),
                    ),
                    2,
                ),
                "capital_min": model.get("capital_min"),
                "capital_max": model.get("capital_max"),
                "signal_count": signal_count,
                "latest_signal_date": str(
                    latest_signal.get("date")
                    or latest_signal.get("data_date")
                    or signal_group.get("data_date")
                    or feed.get("data_date")
                    or ""
                ),
                "latest_signal_code": str(latest_signal.get("code") or ""),
                "latest_signal_name": str(latest_signal.get("name") or ""),
                "generated_at": str(summary.get("generated_at") or signal_group.get("generated_at") or ""),
            }
        )

    ready_count = sum(1 for row in rows if row.get("has_runtime_data"))
    signal_ready_count = sum(1 for row in rows if int(safe_float(row.get("signal_count"), 0)) > 0)
    return {
        "status": "ok",
        "as_of": effective_as_of,
        "data_date": feed.get("data_date") or "",
        "count": len(rows),
        "ready_count": ready_count,
        "missing_count": len(rows) - ready_count,
        "signal_ready_count": signal_ready_count,
        "include_signals": bool(include_signals),
        "items": rows,
    }
