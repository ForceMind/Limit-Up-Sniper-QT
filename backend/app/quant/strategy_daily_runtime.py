from __future__ import annotations

from typing import Any, Dict, Mapping

from app.quant.capital_strategy import capital_presets
from app.quant.engine import quant_engine
from app.quant.engine_utils import safe_float
from app.quant.quant_paths import QUANT_DB_FILE
from app.quant.evolution import strategy_evolution
from app.quant.runtime_policy import target_strategy_count
from app.quant.strategy_runtime_matrix import (
    build_strategy_runtime_matrix_payload,
    build_strategy_runtime_overview_payload,
    strategy_runtime_catalog_items,
)


class StrategyDailyRuntime:
    """Read-side boundary for the daily 20-strategy runtime layer.

    This service intentionally does not train, evolve, or backtest strategies.
    It identifies the strategy set expected to be available for frontend
    following and reports whether persisted runtime results are ready.
    """

    def __init__(self, target_count: int | None = None) -> None:
        self.target_count = target_count

    def _target_count(self) -> int:
        if self.target_count is None:
            return target_strategy_count()
        return max(1, min(int(self.target_count or 20), 200))

    def target_models(self) -> list[Dict[str, Any]]:
        limit = self._target_count()
        base_params = quant_engine.strategy_params()
        presets = capital_presets(base_params)[:limit]
        catalog_limit = max(0, limit - len(presets))
        payload: Dict[str, Any] = {
            "status": "ok",
            "capital_presets": presets,
            "active": {},
            "items": [],
        }
        if catalog_limit > 0:
            try:
                models_payload = strategy_evolution.models(limit=catalog_limit, include_records=False)
                raw_items = models_payload.get("items") if isinstance(models_payload, Mapping) and isinstance(models_payload.get("items"), list) else []
                payload["items"] = [
                    dict(item)
                    for item in raw_items
                    if isinstance(item, dict) and item.get("reusable", True)
                ][:catalog_limit]
            except Exception:
                payload["items"] = []
        return strategy_runtime_catalog_items(payload, limit_models=limit)

    def _model_id(self, model: Mapping[str, Any]) -> str:
        return str(model.get("id") or model.get("model_id") or "").strip()

    def _daily_trade_groups(
        self,
        data_date: str,
        model_ids: list[str],
        limit_per_model: int = 80,
        generated_by_model: Mapping[str, Any] | None = None,
    ) -> Dict[str, Dict[str, Any]]:
        clean_date = str(data_date or "").strip()[:10]
        clean_ids = [str(model_id or "").strip() for model_id in model_ids if str(model_id or "").strip()]
        clean_ids = list(dict.fromkeys(clean_ids))
        if not clean_date or not clean_ids or not QUANT_DB_FILE.exists():
            return {}
        limit_per_model = max(1, min(int(safe_float(limit_per_model, 80)), 200))
        placeholders = ",".join("?" for _ in clean_ids)
        source_sql, source_params = strategy_evolution._daily_runtime_source_filter()
        params = [clean_date, *clean_ids, *source_params, clean_date, *clean_ids, *source_params]
        try:
            conn = strategy_evolution._connect_db()
            try:
                rows = conn.execute(
                    f"""
                    WITH latest AS (
                        SELECT model_id, MAX(generated_at) AS generated_at
                        FROM strategy_runtime_trades
                        WHERE date = ? AND model_id IN ({placeholders}) AND {source_sql}
                        GROUP BY model_id
                    )
                    SELECT
                      t.model_id,
                      t.date,
                      t.time,
                      t.side,
                      t.code,
                      t.name,
                      t.qty,
                      t.price,
                      t.amount,
                      t.score,
                      t.pnl_pct,
                      t.reason,
                      t.mode,
                      t.source,
                      t.generated_at
                    FROM strategy_runtime_trades t
                    JOIN latest l ON l.model_id = t.model_id AND l.generated_at = t.generated_at
                    WHERE t.date = ? AND t.model_id IN ({placeholders}) AND {source_sql}
                    ORDER BY t.model_id ASC, t.time ASC, t.trade_id ASC
                    """,
                    params,
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return {}

        expected_generated = {
            str(model_id or "").strip(): str(generated_at or "").strip()
            for model_id, generated_at in (generated_by_model.items() if isinstance(generated_by_model, Mapping) else [])
            if str(model_id or "").strip() and str(generated_at or "").strip()
        }
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            model_id = str(row["model_id"] or "").strip()
            if not model_id:
                continue
            expected = expected_generated.get(model_id)
            if expected and str(row["generated_at"] or "").strip() != expected:
                continue
            group = grouped.setdefault(
                model_id,
                {
                    "trade_count": 0,
                    "buy_count": 0,
                    "sell_count": 0,
                    "generated_at": str(row["generated_at"] or ""),
                    "trades": [],
                },
            )
            side = str(row["side"] or "").strip().upper()
            group["trade_count"] += 1
            if side == "BUY":
                group["buy_count"] += 1
            elif side == "SELL":
                group["sell_count"] += 1
            trades = group["trades"] if isinstance(group.get("trades"), list) else []
            if len(trades) < limit_per_model:
                trades.append(
                    {
                        "date": str(row["date"] or ""),
                        "time": str(row["time"] or ""),
                        "side": side,
                        "code": str(row["code"] or ""),
                        "name": str(row["name"] or ""),
                        "qty": round(safe_float(row["qty"], 0), 4),
                        "price": round(safe_float(row["price"], 0), 4),
                        "amount": round(safe_float(row["amount"], 0), 2),
                        "score": round(safe_float(row["score"], 0), 2),
                        "pnl_pct": round(safe_float(row["pnl_pct"], 0), 4),
                        "reason": str(row["reason"] or ""),
                        "mode": str(row["mode"] or ""),
                        "source": str(row["source"] or ""),
                    }
                )
            group["trades"] = trades
        return grouped

    def _compact_signals(self, signals: Any, limit: int = 20) -> list[Dict[str, Any]]:
        rows = signals if isinstance(signals, list) else []
        compact: list[Dict[str, Any]] = []
        for signal in rows:
            if not isinstance(signal, Mapping):
                continue
            compact.append(
                {
                    "signal_id": str(signal.get("signal_id") or ""),
                    "date": str(signal.get("date") or ""),
                    "execute_on": str(signal.get("execute_on") or ""),
                    "mode": str(signal.get("mode") or ""),
                    "action": str(signal.get("action") or ""),
                    "code": str(signal.get("code") or ""),
                    "name": str(signal.get("name") or ""),
                    "buy_score": round(safe_float(signal.get("buy_score"), 0), 2),
                    "sell_score": round(safe_float(signal.get("sell_score"), 0), 2),
                    "reason": str(signal.get("reason") or ""),
                    "source": str(signal.get("source") or ""),
                    "generated_at": str(signal.get("generated_at") or ""),
                }
            )
            if len(compact) >= limit:
                break
        return compact

    def _daily_model_readiness(
        self,
        *,
        requested_as_of: str,
        runtime_row: Mapping[str, Any],
        signal_count: int,
        trade_count: int,
    ) -> Dict[str, Any]:
        runtime_end_date = str(runtime_row.get("runtime_end_date") or "").strip()[:10]
        has_runtime = bool(runtime_row.get("has_runtime_data"))
        stale = bool(has_runtime and requested_as_of and runtime_end_date and runtime_end_date < requested_as_of)
        blocking_reasons: list[str] = []
        notes: list[str] = []
        if not has_runtime:
            blocking_reasons.append("missing_runtime")
        if stale:
            blocking_reasons.append("stale_runtime")
        ready_for_follow = has_runtime and not stale
        if ready_for_follow and signal_count <= 0:
            notes.append("no_signal")
        if ready_for_follow and trade_count <= 0:
            notes.append("no_trade")
        return {
            "ready_for_follow": ready_for_follow,
            "blocking_reasons": blocking_reasons,
            "notes": notes,
            "runtime_fresh": bool(ready_for_follow),
            "runtime_stale": stale,
            "signal_status": "not_ready" if blocking_reasons else ("ready" if signal_count > 0 else "no_signal"),
            "trade_status": "not_ready" if blocking_reasons else ("ready" if trade_count > 0 else "no_trade"),
        }

    def _signal_feed(self, as_of: str, include_signals: bool = True, limit_per_model: int = 20) -> Dict[str, Any]:
        if not include_signals:
            return {"status": "skipped", "items": [], "data_date": ""}
        return strategy_evolution.model_signal_feed(
            as_of=as_of,
            limit_models=self._target_count(),
            limit_per_model=limit_per_model,
            fallback_latest=True,
        )

    def overview(
        self,
        as_of: str | None = None,
        include_signals: bool = True,
        targets: list[Dict[str, Any]] | None = None,
        runtime_summaries: Mapping[str, Mapping[str, Any]] | None = None,
        signal_feed: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        effective_as_of = str(as_of or quant_engine.latest_event_date() or "").strip()[:10]
        target_items = targets if isinstance(targets, list) else self.target_models()
        summaries = runtime_summaries if isinstance(runtime_summaries, Mapping) else strategy_evolution.runtime_model_summaries(target_items)
        feed = signal_feed if isinstance(signal_feed, Mapping) else self._signal_feed(effective_as_of, include_signals=include_signals, limit_per_model=20)
        matrix = build_strategy_runtime_matrix_payload(
            effective_as_of=effective_as_of,
            catalog_items=target_items,
            runtime_summaries=summaries,
            signal_feed=feed,
            include_signals=include_signals,
        )
        overview = build_strategy_runtime_overview_payload(matrix, target_count=self._target_count())
        overview["runtime_rows"] = matrix.get("items") if isinstance(matrix.get("items"), list) else []
        overview["target_models"] = [
            {
                "model_id": self._model_id(item),
                "name": str(item.get("name") or item.get("model_name") or item.get("id") or ""),
                "source": str(item.get("source") or ""),
            }
            for item in target_items
        ][: self._target_count()]
        return overview

    def daily_result(
        self,
        as_of: str | None = None,
        overview: Mapping[str, Any] | None = None,
        signal_feed: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        requested_as_of = str(as_of or quant_engine.latest_event_date() or "").strip()[:10]
        feed = signal_feed if isinstance(signal_feed, Mapping) else None
        if not isinstance(overview, Mapping) and feed is None:
            feed = self._signal_feed(requested_as_of, include_signals=True, limit_per_model=20)
        current_overview = overview if isinstance(overview, Mapping) else self.overview(
            as_of=requested_as_of or None,
            include_signals=True,
            signal_feed=feed,
        )
        target_models = current_overview.get("target_models") if isinstance(current_overview.get("target_models"), list) else []
        model_ids = [str(model.get("model_id") or "").strip() for model in target_models if isinstance(model, Mapping)]
        requested_as_of = str(requested_as_of or current_overview.get("as_of") or "").strip()[:10]
        if feed is None:
            feed = self._signal_feed(requested_as_of, include_signals=True, limit_per_model=20)
        feed_data_date = str(feed.get("data_date") or "").strip()[:10]
        data_date = str(requested_as_of or feed_data_date or current_overview.get("data_date") or "").strip()[:10]
        signal_groups = feed.get("items") if isinstance(feed.get("items"), list) else []
        signal_by_model = {
            str(group.get("model_id") or "").strip(): group
            for group in signal_groups
            if (
                isinstance(group, Mapping)
                and str(group.get("model_id") or "").strip()
                and str(group.get("data_date") or feed_data_date or "").strip()[:10] == data_date
            )
        }
        runtime_rows = current_overview.get("runtime_rows") if isinstance(current_overview.get("runtime_rows"), list) else []
        runtime_by_model = {
            str(row.get("model_id") or "").strip(): row
            for row in runtime_rows
            if isinstance(row, Mapping) and str(row.get("model_id") or "").strip()
        }
        generated_by_model = {
            model_id: (
                (runtime_by_model.get(model_id) or {}).get("runtime_generated_at")
                or (runtime_by_model.get(model_id) or {}).get("generated_at")
                or (signal_by_model.get(model_id) or {}).get("generated_at")
            )
            for model_id in model_ids
        }
        trade_by_model = self._daily_trade_groups(data_date, model_ids, generated_by_model=generated_by_model)

        signal_count = 0
        signal_model_count = 0
        trade_count = 0
        trade_model_count = 0
        buy_count = 0
        sell_count = 0
        items: list[Dict[str, Any]] = []
        ready_models: list[Dict[str, Any]] = []
        missing_models: list[Dict[str, Any]] = []
        stale_models: list[Dict[str, Any]] = []
        no_signal_models: list[Dict[str, Any]] = []
        no_trade_models: list[Dict[str, Any]] = []
        for model in target_models:
            if not isinstance(model, Mapping):
                continue
            model_id = str(model.get("model_id") or "").strip()
            if not model_id:
                continue
            signal_group = signal_by_model.get(model_id) or {}
            trade_group = trade_by_model.get(model_id) or {}
            signals = self._compact_signals(signal_group.get("signals"), limit=20)
            model_signal_count = int(safe_float(signal_group.get("signal_count"), len(signals)))
            model_trade_count = int(safe_float(trade_group.get("trade_count"), 0))
            model_buy_count = int(safe_float(trade_group.get("buy_count"), 0))
            model_sell_count = int(safe_float(trade_group.get("sell_count"), 0))
            signal_count += model_signal_count
            trade_count += model_trade_count
            buy_count += model_buy_count
            sell_count += model_sell_count
            if model_signal_count:
                signal_model_count += 1
            if model_trade_count:
                trade_model_count += 1
            runtime_row = runtime_by_model.get(model_id) or {}
            readiness = self._daily_model_readiness(
                requested_as_of=requested_as_of,
                runtime_row=runtime_row,
                signal_count=model_signal_count,
                trade_count=model_trade_count,
            )
            model_brief = {
                "model_id": model_id,
                "name": str(model.get("name") or model_id),
            }
            if readiness["ready_for_follow"]:
                ready_models.append(model_brief)
                if readiness["signal_status"] == "no_signal":
                    no_signal_models.append(model_brief)
                if readiness["trade_status"] == "no_trade":
                    no_trade_models.append(model_brief)
            else:
                if "missing_runtime" in readiness["blocking_reasons"]:
                    missing_models.append(
                        {
                            **model_brief,
                            "runtime_status": str(runtime_row.get("runtime_status") or "missing"),
                        }
                    )
                if "stale_runtime" in readiness["blocking_reasons"]:
                    stale_models.append(
                        {
                            **model_brief,
                            "runtime_end_date": str(runtime_row.get("runtime_end_date") or ""),
                        }
                    )
            items.append(
                {
                    "model_id": model_id,
                    "name": str(model.get("name") or model_id),
                    "source": str(model.get("source") or ""),
                    "runtime_status": str(runtime_row.get("runtime_status") or "missing"),
                    "has_runtime_data": bool(runtime_row.get("has_runtime_data")),
                    "runtime_end_date": str(runtime_row.get("runtime_end_date") or ""),
                    "signal_count": model_signal_count,
                    "trade_count": model_trade_count,
                    "buy_count": model_buy_count,
                    "sell_count": model_sell_count,
                    "signals": signals,
                    "trades": trade_group.get("trades") if isinstance(trade_group.get("trades"), list) else [],
                    "signal_generated_at": str(signal_group.get("generated_at") or ""),
                    "trade_generated_at": str(trade_group.get("generated_at") or ""),
                    **readiness,
                }
            )

        model_count = len(items)
        ready_model_count = len(ready_models)
        fallback_latest = bool(feed.get("fallback_latest")) and bool(feed_data_date and feed_data_date == data_date)
        if model_count and ready_model_count == model_count and not fallback_latest:
            status = "ok"
        elif ready_model_count > 0:
            status = "partial"
        else:
            status = "pending"
        return {
            "status": status,
            "as_of": requested_as_of,
            "data_date": data_date,
            "target_strategy_count": current_overview.get("target_strategy_count") or self._target_count(),
            "model_count": model_count,
            "ready_model_count": ready_model_count,
            "missing_model_count": len(missing_models),
            "stale_model_count": len(stale_models),
            "no_signal_model_count": len(no_signal_models),
            "no_trade_model_count": len(no_trade_models),
            "ready_for_follow_count": ready_model_count,
            "signal_model_count": signal_model_count,
            "trade_model_count": trade_model_count,
            "signal_count": signal_count,
            "trade_count": trade_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "fallback_latest": fallback_latest,
            "readiness": {
                "ready_model_count": ready_model_count,
                "missing_model_count": len(missing_models),
                "stale_model_count": len(stale_models),
                "no_signal_model_count": len(no_signal_models),
                "no_trade_model_count": len(no_trade_models),
                "ready_models": ready_models[:20],
                "missing_models": missing_models[:20],
                "stale_models": stale_models[:20],
                "no_signal_models": no_signal_models[:20],
                "no_trade_models": no_trade_models[:20],
            },
            "items": items,
        }

    def status_summary(self, as_of: str | None = None) -> Dict[str, Any]:
        overview = self.overview(as_of=as_of, include_signals=False)
        ready = bool(overview.get("ready_for_frontend"))
        return {
            "status": "ok" if ready else "pending",
            "mode": "persisted_strategy_runtime",
            "as_of": overview.get("as_of") or str(as_of or "").strip()[:10],
            "target_strategy_count": overview.get("target_strategy_count") or self._target_count(),
            "catalog_count": overview.get("catalog_count") or 0,
            "model_count": overview.get("covered_catalog_count") or 0,
            "ready_count": overview.get("ready_count") or 0,
            "runtime_missing_count": overview.get("runtime_missing_count") or 0,
            "stale_count": overview.get("stale_count") or 0,
            "signal_ready_count": overview.get("signal_ready_count") or 0,
            "ready_for_frontend": ready,
            "latest_runtime_end_date": overview.get("latest_runtime_end_date") or "",
            "earliest_runtime_start_date": overview.get("earliest_runtime_start_date") or "",
            "missing_models": overview.get("missing_models") if isinstance(overview.get("missing_models"), list) else [],
            "stale_models": overview.get("stale_models") if isinstance(overview.get("stale_models"), list) else [],
            "message": overview.get("message") or "",
            "next_action": (
                "Frontend can read persisted strategy runtime results."
                if ready
                else "Run manual strategy replay or import runtime rows to fill missing strategies."
            ),
        }

    def run_daily(self, as_of: str | None = None) -> Dict[str, Any]:
        effective_as_of = str(as_of or quant_engine.latest_event_date() or "").strip()[:10]
        targets = self.target_models()
        summaries = strategy_evolution.runtime_model_summaries(targets)
        signal_feed = self._signal_feed(effective_as_of, include_signals=True, limit_per_model=20)
        overview = self.overview(
            as_of=effective_as_of,
            include_signals=True,
            targets=targets,
            runtime_summaries=summaries,
            signal_feed=signal_feed,
        )
        ready = bool(overview.get("ready_for_frontend"))
        daily_result = self.daily_result(as_of=effective_as_of, overview=overview, signal_feed=signal_feed)
        return {
            "status": "ok" if ready else "pending",
            "mode": "persisted_strategy_runtime",
            "as_of": overview.get("as_of") or str(as_of or "").strip()[:10],
            "target_strategy_count": overview.get("target_strategy_count"),
            "ready_count": overview.get("ready_count"),
            "runtime_missing_count": overview.get("runtime_missing_count"),
            "stale_count": overview.get("stale_count"),
            "daily_ready_model_count": daily_result.get("ready_model_count"),
            "daily_missing_model_count": daily_result.get("missing_model_count"),
            "daily_stale_model_count": daily_result.get("stale_model_count"),
            "daily_no_signal_model_count": daily_result.get("no_signal_model_count"),
            "daily_no_trade_model_count": daily_result.get("no_trade_model_count"),
            "ready_for_frontend": ready,
            "message": overview.get("message") or "",
            "next_action": (
                "Frontend can read persisted strategy runtime results."
                if ready
                else "Run manual strategy replay or import runtime rows to fill missing strategies."
            ),
            "daily_result": daily_result,
            "overview": overview,
        }


strategy_daily_runtime = StrategyDailyRuntime()
