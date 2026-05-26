from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.quant.capital_strategy import (
    DEFAULT_FRONTEND_STRATEGY_ID,
    apply_capital_constraints,
    capital_presets,
    recommended_strategy_id,
)
from app.quant.engine import quant_engine
from app.quant.engine_utils import safe_float
from app.quant.evolution import strategy_evolution
from app.quant.runtime_cache import env_int, load_payload_cache, save_payload_cache
from app.quant.runtime_policy import target_strategy_count
from app.quant.security import frontend_user_summary


def frontend_payload_cache_ttl(name: str, default: int) -> int:
    return env_int(name, default, minimum=0, maximum=86400)


def frontend_account_as_of(as_of: Optional[str]) -> Optional[str]:
    latest = str(quant_engine.latest_event_date() or "").strip()
    requested = str(as_of or "").strip()
    if requested and latest and requested > latest:
        return latest
    return requested or latest or None


def frontend_replay_start_date(end_date: Optional[str]) -> Optional[str]:
    first = str(quant_engine.first_data_date() or "").strip()
    if not end_date:
        return first or None
    replay_days = max(20, min(env_int("QT_FRONTEND_ACCOUNT_REPLAY_DAYS", 90, minimum=1, maximum=260), 260))
    try:
        start = datetime.strptime(end_date[:10], "%Y-%m-%d") - timedelta(days=replay_days)
        start_text = start.strftime("%Y-%m-%d")
        return max(first, start_text) if first else start_text
    except Exception:
        return first or None


def _active_strategy_model() -> Dict[str, Any]:
    return {
        "id": "active",
        "name": "系统默认基础参数（非跟随策略）",
        "source": "baseline",
        "reusable": False,
        "description": "用于诊断和默认调参，不代表任何用户正在跟随。",
        "params": quant_engine.strategy_params(),
        "strategy_source": quant_engine.strategy_source(),
    }


def _strategy_models_payload() -> Dict[str, Any]:
    clean_target_count = max(1, min(int(target_strategy_count()), 200))
    presets = capital_presets(quant_engine.strategy_params())[:clean_target_count]
    catalog_limit = max(0, clean_target_count - len(presets))
    payload = strategy_evolution.models(limit=max(1, catalog_limit), include_records=False) if catalog_limit else {"status": "ok", "items": [], "count": 0}
    if not isinstance(payload, dict):
        payload = {"status": "ok", "active": _active_strategy_model(), "items": [], "count": 0}
    payload["active"] = {**_active_strategy_model(), **(payload.get("active") if isinstance(payload.get("active"), dict) else {})}
    raw_items = payload.get("items") if isinstance(payload.get("items"), list) else []
    payload["items"] = [dict(item) for item in raw_items if isinstance(item, dict) and item.get("reusable", True)][:catalog_limit]
    payload["count"] = len(payload["items"])
    payload["capital_presets"] = presets
    payload["target_strategy_count"] = clean_target_count
    return payload


def _strategy_catalog_items(models_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in models_payload.get("capital_presets") if isinstance(models_payload.get("capital_presets"), list) else []:
        if isinstance(item, dict):
            items.append(item)
    active = models_payload.get("active") if isinstance(models_payload.get("active"), dict) else {}
    if active:
        items.append({**active, "id": str(active.get("id") or "active")})
    for item in models_payload.get("items") if isinstance(models_payload.get("items"), list) else []:
        if isinstance(item, dict):
            items.append(item)
    seen: set[str] = set()
    unique: List[Dict[str, Any]] = []
    for item in items:
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        unique.append(item)
    return unique


def _frontend_followed_model_version(context: Dict[str, Any]) -> str:
    model = context.get("followed_model") if isinstance(context.get("followed_model"), dict) else {}
    record_counts = model.get("record_counts") if isinstance(model.get("record_counts"), dict) else {}
    return "|".join(
        [
            str(model.get("id") or ""),
            str(model.get("run_id") or ""),
            str(model.get("generated_at") or ""),
            str(model.get("rank") or ""),
            json.dumps(record_counts, ensure_ascii=False, sort_keys=True, default=str),
        ]
    )


def frontend_payload_cache_parts(context: Dict[str, Any], payload_type: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    return {
        "payload_type": payload_type,
        "strategy_model_id": str(profile.get("strategy_model_id") or ""),
        "simulated_cash": round(safe_float(profile.get("simulated_cash"), 0), 2),
        "model_version": _frontend_followed_model_version(context),
        "strategy_params": context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {},
        **extra,
    }


def affordable_payload(payload: Dict[str, Any], context: Dict[str, Any], as_of: Optional[str]) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
    cash = safe_float(profile.get("simulated_cash"), params.get("account_initial_cash", 0))
    max_positions = max(1.0, safe_float(params.get("max_positions"), 1))
    position_cash = min(safe_float(params.get("paper_position_value"), cash), cash / max_positions if max_positions else cash)
    if cash <= 0:
        return payload

    def enrich(item: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(item)
        code = str(row.get("code") or "").strip()
        price = safe_float(row.get("price") or row.get("current") or row.get("close"), 0)
        if price <= 0 and code:
            latest = quant_engine.latest_price(code, as_of=as_of)
            price = safe_float((latest or {}).get("close"), 0)
        lot_amount = price * 100 if price > 0 else 0.0
        max_qty = math.floor(position_cash / price / 100) * 100 if price > 0 else 0
        affordable = bool(price > 0 and max_qty >= 100 and lot_amount <= cash)
        row["estimated_price"] = round(price, 3) if price > 0 else 0.0
        row["min_lot_amount"] = round(lot_amount, 2)
        row["max_buy_qty"] = int(max_qty)
        row["affordable"] = affordable
        if not affordable:
            row["capital_note"] = "模拟资金不足以买入一手" if price > 0 else "缺少可用行情，暂不能估算一手金额"
        elif cash <= 50_000:
            row["capital_note"] = "小资金可买一手"
        return row

    next_payload = dict(payload)
    for key in ("items", "buy_list"):
        values = payload.get(key)
        if isinstance(values, list):
            enriched = [enrich(item) if isinstance(item, dict) else item for item in values]
            if cash <= 50_000:
                enriched.sort(
                    key=lambda item: (
                        0 if isinstance(item, dict) and item.get("affordable") else 1,
                        -safe_float(item.get("buy_score"), 0) if isinstance(item, dict) else 0,
                    )
                )
            next_payload[key] = enriched
    next_payload["capital_filter"] = {
        "simulated_cash": round(cash, 2),
        "position_cash": round(position_cash, 2),
        "max_positions": int(max_positions),
        "small_cash_mode": cash <= 50_000,
    }
    if isinstance(params, dict):
        next_payload["strategy_params"] = {
            **(next_payload.get("strategy_params") if isinstance(next_payload.get("strategy_params"), dict) else {}),
            **params,
        }
    return next_payload


def frontend_user_contexts(usernames: Optional[Iterable[str]] = None, limit_users: int = 50) -> List[Dict[str, Any]]:
    username_filter = {str(item or "").strip() for item in usernames or [] if str(item or "").strip()}
    summary = frontend_user_summary()
    raw_items = summary.get("items") if isinstance(summary.get("items"), list) else []
    models_payload = _strategy_models_payload()
    model_items = _strategy_catalog_items(models_payload)
    contexts: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict) or item.get("disabled"):
            continue
        username = str(item.get("username") or "").strip()
        if not username or (username_filter and username not in username_filter):
            continue
        profile = dict(item.get("profile") if isinstance(item.get("profile"), dict) else {})
        simulated_cash = max(10_000.0, min(10_000_000.0, safe_float(profile.get("simulated_cash"), 10_000.0)))
        selected_id = str(profile.get("strategy_model_id") or "").strip()
        selected = next((model for model in model_items if str(model.get("id") or "") == selected_id), None)
        recommended_id = recommended_strategy_id(simulated_cash, model_items)
        if not selected_id or selected_id == "active" or selected is None:
            selected_id = recommended_id or DEFAULT_FRONTEND_STRATEGY_ID
            selected = next((model for model in model_items if str(model.get("id") or "") == selected_id), None)
        if not selected:
            selected = _active_strategy_model()
            selected_id = "active"
        params = quant_engine.strategy_params(selected.get("params") if isinstance(selected.get("params"), dict) else {})
        params = apply_capital_constraints(params, simulated_cash)
        profile["simulated_cash"] = round(simulated_cash, 2)
        profile["strategy_model_id"] = selected_id
        profile["recommended_strategy_model_id"] = recommended_id
        profile["capital_mode"] = str(params.get("capital_mode") or "")
        profile["capital_label"] = str(params.get("capital_label") or "")
        contexts.append(
            {
                "username": username,
                "created_at": str(item.get("created_at") or ""),
                "profile_updated_at": str(item.get("profile_updated_at") or ""),
                "profile": profile,
                "followed_model": selected or {},
                "strategy_params": params,
            }
        )
        if len(contexts) >= max(1, min(int(limit_users or 50), 500)):
            break
    return contexts


def _normalize_usernames(usernames: Optional[Any]) -> List[str]:
    if usernames is None:
        return []
    if isinstance(usernames, str):
        return [item.strip() for item in usernames.split(",") if item.strip()]
    if isinstance(usernames, Iterable):
        return [str(item or "").strip() for item in usernames if str(item or "").strip()]
    return []


def precompute_frontend_payloads(
    as_of: Optional[str] = None,
    usernames: Optional[Any] = None,
    limit_users: int = 8,
    force: bool = False,
    lookback_days: int = 2,
    top_n: int = 30,
    limit_days: int = 30,
    max_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    started_at = time.monotonic()
    effective_as_of = frontend_account_as_of(as_of)
    clean_usernames = _normalize_usernames(usernames)
    clean_limit = max(1, min(int(limit_users or 8), 500))
    clean_lookback = max(1, min(int(lookback_days or 2), 20))
    clean_top_n = max(1, min(int(top_n or 30), 100))
    clean_limit_days = max(1, min(int(limit_days or 30), 500))
    clean_max_seconds = (
        frontend_payload_cache_ttl("QT_FRONT_PAYLOAD_PRECOMPUTE_MAX_SECONDS", 20)
        if max_seconds is None
        else max(0, min(int(max_seconds or 0), 86400))
    )
    effective_start = frontend_replay_start_date(effective_as_of)
    rec_ttl = frontend_payload_cache_ttl("QT_FRONT_RECOMMENDATIONS_CACHE_TTL_SECONDS", 1800)
    plan_ttl = frontend_payload_cache_ttl("QT_FRONT_DAILY_PLAN_CACHE_TTL_SECONDS", 1800)
    contexts = frontend_user_contexts(clean_usernames, limit_users=clean_limit)
    results: List[Dict[str, Any]] = []
    saved = 0
    skipped = 0
    deferred = 0
    processed_users = 0
    remaining_users = 0
    budget_exhausted = False
    errors: List[Dict[str, Any]] = []

    def exhausted() -> bool:
        return clean_max_seconds > 0 and time.monotonic() - started_at >= clean_max_seconds

    for index, context in enumerate(contexts):
        if exhausted():
            budget_exhausted = True
            remaining_users = len(contexts) - index
            deferred += remaining_users * 2
            break
        username = str(context.get("username") or "")
        row: Dict[str, Any] = {"username": username, "strategy_model_id": (context.get("profile") or {}).get("strategy_model_id")}
        processed_users += 1
        try:
            rec_parts = frontend_payload_cache_parts(
                context,
                "front_recommendations",
                {"as_of": effective_as_of, "lookback_days": clean_lookback, "top_n": clean_top_n},
            )
            if not force and load_payload_cache("front_recommendations", rec_parts, rec_ttl):
                row["recommendations"] = "hit"
                skipped += 1
            elif exhausted():
                budget_exhausted = True
                row["recommendations"] = "deferred"
                deferred += 1
            else:
                with quant_engine.temporary_strategy_params(context["strategy_params"]):
                    payload = quant_engine.recommendations(as_of=effective_as_of, lookback_days=clean_lookback, top_n=clean_top_n)
                payload = affordable_payload(payload, context, effective_as_of)
                payload["frontend_payload_cache"] = "precomputed"
                save_payload_cache("front_recommendations", rec_parts, payload, rec_ttl)
                row["recommendations"] = "saved"
                saved += 1

            plan_parts = frontend_payload_cache_parts(
                context,
                "front_daily_plan",
                {"as_of": effective_as_of, "start_date": effective_start, "limit_days": clean_limit_days},
            )
            if not force and load_payload_cache("front_daily_plan", plan_parts, plan_ttl):
                row["daily_plan"] = "hit"
                skipped += 1
            elif exhausted():
                budget_exhausted = True
                row["daily_plan"] = "deferred"
                deferred += 1
            else:
                with quant_engine.temporary_strategy_params(context["strategy_params"]):
                    payload = quant_engine.daily_plan(
                        as_of=effective_as_of,
                        start_date=effective_start,
                        limit_days=clean_limit_days,
                    )
                payload = affordable_payload(payload, context, effective_as_of)
                payload["frontend_payload_cache"] = "precomputed"
                save_payload_cache("front_daily_plan", plan_parts, payload, plan_ttl)
                row["daily_plan"] = "saved"
                saved += 1
        except Exception as exc:
            row["status"] = "error"
            row["error"] = str(exc)
            errors.append({"username": username, "error": str(exc)})
        results.append(row)

    status = "ok" if not errors and not budget_exhausted else ("partial" if saved or skipped or deferred else "error")
    return {
        "status": status,
        "job": "frontend_payload_precompute",
        "as_of": effective_as_of,
        "start_date": effective_start,
        "user_count": len(contexts),
        "processed_users": processed_users,
        "remaining_users": remaining_users,
        "saved": saved,
        "skipped": skipped,
        "deferred": deferred,
        "budget_exhausted": budget_exhausted,
        "error_count": len(errors),
        "errors": errors[:20],
        "items": results,
        "force": bool(force),
        "lookback_days": clean_lookback,
        "top_n": clean_top_n,
        "limit_days": clean_limit_days,
        "max_seconds": clean_max_seconds,
        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ttl_seconds": {"front_recommendations": rec_ttl, "front_daily_plan": plan_ttl},
        "process_enabled": str(os.getenv("QT_FRONT_PAYLOAD_PRECOMPUTE_PROCESS_ENABLED") or "").strip().lower() not in {"", "0", "false", "no", "off"},
    }
