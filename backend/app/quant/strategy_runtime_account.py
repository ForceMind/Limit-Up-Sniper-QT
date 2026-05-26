from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


DigestPayload = Callable[..., str]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "--"}:
            return default
        return float(text)
    except Exception:
        return default


def runtime_cache_key(
    *,
    model_id: str,
    params: Dict[str, Any],
    initial_cash: Any,
    start_date: Optional[str],
    as_of: Optional[str],
    limit: int,
    model_version: str = "",
    digest: DigestPayload,
) -> tuple[str, str]:
    clean_model_id = str(model_id or "active").strip() or "active"
    params_hash = digest("strategy_params", params or {})[:24]
    key = digest(
        "strategy_runtime_snapshot",
        clean_model_id,
        str(model_version or ""),
        params_hash,
        round(_safe_float(initial_cash, 0), 2),
        str(start_date or ""),
        str(as_of or ""),
        int(limit or 0),
    )[:32]
    return key, params_hash


def user_follow_snapshot_key(
    *,
    username: str,
    model_id: str,
    params: Dict[str, Any],
    initial_cash: Any,
    follow_start_date: Optional[str],
    as_of: Optional[str],
    limit: int,
    model_version: str = "",
    digest: DigestPayload,
) -> tuple[str, str]:
    clean_username = str(username or "anonymous").strip() or "anonymous"
    clean_model_id = str(model_id or "active").strip() or "active"
    params_hash = digest("strategy_params", params or {})[:24]
    key = digest(
        "user_follow_snapshot",
        clean_username,
        clean_model_id,
        str(model_version or ""),
        params_hash,
        round(_safe_float(initial_cash, 0), 2),
        str(follow_start_date or ""),
        str(as_of or ""),
        int(limit or 0),
    )[:32]
    return key, params_hash


def runtime_date_filter(
    *,
    table: str,
    date_column: str,
    model_id: str,
    model_version: str,
    start_date: Optional[str],
    as_of: Optional[str],
    params_hash: str = "",
) -> tuple[str, list[Any]]:
    where = ["model_id = ?"]
    params: list[Any] = [model_id]
    if model_version:
        where.append("model_version = ?")
        params.append(model_version)
    if params_hash:
        where.append("params_hash = ?")
        params.append(params_hash)
    if start_date:
        where.append(f"{date_column} >= ?")
        params.append(str(start_date))
    if as_of:
        where.append(f"{date_column} <= ?")
        params.append(str(as_of))
    return " AND ".join(where), params


def daily_runtime_source_filter(prefix: str, upper_bound: str) -> tuple[str, list[str]]:
    return "source >= ? AND source < ?", [prefix, upper_bound]


def scale_runtime_trades(trades: List[Dict[str, Any]], base_cash: float, target_cash: float) -> List[Dict[str, Any]]:
    if base_cash <= 0 or target_cash <= 0:
        return [dict(trade) for trade in trades if isinstance(trade, dict)]
    scale = target_cash / base_cash
    if abs(scale - 1.0) < 0.0001:
        return [dict(trade) for trade in trades if isinstance(trade, dict)]
    scaled: List[Dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        qty = _safe_float(trade.get("qty"), 0)
        price = _safe_float(trade.get("price"), 0)
        if qty <= 0 or price <= 0:
            continue
        scaled_qty = math.floor(qty * scale / 100) * 100
        if scaled_qty <= 0:
            continue
        item = dict(trade)
        item["qty"] = scaled_qty
        item["amount"] = round(scaled_qty * price, 2)
        item["scaled_from_cash"] = round(base_cash, 2)
        item["scaled_to_cash"] = round(target_cash, 2)
        scaled.append(item)
    return scaled


def runtime_snapshot_payload(
    *,
    snapshot_row: Any,
    model_id: str,
    selected_version: str,
    selected_start_date: Optional[str],
    requested_start_date: Optional[str],
    as_of: Optional[str],
    target_cash: float,
    generated_at: str,
    scope: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        loaded = json.loads(str(snapshot_row["account_json"] or "{}"))
    except Exception:
        return None
    if not isinstance(loaded, dict) or not loaded:
        return None
    account = dict(loaded)
    account.setdefault("status", "ok")
    account.setdefault("as_of", str(snapshot_row["as_of"] or as_of or ""))
    account.setdefault("start_date", str(snapshot_row["start_date"] or selected_start_date or ""))
    positions = account.get("positions") if isinstance(account.get("positions"), list) else []
    today_deals = account.get("today_deals") if isinstance(account.get("today_deals"), list) else []
    history_deals = account.get("history_deals") if isinstance(account.get("history_deals"), list) else []
    delivery_records = account.get("delivery_records") if isinstance(account.get("delivery_records"), list) else []
    daily_settlements = account.get("daily_settlements") if isinstance(account.get("daily_settlements"), list) else []
    account["positions"] = [dict(item) for item in positions if isinstance(item, dict)]
    account["today_deals"] = [dict(item) for item in today_deals if isinstance(item, dict)]
    account["history_deals"] = [dict(item) for item in history_deals if isinstance(item, dict)]
    account["delivery_records"] = [dict(item) for item in delivery_records if isinstance(item, dict)]
    account["daily_settlements"] = [dict(item) for item in daily_settlements if isinstance(item, dict)]
    base_cash = _safe_float(snapshot_row["initial_cash"], target_cash)
    account["strategy_account_source"] = "runtime_snapshot"
    account["strategy_account_cache"] = "runtime"
    account["follow_start_date"] = str(requested_start_date or "")
    account["runtime_data_start_date"] = selected_start_date or ""
    account["runtime_model_id"] = model_id
    account["runtime_model_version"] = selected_version
    account["runtime_trade_count"] = int(_safe_float(snapshot_row["deal_count"], 0))
    account["runtime_scaled_trade_count"] = len(account.get("history_deals", []))
    account["runtime_position_count"] = int(_safe_float(snapshot_row["position_count"], 0))
    account["runtime_scaled_from_cash"] = round(base_cash, 2)
    account["runtime_scaled_to_cash"] = round(target_cash, 2)
    account["runtime_generated_at"] = generated_at
    account["runtime_fallback_latest_snapshot"] = bool(scope.get("fallback_latest_snapshot"))
    account["runtime_relaxed_params_hash"] = bool(scope.get("relaxed_params_hash"))
    account["runtime_relaxed_model_version"] = bool(scope.get("relaxed_model_version"))
    account["runtime_snapshot_as_of"] = str(snapshot_row["as_of"] or "")
    account["runtime_snapshot_source"] = str(snapshot_row["source"] or "")
    account["runtime_snapshot_total_asset"] = round(_safe_float(snapshot_row["total_asset"], 0), 2)
    account["runtime_snapshot_return_pct"] = round(_safe_float(snapshot_row["return_pct"], 0), 3)
    account["runtime_snapshot_fast_path"] = True
    return account


def cache_is_fresh(generated_at: str, *, ttl_seconds: int, now: Optional[datetime] = None) -> bool:
    if ttl_seconds <= 0:
        return False
    try:
        generated = datetime.fromisoformat(str(generated_at or ""))
    except Exception:
        return False
    current = now or datetime.now()
    return (current - generated).total_seconds() <= ttl_seconds
