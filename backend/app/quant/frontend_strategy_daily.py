from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from app.quant.engine_utils import safe_float


ResolveAsOf = Callable[[Optional[str]], str]
RuntimeDailyPayload = Callable[[Optional[str]], Dict[str, Any]]
NewsPayload = Callable[..., Dict[str, Any]]


def _followed_model_id(context: Mapping[str, Any]) -> str:
    profile = context.get("profile") if isinstance(context.get("profile"), Mapping) else {}
    followed = context.get("followed_model") if isinstance(context.get("followed_model"), Mapping) else {}
    return str(profile.get("strategy_model_id") or followed.get("id") or followed.get("model_id") or "").strip()


def _followed_model_name(context: Mapping[str, Any], model_id: str) -> str:
    followed = context.get("followed_model") if isinstance(context.get("followed_model"), Mapping) else {}
    return str(followed.get("name") or followed.get("model_name") or model_id)


def _find_daily_model(daily_result: Mapping[str, Any], model_id: str) -> Dict[str, Any]:
    items = daily_result.get("items") if isinstance(daily_result.get("items"), list) else []
    for item in items:
        if isinstance(item, Mapping) and str(item.get("model_id") or "").strip() == model_id:
            return dict(item)
    return {}


def _daily_stock_refs(followed_daily: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    codes: list[str] = []
    names: list[str] = []
    for key in ("signals", "trades"):
        rows = followed_daily.get(key) if isinstance(followed_daily.get(key), list) else []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if code and code not in codes:
                codes.append(code)
            if name and name not in names:
                names.append(name)
    return codes, names


def _item_matches_stock(item: Mapping[str, Any], codes: set[str], names: set[str]) -> bool:
    code = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    if code and code in codes:
        return True
    if name and name in names:
        return True
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("title", "text", "reason", "summary")
    )
    return any(code and code in haystack for code in codes) or any(name and name in haystack for name in names)


def _related_news_payload(news: Mapping[str, Any], followed_daily: Mapping[str, Any], limit: int) -> Dict[str, Any]:
    codes, names = _daily_stock_refs(followed_daily)
    code_set = set(codes)
    name_set = set(names)
    if not code_set and not name_set:
        return {
            "status": "empty",
            "codes": [],
            "names": [],
            "events": [],
            "items": [],
            "count": 0,
        }
    clean_limit = max(1, min(int(safe_float(limit, 30)), 100))
    events = [
        dict(item)
        for item in news.get("events", [])
        if isinstance(item, Mapping) and _item_matches_stock(item, code_set, name_set)
    ][:clean_limit]
    items = [
        dict(item)
        for item in news.get("items", [])
        if isinstance(item, Mapping) and _item_matches_stock(item, code_set, name_set)
    ][:clean_limit]
    return {
        "status": "ok" if events or items else "empty",
        "codes": codes,
        "names": names,
        "events": events,
        "items": items,
        "count": len(events) + len(items),
    }


def frontend_strategy_daily_payload(
    *,
    context: Mapping[str, Any],
    as_of: Optional[str] = None,
    news_limit: int = 30,
    resolve_as_of: ResolveAsOf,
    runtime_daily_payload: RuntimeDailyPayload,
    news_payload: NewsPayload,
) -> Dict[str, Any]:
    effective_as_of = resolve_as_of(as_of)
    clean_news_limit = max(1, min(int(safe_float(news_limit, 30)), 100))
    runtime = runtime_daily_payload(effective_as_of)
    runtime = runtime if isinstance(runtime, dict) else {}
    daily_result = runtime.get("daily_result") if isinstance(runtime.get("daily_result"), Mapping) else {}
    model_id = _followed_model_id(context)
    followed_daily = _find_daily_model(daily_result, model_id) if model_id else {}
    news = news_payload(as_of=effective_as_of, limit=clean_news_limit, fallback_latest=True)
    news = news if isinstance(news, dict) else {"status": "pending", "items": [], "events": [], "count": 0}
    related_news = _related_news_payload(news, followed_daily, clean_news_limit)
    signals = followed_daily.get("signals") if isinstance(followed_daily.get("signals"), list) else []
    trades = followed_daily.get("trades") if isinstance(followed_daily.get("trades"), list) else []
    buy_trades = [item for item in trades if isinstance(item, Mapping) and str(item.get("side") or "").strip().upper() == "BUY"]
    sell_trades = [item for item in trades if isinstance(item, Mapping) and str(item.get("side") or "").strip().upper() == "SELL"]
    followed_ready = bool(followed_daily.get("ready_for_follow")) if "ready_for_follow" in followed_daily else bool(followed_daily)
    profile = context.get("profile") if isinstance(context.get("profile"), Mapping) else {}
    return {
        "status": "ok" if followed_ready else "pending",
        "mode": "frontend_followed_strategy_daily",
        "source": "persisted_strategy_runtime",
        "read_only": True,
        "as_of": effective_as_of,
        "data_date": str(daily_result.get("data_date") or runtime.get("as_of") or effective_as_of),
        "frontend_profile": dict(profile),
        "model_id": model_id,
        "strategy_name": _followed_model_name(context, model_id),
        "daily": followed_daily,
        "signals": signals,
        "trades": trades,
        "buy_trades": buy_trades,
        "sell_trades": sell_trades,
        "trade_summary": {
            "signal_count": followed_daily.get("signal_count", len(signals)) if followed_daily else 0,
            "trade_count": followed_daily.get("trade_count", len(trades)) if followed_daily else 0,
            "buy_count": followed_daily.get("buy_count", len(buy_trades)) if followed_daily else 0,
            "sell_count": followed_daily.get("sell_count", len(sell_trades)) if followed_daily else 0,
            "signal_status": followed_daily.get("signal_status") or ("ready" if signals else "no_signal"),
            "trade_status": followed_daily.get("trade_status") or ("ready" if trades else "no_trade"),
        },
        "follow_readiness": {
            "ready_for_follow": followed_ready,
            "blocking_reasons": followed_daily.get("blocking_reasons") if isinstance(followed_daily.get("blocking_reasons"), list) else [],
            "notes": followed_daily.get("notes") if isinstance(followed_daily.get("notes"), list) else [],
            "runtime_fresh": bool(followed_daily.get("runtime_fresh", followed_ready)),
            "runtime_stale": bool(followed_daily.get("runtime_stale")),
            "signal_status": followed_daily.get("signal_status") or ("ready" if signals else "no_signal"),
            "trade_status": followed_daily.get("trade_status") or ("ready" if trades else "no_trade"),
        },
        "runtime_overview": {
            "target_strategy_count": runtime.get("target_strategy_count"),
            "ready_count": runtime.get("ready_count"),
            "runtime_missing_count": runtime.get("runtime_missing_count"),
            "stale_count": runtime.get("stale_count"),
            "ready_for_frontend": runtime.get("ready_for_frontend"),
            "message": runtime.get("message") or "",
        },
        "daily_summary": {
            "target_strategy_count": daily_result.get("target_strategy_count"),
            "signal_model_count": daily_result.get("signal_model_count"),
            "trade_model_count": daily_result.get("trade_model_count"),
            "signal_count": daily_result.get("signal_count"),
            "trade_count": daily_result.get("trade_count"),
            "buy_count": daily_result.get("buy_count"),
            "sell_count": daily_result.get("sell_count"),
            "fallback_latest": bool(daily_result.get("fallback_latest")),
            "ready_model_count": daily_result.get("ready_model_count"),
            "missing_model_count": daily_result.get("missing_model_count"),
            "stale_model_count": daily_result.get("stale_model_count"),
            "no_signal_model_count": daily_result.get("no_signal_model_count"),
            "no_trade_model_count": daily_result.get("no_trade_model_count"),
        },
        "readiness": daily_result.get("readiness") if isinstance(daily_result.get("readiness"), Mapping) else {},
        "related_news": related_news,
        "news": news,
    }
