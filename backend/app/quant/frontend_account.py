from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, Dict, Optional

from app.quant.engine_utils import safe_float


def frontend_followed_model_version(context: Dict[str, Any]) -> str:
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


def scale_model_trades_for_cash(model: Dict[str, Any], target_cash: float) -> list[Dict[str, Any]]:
    trades = model.get("trade_records") if isinstance(model.get("trade_records"), list) else []
    if not trades:
        return []
    backtest = model.get("backtest") if isinstance(model.get("backtest"), dict) else {}
    params = model.get("params") if isinstance(model.get("params"), dict) else {}
    base_cash = safe_float(backtest.get("initial_cash"), safe_float(params.get("account_initial_cash"), target_cash))
    scale = target_cash / base_cash if base_cash > 0 and target_cash > 0 else 1.0
    if abs(scale - 1.0) < 0.0001:
        return [dict(trade) for trade in trades if isinstance(trade, dict)]
    scaled: list[Dict[str, Any]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        qty = safe_float(trade.get("qty"), 0)
        price = safe_float(trade.get("price"), 0)
        scaled_qty = int(qty * scale // 100) * 100 if qty > 0 else 0
        if scaled_qty <= 0 or price <= 0:
            continue
        item = dict(trade)
        item["qty"] = scaled_qty
        item["amount"] = round(scaled_qty * price, 2)
        item["scaled_for_cash"] = round(target_cash, 2)
        scaled.append(item)
    return scaled


def frontend_pending_account(
    context: Dict[str, Any],
    effective_as_of: Optional[str],
    replay_start_date: Optional[str],
    limit: int,
    reason: str,
) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
    target_cash = safe_float(params.get("account_initial_cash"), safe_float(profile.get("simulated_cash"), 10_000))
    model_id = str(profile.get("strategy_model_id") or "active").strip() or "active"
    return {
        "status": "pending",
        "as_of": effective_as_of,
        "start_date": replay_start_date or "",
        "follow_start_date": replay_start_date or "",
        "strategy_model_id": model_id,
        "strategy_account_source": "pending_runtime_missing",
        "strategy_account_cache": "miss_deferred",
        "frontend_account_deferred": True,
        "frontend_account_defer_reason": reason,
        "message": "账户运行结果缓存未命中，已跳过同步回放。请先在后台手动运行策略复盘，或导入本地策略运行结果小包。",
        "account": {
            "status": "pending",
            "initial_cash": round(target_cash, 2),
            "simulated_cash": round(target_cash, 2),
            "total_asset": round(target_cash, 2),
            "cash": round(target_cash, 2),
            "available_cash": round(target_cash, 2),
            "market_value": 0.0,
            "total_pnl": 0.0,
            "return_pct": 0.0,
            "position_count": 0,
            "deal_count": 0,
        },
        "positions": [],
        "today_deals": [],
        "history_deals": [],
        "delivery_records": [],
        "daily_settlements": [],
        "portfolio": {"cash": round(target_cash, 2), "total_value": round(target_cash, 2), "strategy_params": params},
        "limit": limit,
    }


def scale_account_row(row: Dict[str, Any], scale: float, keys: tuple[str, ...]) -> Dict[str, Any]:
    item = dict(row)
    for key in keys:
        if key in item:
            item[key] = round(safe_float(item.get(key), 0) * scale, 2)
    return item


def frontend_trading_account_payload(account_payload: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    target_cash = safe_float(profile.get("simulated_cash"), 0)
    account = account_payload.get("account") if isinstance(account_payload.get("account"), dict) else {}
    base_initial = safe_float(account.get("total_asset"), 0) - safe_float(account.get("total_pnl"), 0)
    if base_initial <= 0:
        portfolio_params = ((account_payload.get("portfolio") or {}).get("strategy_params") or {}) if isinstance(account_payload.get("portfolio"), dict) else {}
        base_initial = safe_float(portfolio_params.get("account_initial_cash"), target_cash)
    scale = target_cash / base_initial if base_initial > 0 and target_cash > 0 else 1.0
    money_keys = (
        "total_asset",
        "cash",
        "available_cash",
        "frozen_cash",
        "state_cash_gross",
        "market_value",
        "position_cost",
        "unrealized_pnl",
        "realized_pnl",
        "total_pnl",
        "total_fees",
    )
    position_money_keys = ("qty", "available_qty", "frozen_qty", "market_value", "cost_amount", "pnl_amount")
    deal_money_keys = (
        "qty",
        "amount",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "total_fee",
        "net_amount",
        "cost_amount",
        "realized_pnl",
    )
    settlement_money_keys = (
        "buy_amount",
        "sell_amount",
        "commission",
        "stamp_duty",
        "transfer_fee",
        "total_fee",
        "net_amount",
        "realized_pnl",
    )
    next_payload = dict(account_payload)
    next_account = scale_account_row(account, scale, money_keys)
    next_account["initial_cash"] = round(target_cash, 2)
    next_account["simulated_cash"] = round(target_cash, 2)
    next_account["total_pnl"] = round(safe_float(next_account.get("total_asset"), target_cash) - target_cash, 2)
    next_account["return_pct"] = round(safe_float(next_account.get("total_pnl"), 0) / target_cash * 100, 3) if target_cash > 0 else 0.0
    next_account["follow_model_id"] = str(profile.get("strategy_model_id") or "active")
    next_account["follow_model_name"] = str((context.get("followed_model") or {}).get("name") or "未选择策略")
    next_payload["account"] = next_account
    next_payload["positions"] = [scale_account_row(item, scale, position_money_keys) for item in account_payload.get("positions", []) if isinstance(item, dict)]
    next_payload["today_deals"] = [scale_account_row(item, scale, deal_money_keys) for item in account_payload.get("today_deals", []) if isinstance(item, dict)]
    next_payload["history_deals"] = [scale_account_row(item, scale, deal_money_keys) for item in account_payload.get("history_deals", []) if isinstance(item, dict)]
    next_payload["delivery_records"] = [scale_account_row(item, scale, deal_money_keys) for item in account_payload.get("delivery_records", []) if isinstance(item, dict)]
    next_payload["daily_settlements"] = [scale_account_row(item, scale, settlement_money_keys) for item in account_payload.get("daily_settlements", []) if isinstance(item, dict)]
    portfolio = account_payload.get("portfolio") if isinstance(account_payload.get("portfolio"), dict) else {}
    next_portfolio = scale_account_row(portfolio, scale, ("cash", "total_value"))
    next_portfolio["strategy_params"] = context.get("strategy_params") or portfolio.get("strategy_params") or {}
    next_payload["portfolio"] = next_portfolio
    next_payload["frontend_profile"] = profile
    next_payload["followed_model"] = context.get("followed_model") or {}
    next_payload["follow_start_date"] = account_payload.get("follow_start_date") or profile.get("follow_start_date") or ""
    return next_payload


def frontend_strategy_account_runtime(
    context: Dict[str, Any],
    as_of: Optional[str],
    limit: int,
    *,
    force: bool = False,
    record_period: bool = True,
    defer_miss: bool = True,
    persist_derived: bool = True,
    hydrate_runtime_trades: bool = True,
    replay_days: int = 90,
    resolve_as_of: Callable[[Optional[str]], Optional[str]],
    follow_start_date: Callable[[Dict[str, Any], Optional[str]], Optional[str]],
    record_follow_period: Callable[..., Dict[str, Any]],
    load_user_follow_account: Callable[..., Optional[Dict[str, Any]]],
    load_runtime_account: Callable[..., Optional[Dict[str, Any]]],
    load_account_cache: Callable[..., Optional[Dict[str, Any]]],
    save_account_cache: Callable[..., Any],
    save_user_follow_account: Callable[..., Any],
    model_loader: Callable[[str], Dict[str, Any]],
    account_from_trades: Callable[..., Dict[str, Any]],
    temporary_strategy_params: Callable[[Dict[str, Any]], Any],
    walk_forward: Callable[..., Dict[str, Any]],
    trading_account: Callable[..., Dict[str, Any]],
    memory_cache_get: Callable[[str], Optional[Dict[str, Any]]],
    memory_cache_set: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    allow_model_records_fallback: Callable[[], bool],
) -> Dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    followed_id = str(profile.get("strategy_model_id") or "active").strip() or "active"
    params = context.get("strategy_params") if isinstance(context.get("strategy_params"), dict) else {}
    target_cash = safe_float(params.get("account_initial_cash"), safe_float(profile.get("simulated_cash"), 10_000))
    effective_as_of = resolve_as_of(as_of)
    replay_start_date = follow_start_date(context, effective_as_of)
    model_version = frontend_followed_model_version(context)
    username = str(context.get("username") or "").strip() or "anonymous"
    if record_period:
        record_follow_period(username, profile, source="front_account", reason="account_view", created_at=context.get("created_at"))

    def persist_user_follow(account: Dict[str, Any], source: str) -> Dict[str, Any]:
        if not persist_derived:
            marked = dict(account)
            marked["user_follow_persist_deferred"] = True
            marked["user_follow_persist_source"] = source
            marked["frontend_account_precompute_reason"] = "account_persist_deferred"
            return marked
        save_user_follow_account(
            username,
            followed_id,
            params,
            target_cash,
            replay_start_date,
            effective_as_of,
            limit,
            account,
            model_version=model_version,
            source=source,
        )
        return account

    user_cached = None if force else load_user_follow_account(
        username,
        followed_id,
        target_cash,
        replay_start_date,
        effective_as_of,
        limit,
        model_version=model_version,
        params=params,
    )
    if user_cached:
        return user_cached

    runtime_account = None if force else load_runtime_account(
        followed_id,
        target_cash,
        replay_start_date,
        effective_as_of,
        limit,
        model_version=model_version,
        params=params,
        hydrate_trades=hydrate_runtime_trades,
    )
    if runtime_account:
        if persist_derived:
            save_account_cache(
                followed_id,
                params,
                target_cash,
                replay_start_date,
                effective_as_of,
                limit,
                runtime_account,
                model_version=model_version,
                source="runtime_tables",
            )
        return persist_user_follow(runtime_account, "runtime_tables")

    sqlite_cached = None if force else load_account_cache(
        followed_id,
        params,
        target_cash,
        replay_start_date,
        effective_as_of,
        limit,
        model_version=model_version,
    )
    if sqlite_cached:
        return persist_user_follow(sqlite_cached, str(sqlite_cached.get("strategy_account_source") or "strategy_runtime_snapshot"))

    if followed_id != "active":
        if bool(force or allow_model_records_fallback()):
            model = model_loader(followed_id)
            raw_records = model.get("trade_records") if isinstance(model.get("trade_records"), list) else []
            if raw_records:
                trade_records = scale_model_trades_for_cash(model, target_cash)
                account = account_from_trades(
                    trade_records,
                    initial_cash=target_cash,
                    as_of=effective_as_of,
                    start_date=replay_start_date,
                    limit=limit,
                    drop_unmatched_sells=True,
                )
                account["strategy_account_source"] = "model_records"
                account["follow_start_date"] = replay_start_date
                account["strategy_account_cache"] = "miss"
                if persist_derived:
                    save_account_cache(
                        followed_id,
                        params,
                        target_cash,
                        replay_start_date,
                        effective_as_of,
                        limit,
                        account,
                        model_version=model_version,
                        source="model_records",
                    )
                return persist_user_follow(account, "model_records")

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "model_id": followed_id,
                    "as_of": effective_as_of,
                    "start_date": replay_start_date,
                    "limit": limit,
                    "cash": round(target_cash, 2),
                    "params": params,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        cache_key = f"front-account:{fingerprint}"
        cached = None if force else memory_cache_get(cache_key)
        if cached:
            cached["strategy_account_cache"] = "hit"
            return cached
        if defer_miss:
            return frontend_pending_account(
                context,
                effective_as_of,
                replay_start_date,
                limit,
                reason="strategy_runtime_cache_miss",
            )
        with temporary_strategy_params(params):
            timeline = walk_forward(
                start_date=replay_start_date,
                end_date=effective_as_of,
                initial_cash=target_cash,
                max_positions=int(params.get("max_positions", 5)),
                hold_days=int(params.get("max_hold_days", 3)),
                top_n=int(params.get("top_n", 5)),
                auto_fill=False,
            )
            trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
            account = account_from_trades(
                trades,
                initial_cash=target_cash,
                as_of=effective_as_of or timeline.get("end_date"),
                start_date=replay_start_date,
                limit=limit,
                drop_unmatched_sells=True,
            )
        account["strategy_account_source"] = "strategy_replay"
        account["strategy_account_cache"] = "miss"
        account["follow_start_date"] = replay_start_date
        account["strategy_timeline_summary"] = {
            "mode": timeline.get("mode", "daily"),
            "start_date": timeline.get("start_date"),
            "end_date": timeline.get("end_date"),
            "replay_days": replay_days,
            "trade_count": len(trades),
            "closed_trades": timeline.get("closed_trades", 0),
            "return_pct": timeline.get("return_pct", 0),
            "max_drawdown_pct": timeline.get("max_drawdown_pct", 0),
        }
        memory_cache_set(cache_key, account)
        if persist_derived:
            save_account_cache(
                followed_id,
                params,
                target_cash,
                replay_start_date,
                effective_as_of,
                limit,
                account,
                model_version=model_version,
                source="strategy_replay",
            )
        return persist_user_follow(account, "strategy_replay")

    if defer_miss:
        return frontend_pending_account(
            context,
            effective_as_of,
            replay_start_date,
            limit,
            reason="baseline_runtime_cache_miss",
        )
    with temporary_strategy_params(params):
        account = trading_account(as_of=effective_as_of, limit=limit)
    account["strategy_account_source"] = "baseline_replay"
    account["follow_start_date"] = replay_start_date
    account["strategy_account_cache"] = "miss"
    if persist_derived:
        save_account_cache(
            followed_id,
            params,
            target_cash,
            replay_start_date,
            effective_as_of,
            limit,
            account,
            model_version=model_version,
            source="baseline_replay",
        )
    return persist_user_follow(account, "baseline_replay")


class FrontendAccountReadService:
    def __init__(
        self,
        *,
        replay_days: Callable[[], int],
        resolve_as_of: Callable[[Optional[str]], Optional[str]],
        follow_start_date: Callable[[Dict[str, Any], Optional[str]], Optional[str]],
        record_follow_period: Callable[..., Dict[str, Any]],
        load_user_follow_account: Callable[..., Optional[Dict[str, Any]]],
        load_runtime_account: Callable[..., Optional[Dict[str, Any]]],
        load_account_cache: Callable[..., Optional[Dict[str, Any]]],
        save_account_cache: Callable[..., Any],
        save_user_follow_account: Callable[..., Any],
        model_loader: Callable[[str], Dict[str, Any]],
        account_from_trades: Callable[..., Dict[str, Any]],
        temporary_strategy_params: Callable[[Dict[str, Any]], Any],
        walk_forward: Callable[..., Dict[str, Any]],
        trading_account: Callable[..., Dict[str, Any]],
        allow_model_records_fallback: Callable[[], bool],
        cache_ttl_seconds: int = 300,
        max_cache_rows: int = 64,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._replay_days = replay_days
        self._resolve_as_of = resolve_as_of
        self._follow_start_date = follow_start_date
        self._record_follow_period = record_follow_period
        self._load_user_follow_account = load_user_follow_account
        self._load_runtime_account = load_runtime_account
        self._load_account_cache = load_account_cache
        self._save_account_cache = save_account_cache
        self._save_user_follow_account = save_user_follow_account
        self._model_loader = model_loader
        self._account_from_trades = account_from_trades
        self._temporary_strategy_params = temporary_strategy_params
        self._walk_forward = walk_forward
        self._trading_account = trading_account
        self._allow_model_records_fallback = allow_model_records_fallback
        self._cache_ttl_seconds = max(0, int(cache_ttl_seconds or 0))
        self._max_cache_rows = max(1, int(max_cache_rows or 1))
        self._clock = clock
        self._memory_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}

    @property
    def memory_cache(self) -> Dict[str, tuple[float, Dict[str, Any]]]:
        return self._memory_cache

    def memory_cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        cached = self._memory_cache.get(key)
        if not cached:
            return None
        ts, payload = cached
        if self._clock() - ts > self._cache_ttl_seconds:
            self._memory_cache.pop(key, None)
            return None
        return dict(payload)

    def memory_cache_set(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if len(self._memory_cache) > self._max_cache_rows:
            trim_count = max(1, self._max_cache_rows // 4)
            oldest = sorted(self._memory_cache.items(), key=lambda item: item[1][0])[:trim_count]
            for old_key, _item in oldest:
                self._memory_cache.pop(old_key, None)
        self._memory_cache[key] = (self._clock(), dict(payload))
        return payload

    def clear_memory_cache(self) -> None:
        self._memory_cache.clear()

    def strategy_account(
        self,
        context: Dict[str, Any],
        as_of: Optional[str],
        limit: int,
        *,
        force: bool = False,
        record_period: bool = True,
        defer_miss: bool = True,
        persist_derived: bool = True,
        hydrate_runtime_trades: bool = True,
    ) -> Dict[str, Any]:
        return frontend_strategy_account_runtime(
            context,
            as_of,
            limit,
            force=force,
            record_period=record_period,
            defer_miss=defer_miss,
            persist_derived=persist_derived,
            hydrate_runtime_trades=hydrate_runtime_trades,
            replay_days=self._replay_days(),
            resolve_as_of=self._resolve_as_of,
            follow_start_date=self._follow_start_date,
            record_follow_period=self._record_follow_period,
            load_user_follow_account=self._load_user_follow_account,
            load_runtime_account=self._load_runtime_account,
            load_account_cache=self._load_account_cache,
            save_account_cache=self._save_account_cache,
            save_user_follow_account=self._save_user_follow_account,
            model_loader=self._model_loader,
            account_from_trades=self._account_from_trades,
            temporary_strategy_params=self._temporary_strategy_params,
            walk_forward=self._walk_forward,
            trading_account=self._trading_account,
            memory_cache_get=self.memory_cache_get,
            memory_cache_set=self.memory_cache_set,
            allow_model_records_fallback=self._allow_model_records_fallback,
        )
