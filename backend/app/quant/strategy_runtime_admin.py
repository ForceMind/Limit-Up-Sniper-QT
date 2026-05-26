from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from app.quant.engine_utils import safe_float
from app.quant.evolution import strategy_evolution
from app.quant.front_profile import strategy_catalog_items
from app.quant.runtime_policy import target_strategy_count
from app.quant.strategy_runtime_matrix import (
    build_strategy_runtime_matrix_payload,
    build_strategy_runtime_overview_payload,
    clean_strategy_runtime_matrix_limit,
    strategy_runtime_catalog_items,
)


ResolveAsOf = Callable[[Optional[str]], str]
StrategyModelsPayload = Callable[..., Dict[str, Any]]
ModelSignalFeedPayload = Callable[..., Dict[str, Any]]
ApplyCapitalConstraints = Callable[[Dict[str, Any], float], Dict[str, Any]]
TradingAccountPayload = Callable[[Optional[str], Optional[str], Optional[float], Optional[str], int], Dict[str, Any]]


class StrategyRuntimeModelNotFound(ValueError):
    pass


class AdminStrategyRuntimeReadService:
    def __init__(
        self,
        *,
        resolve_as_of: ResolveAsOf,
        strategy_models_payload: StrategyModelsPayload,
        model_signal_feed_payload: Optional[ModelSignalFeedPayload] = None,
        model_signal_feed: Optional[ModelSignalFeedPayload] = None,
        quant_engine: Any,
        strategy_evolution_service: Any,
        default_strategy_id: str,
        apply_capital_constraints: ApplyCapitalConstraints,
    ) -> None:
        self._resolve_as_of = resolve_as_of
        self._strategy_models_payload = strategy_models_payload
        self._model_signal_feed_payload = model_signal_feed_payload
        self._model_signal_feed = model_signal_feed
        self._quant_engine = quant_engine
        self._strategy_evolution_service = strategy_evolution_service
        self._default_strategy_id = default_strategy_id
        self._apply_capital_constraints = apply_capital_constraints

    def signal_feed_payload(
        self,
        as_of: Optional[str],
        models_payload: Optional[Dict[str, Any]] = None,
        limit_models: int = 24,
        limit_per_model: int = 12,
    ) -> Dict[str, Any]:
        if self._model_signal_feed_payload is not None:
            return self._model_signal_feed_payload(
                as_of,
                models_payload=models_payload,
                limit_models=limit_models,
                limit_per_model=limit_per_model,
            )
        if self._model_signal_feed is None:
            raise RuntimeError("AdminStrategyRuntimeReadService model signal feed is not configured")
        return admin_model_signal_feed(
            as_of,
            models_payload=models_payload,
            limit_models=limit_models,
            limit_per_model=limit_per_model,
            strategy_models_payload=self._strategy_models_payload,
            model_signal_feed=self._model_signal_feed,
        )

    def model_signals_payload(
        self,
        as_of: Optional[str] = None,
        limit_models: int = 24,
        limit_per_model: int = 12,
    ) -> Dict[str, Any]:
        effective_as_of = self._resolve_as_of(as_of)
        models_payload = self._strategy_models_payload(include_catalog=True)
        return self.signal_feed_payload(
            effective_as_of,
            models_payload=models_payload,
            limit_models=limit_models,
            limit_per_model=limit_per_model,
        )

    def matrix_payload(
        self,
        as_of: Optional[str] = None,
        limit_models: int = 80,
        include_signals: bool = True,
    ) -> Dict[str, Any]:
        return admin_strategy_runtime_matrix_payload(
            as_of=as_of,
            limit_models=limit_models,
            include_signals=include_signals,
            resolve_as_of=self._resolve_as_of,
            strategy_models_payload=self._strategy_models_payload,
            model_signal_feed_payload=self.signal_feed_payload,
        )

    def overview_payload(
        self,
        as_of: Optional[str] = None,
        models_payload: Optional[Dict[str, Any]] = None,
        signal_feed: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return admin_strategy_runtime_overview_payload(
            as_of=as_of,
            models_payload=models_payload,
            signal_feed=signal_feed,
            resolve_as_of=self._resolve_as_of,
            strategy_models_payload=self._strategy_models_payload,
            model_signal_feed_payload=self.signal_feed_payload,
        )

    def trading_account_payload(
        self,
        as_of: Optional[str] = None,
        model_id: Optional[str] = None,
        initial_cash: Optional[float] = None,
        start_date: Optional[str] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        return admin_strategy_trading_account_payload(
            as_of=as_of,
            model_id=model_id,
            initial_cash=initial_cash,
            start_date=start_date,
            limit=limit,
            resolve_as_of=self._resolve_as_of,
            strategy_models_payload=self._strategy_models_payload,
            quant_engine=self._quant_engine,
            strategy_evolution_service=self._strategy_evolution_service,
            default_strategy_id=self._default_strategy_id,
            apply_capital_constraints=self._apply_capital_constraints,
        )

    def replay_payload(
        self,
        as_of: Optional[str] = None,
        model_id: Optional[str] = None,
        initial_cash: Optional[float] = None,
        start_date: Optional[str] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        return admin_strategy_runtime_replay_payload(
            as_of=as_of,
            model_id=model_id,
            initial_cash=initial_cash,
            start_date=start_date,
            limit=limit,
            trading_account_payload=self.trading_account_payload,
        )


def admin_model_signal_feed(
    as_of: Optional[str],
    *,
    models_payload: Optional[Dict[str, Any]] = None,
    limit_models: int = 24,
    limit_per_model: int = 12,
    strategy_models_payload: StrategyModelsPayload,
    model_signal_feed: ModelSignalFeedPayload,
) -> Dict[str, Any]:
    payload = model_signal_feed(
        as_of=as_of,
        limit_models=limit_models,
        limit_per_model=limit_per_model,
        fallback_latest=True,
    )
    catalog_payload = models_payload if isinstance(models_payload, dict) else strategy_models_payload(include_catalog=True)
    catalog_items = strategy_catalog_items(catalog_payload)
    catalog = {str(item.get("id") or ""): item for item in catalog_items}
    catalog_order = {str(item.get("id") or ""): index for index, item in enumerate(catalog_items)}
    for group in payload.get("items") if isinstance(payload.get("items"), list) else []:
        if not isinstance(group, dict):
            continue
        model_id = str(group.get("model_id") or "")
        meta = catalog.get(model_id)
        if not isinstance(meta, dict):
            continue
        group["model_name"] = str(meta.get("name") or group.get("model_name") or model_id)
        group["model_description"] = str(meta.get("description") or "")
        group["model_source"] = str(meta.get("source") or group.get("source") or "")
        for key in ("objective", "return_pct", "max_drawdown_pct", "win_rate", "closed_trades"):
            if group.get(key) in (None, "", 0) and meta.get(key) not in (None, ""):
                group[key] = meta.get(key)
        if meta.get("capital_min") is not None:
            group["capital_min"] = meta.get("capital_min")
        if meta.get("capital_max") is not None:
            group["capital_max"] = meta.get("capital_max")
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    items.sort(
        key=lambda group: (
            catalog_order.get(str(group.get("model_id") or ""), 999999),
            -safe_float(group.get("objective"), 0),
            str(group.get("model_id") or ""),
        )
    )
    return payload


def admin_strategy_runtime_matrix_payload(
    *,
    as_of: Optional[str] = None,
    limit_models: int = 80,
    include_signals: bool = True,
    resolve_as_of: ResolveAsOf,
    strategy_models_payload: StrategyModelsPayload,
    model_signal_feed_payload: ModelSignalFeedPayload,
) -> Dict[str, Any]:
    effective_as_of = resolve_as_of(as_of)
    clean_limit = clean_strategy_runtime_matrix_limit(limit_models)
    models_payload = strategy_models_payload(include_catalog=True)
    catalog_items = strategy_runtime_catalog_items(models_payload, clean_limit)
    runtime_summaries = strategy_evolution.runtime_model_summaries(catalog_items)
    signal_feed = (
        model_signal_feed_payload(
            effective_as_of,
            models_payload=models_payload,
            limit_models=min(clean_limit, 80),
            limit_per_model=1,
        )
        if include_signals
        else {"status": "skipped", "items": [], "data_date": ""}
    )
    payload = build_strategy_runtime_matrix_payload(
        effective_as_of=effective_as_of,
        catalog_items=catalog_items,
        runtime_summaries=runtime_summaries,
        signal_feed=signal_feed,
        include_signals=include_signals,
    )
    payload["overview"] = build_strategy_runtime_overview_payload(payload)
    return payload


def admin_strategy_runtime_overview_payload(
    *,
    as_of: Optional[str] = None,
    models_payload: Optional[Dict[str, Any]] = None,
    signal_feed: Optional[Dict[str, Any]] = None,
    resolve_as_of: ResolveAsOf,
    strategy_models_payload: StrategyModelsPayload,
    model_signal_feed_payload: ModelSignalFeedPayload,
) -> Dict[str, Any]:
    effective_as_of = resolve_as_of(as_of)
    catalog_payload = models_payload if isinstance(models_payload, dict) else strategy_models_payload(include_catalog=True)
    clean_limit = clean_strategy_runtime_matrix_limit(target_strategy_count())
    catalog_items = strategy_runtime_catalog_items(catalog_payload, clean_limit)
    runtime_summaries = strategy_evolution.runtime_model_summaries(catalog_items)
    feed = signal_feed if isinstance(signal_feed, dict) else model_signal_feed_payload(
        effective_as_of,
        models_payload=catalog_payload,
        limit_models=min(clean_limit, 80),
        limit_per_model=1,
    )
    matrix = build_strategy_runtime_matrix_payload(
        effective_as_of=effective_as_of,
        catalog_items=catalog_items,
        runtime_summaries=runtime_summaries,
        signal_feed=feed,
        include_signals=True,
    )
    return build_strategy_runtime_overview_payload(matrix, target_count=clean_limit)


def admin_strategy_trading_account_payload(
    *,
    as_of: Optional[str] = None,
    model_id: Optional[str] = None,
    initial_cash: Optional[float] = None,
    start_date: Optional[str] = None,
    limit: int = 1000,
    resolve_as_of: ResolveAsOf,
    strategy_models_payload: StrategyModelsPayload,
    quant_engine: Any,
    strategy_evolution_service: Any,
    default_strategy_id: str,
    apply_capital_constraints: ApplyCapitalConstraints,
) -> Dict[str, Any]:
    effective_as_of = resolve_as_of(as_of)
    models_payload = strategy_models_payload(include_catalog=True)
    catalog = [item for item in strategy_catalog_items(models_payload) if str((item or {}).get("id") or "") != "active"]
    requested_id = str(model_id or "").strip()
    if not requested_id:
        ready = next((item for item in catalog if item.get("has_runtime_data")), None)
        requested_id = str((ready or {}).get("id") or default_strategy_id)
    model = next((item for item in catalog if str(item.get("id") or "") == requested_id), None)
    if not model:
        raise StrategyRuntimeModelNotFound("strategy model not found")
    base_params = model.get("params") if isinstance(model.get("params"), dict) else {}
    cash = safe_float(initial_cash, safe_float(base_params.get("account_initial_cash"), safe_float(model.get("initial_cash"), 10_000)))
    params = quant_engine.strategy_params(base_params)
    params = apply_capital_constraints(params, cash)
    selected_start = str(start_date or model.get("runtime_start_date") or quant_engine.first_data_date() or "").strip() or None
    model_version = strategy_evolution_service.runtime_model_version(model)
    payload = strategy_evolution_service.load_runtime_account(
        requested_id,
        cash,
        selected_start,
        effective_as_of,
        limit,
        model_version=model_version,
        params=params,
    )
    if not payload:
        payload = {
            "status": "missing",
            "as_of": effective_as_of,
            "start_date": selected_start or "",
            "account": {
                "initial_cash": round(cash, 2),
                "total_asset": round(cash, 2),
                "cash": round(cash, 2),
                "available_cash": round(cash, 2),
                "market_value": 0,
                "position_count": 0,
                "deal_count": 0,
                "return_pct": 0,
            },
            "positions": [],
            "today_deals": [],
            "history_deals": [],
            "delivery_records": [],
            "daily_settlements": [],
            "message": "strategy runtime data is missing; run strategy replay or import merged replay data first.",
        }
    payload["strategy_name"] = str(model.get("name") or requested_id)
    payload["strategy_model_id"] = requested_id
    payload["strategy_model"] = model
    payload["strategy_account_source"] = payload.get("strategy_account_source") or "strategy_runtime"
    payload["strategy_scope"] = "strategy_runtime"
    payload["strategy_params"] = params
    payload["selected_initial_cash"] = round(cash, 2)
    payload["selected_start_date"] = selected_start or ""
    payload["selected_as_of"] = effective_as_of or ""
    return payload


def admin_strategy_runtime_replay_payload(
    *,
    as_of: Optional[str] = None,
    model_id: Optional[str] = None,
    initial_cash: Optional[float] = None,
    start_date: Optional[str] = None,
    limit: int = 1000,
    trading_account_payload: TradingAccountPayload,
) -> Dict[str, Any]:
    payload = trading_account_payload(as_of, model_id, initial_cash, start_date, limit)
    account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
    trades = payload.get("history_deals") if isinstance(payload.get("history_deals"), list) else []
    deliveries = payload.get("delivery_records") if isinstance(payload.get("delivery_records"), list) else trades
    settlements = payload.get("daily_settlements") if isinstance(payload.get("daily_settlements"), list) else []
    initial = safe_float(account.get("initial_cash"), safe_float(payload.get("selected_initial_cash"), 0))
    final_value = safe_float(account.get("total_asset"), initial)
    return_pct = safe_float(account.get("return_pct"), ((final_value / initial - 1) * 100 if initial > 0 else 0))
    sell_rows = [
        item
        for item in deliveries
        if str(item.get("side") or item.get("direction") or "").upper() in {"SELL", "卖出"}
    ]
    closed_trades = len(sell_rows)
    wins = [item for item in sell_rows if safe_float(item.get("realized_pnl"), 0) > 0]
    win_rate = round(len(wins) / closed_trades * 100, 2) if closed_trades else 0.0
    curve = []
    cumulative_realized = 0.0
    for row in sorted([item for item in settlements if isinstance(item, dict)], key=lambda item: str(item.get("date") or "")):
        cumulative_realized += safe_float(row.get("realized_pnl"), 0)
        value = initial + cumulative_realized
        curve.append(
            {
                "date": str(row.get("date") or ""),
                "total_value": round(value, 2),
                "return_pct": round((value / initial - 1) * 100, 3) if initial > 0 else 0.0,
                "deal_count": int(safe_float(row.get("deal_count"), 0)),
            }
        )
    if not curve and payload.get("selected_as_of"):
        curve.append(
            {
                "date": payload.get("selected_as_of"),
                "total_value": round(final_value, 2),
                "return_pct": round(return_pct, 3),
                "deal_count": len(trades),
            }
        )
    peak = initial if initial > 0 else 1.0
    max_drawdown = 0.0
    for point in curve:
        value = safe_float(point.get("total_value"), peak)
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100)
    return {
        "status": payload.get("status") or "ok",
        "source": "strategy_runtime",
        "model_id": payload.get("strategy_model_id"),
        "strategy_model_id": payload.get("strategy_model_id"),
        "strategy_name": payload.get("strategy_name"),
        "strategy_model": payload.get("strategy_model"),
        "mode": "strategy_runtime",
        "start_date": payload.get("selected_start_date") or payload.get("start_date") or "",
        "end_date": payload.get("selected_as_of") or payload.get("as_of") or "",
        "initial_cash": round(initial, 2),
        "final_value": round(final_value, 2),
        "return_pct": round(return_pct, 3),
        "max_drawdown_pct": round(max_drawdown, 3),
        "win_rate": win_rate,
        "closed_trades": closed_trades,
        "trade_count": len(trades),
        "runtime_signal_count": payload.get("runtime_signal_count", 0),
        "runtime_generated_at": payload.get("runtime_generated_at", ""),
        "account": account,
        "positions": payload.get("positions", []),
        "trades": trades,
        "trade_records": trades,
        "delivery_records": deliveries,
        "daily_settlements": settlements,
        "equity_curve": curve,
        "days": payload.get("days", []),
        "message": payload.get("message", ""),
        "strategy_params": payload.get("strategy_params", {}),
    }
