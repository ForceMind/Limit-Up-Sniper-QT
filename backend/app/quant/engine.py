from __future__ import annotations

import contextlib
import math
import os
import sqlite3
import statistics
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.quant.accounting import (
    account_from_trades_payload,
    broker_fees as accounting_broker_fees,
    trade_clock as accounting_trade_clock,
)
from app.quant.backtest_research import (
    backtest_data_diagnostics,
    backtest_event_outcome_summary,
    backtest_event_score,
)
from app.quant.correlation_analysis import build_correlation_payload
from app.quant.event_classifier import EventClassifier
from app.quant.event_models import NewsEvent
from app.quant.event_repository import (
    event_source_mtime_key,
    lhb_summary_payload,
    read_analysis_records,
    read_lhb_records,
    read_news_history,
)
from app.quant.engine_runtime_cache import EngineRuntimeCaches
from app.quant.factors import (
    factor_profile_payload,
    lhb_factor_profile_from_rows,
    technical_profile_from_rows,
)
from app.quant.market_read_repository import (
    all_trading_dates_for_codes,
    first_data_date as market_first_data_date,
    future_return_from_rows,
    latest_price as market_latest_price,
    read_available_intraday_dates,
    read_daily_kline,
    read_intraday_bars,
)
from app.quant.market_data_preparation import sync_daily_kline_for_events
from app.quant.news_feed_payload import build_news_feed_payload
from app.quant.performance import aggregate_return_stats, performance_metrics_payload
from app.quant.quant_paths import (
    DATA_DIR,
    EVENTS_CACHE_FILE,
    KLINE_DAY_DIR,
    KLINE_MIN_DIR,
    QUANT_DB_FILE,
    STATE_FILE,
)
from app.quant.replay_execution import (
    daily_buy_execution,
    daily_sell_execution,
    intraday_buy_execution,
    intraday_sell_execution,
    lot_quantity_for_cash,
    replay_day_valuation,
    replay_missed_order,
    replay_position_snapshot,
)
from app.quant.replay_context import (
    ReplayCorrelationState,
    empty_replay_result,
    historical_outcomes_for_replay,
    replay_final_metrics,
)
from app.quant.replay_signals import (
    BUY_ACTION,
    build_daily_signal_orders,
    build_intraday_signal_order,
    build_replay_candidate_scores,
)
from app.quant.stock_universe import StockUniverse
from app.quant.strategy_defaults import DEFAULT_AI_MODEL, DEFAULT_BROKER_FEE_PARAMS, DEFAULT_STRATEGY_PARAMS
from app.quant.strategy_state import (
    apply_strategy_reset,
    apply_strategy_update,
    load_strategy_state,
    normalize_strategy_params,
    save_strategy_state,
    strategy_params_from_state,
    strategy_source_from_state,
)
from app.quant.engine_utils import (
    SAMPLE_CODES,
    SAMPLE_MARKERS,
    clamp,
    contains_sample_marker,
    digits6,
    env_bool,
    env_int,
    is_sample_code,
    item_datetime,
    parse_time,
    read_json,
    safe_float,
    short_hash,
    write_json,
)

class QuantEngine:
    def __init__(self) -> None:
        self.universe = StockUniverse()
        self.classifier = EventClassifier()
        self._events_cache_key = ""
        self._events_cache: List[NewsEvent] = []
        self._runtime_caches = EngineRuntimeCaches()
        self._kline_cache = self._runtime_caches.kline
        self._correlation_cache = self._runtime_caches.correlation
        self._future_return_cache = self._runtime_caches.future_return
        self._kline_row_map_cache = self._runtime_caches.kline_row_map
        self._intraday_cache = self._runtime_caches.intraday
        self._factor_cache = self._runtime_caches.factor
        self._lhb_rows_cache = self._runtime_caches.lhb_rows
        self._lhb_by_code_cache = self._runtime_caches.lhb_by_code
        self._thread_local = threading.local()

    def _cache_limit(self, name: str, default: int, maximum: Optional[int] = None) -> int:
        return self._runtime_caches.cache_limit(name, default, maximum=maximum)

    def _prune_cache(self, cache: Dict[Any, Any], limit: int) -> int:
        return self._runtime_caches.prune_cache(cache, limit)

    def _remember_kline(self, code: str, rows: List[Dict[str, Any]]) -> None:
        self._runtime_caches.remember_kline(code, rows)

    def _remember_intraday(self, cache_key: Tuple[str, str], rows: List[Dict[str, Any]]) -> None:
        self._runtime_caches.remember_intraday(cache_key, rows)

    def _remember_future_return(self, cache_key: Tuple[str, str, int], value: Optional[Dict[str, Any]]) -> None:
        self._runtime_caches.remember_future_return(cache_key, value)

    def _remember_factor(self, cache_key: Tuple[str, str], value: Dict[str, Any]) -> None:
        self._runtime_caches.remember_factor(cache_key, value)

    def _remember_lhb_cache(self, rows_key: str, rows: List[Dict[str, Any]], by_code_key: str, by_code: Dict[str, List[Dict[str, Any]]]) -> None:
        self._runtime_caches.remember_lhb(rows_key, rows, by_code_key, by_code)

    def trim_runtime_caches(self, aggressive: bool = False) -> Dict[str, Any]:
        return self._runtime_caches.trim(event_count=len(self._events_cache), aggressive=aggressive)

    def cache_stats(self) -> Dict[str, Any]:
        return self._runtime_caches.stats(event_count=len(self._events_cache))

    def _sqlite_rows(self, query: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        if not QUANT_DB_FILE.exists():
            return []
        try:
            conn = sqlite3.connect(QUANT_DB_FILE)
            conn.row_factory = sqlite3.Row
            try:
                return [dict(row) for row in conn.execute(query, params).fetchall()]
            finally:
                conn.close()
        except Exception:
            return []

    def clear_intraday_cache(self) -> None:
        self._runtime_caches.clear_intraday()

    def clear_market_cache(self) -> None:
        self._runtime_caches.clear_market()

    def _source_mtime_key(self) -> str:
        return event_source_mtime_key()

    def load_news_history(self) -> List[Dict[str, Any]]:
        return read_news_history(self._sqlite_rows)

    def load_analysis_records(self) -> List[Dict[str, Any]]:
        return read_analysis_records()

    def load_lhb_records(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        return read_lhb_records(
            self._sqlite_rows,
            self.universe.name,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

    def lhb_summary(self, end_date: Optional[str] = None, recent_limit: int = 20) -> Dict[str, Any]:
        return lhb_summary_payload(self._sqlite_rows, self.load_lhb_records, end_date=end_date, recent_limit=recent_limit)

    def load_kline(self, code: str) -> List[Dict[str, Any]]:
        code = digits6(code)
        if not code:
            return []
        cached = self._kline_cache.get(code)
        if cached is not None:
            return cached
        merged_rows = read_daily_kline(
            self._sqlite_rows,
            code,
            read_legacy_json_cache=env_bool("QT_READ_LEGACY_KLINE_JSON_CACHE", False),
        )
        self._remember_kline(code, merged_rows)
        return merged_rows

    def load_intraday_bars(self, code: str, date: str) -> List[Dict[str, Any]]:
        code = digits6(code)
        date = str(date or "").strip()[:10]
        cache_key = (code, date)
        cached = self._intraday_cache.get(cache_key)
        if cached is not None:
            return cached
        if not code or not date:
            self._remember_intraday(cache_key, [])
            return []
        bars = read_intraday_bars(self._sqlite_rows, code, date)
        self._remember_intraday(cache_key, bars)
        return bars

    def _available_intraday_dates(
        self,
        codes: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, set]:
        return read_available_intraday_dates(self._sqlite_rows, codes, start_date, end_date)

    def _first_intraday_bar(self, code: str, date: str) -> Optional[Dict[str, Any]]:
        bars = self.load_intraday_bars(code, date)
        return bars[0] if bars else None

    def _next_intraday_bar_after(self, code: str, date: str, signal_dt: Optional[datetime]) -> Optional[Dict[str, Any]]:
        bars = self.load_intraday_bars(code, date)
        if not bars:
            return None
        if signal_dt is None:
            return bars[0]
        for bar in bars:
            if bar["dt"] > signal_dt:
                return bar
        return None

    def _event_signal_dt(self, event: NewsEvent) -> Optional[datetime]:
        if event.timestamp > 0:
            try:
                return datetime.fromtimestamp(event.timestamp)
            except Exception:
                pass
        return parse_time(event.date)

    def _event_from_stock_result(self, record: Dict[str, Any], stock: Dict[str, Any]) -> Optional[NewsEvent]:
        if not isinstance(stock, dict):
            return None
        code = self.universe.normalize_code(stock.get("code"), stock.get("name"))
        if not self.universe.is_tradeable_a_share(code):
            return None
        news_items = record.get("news_items") if isinstance(record.get("news_items"), list) else []
        first_news = news_items[0] if news_items and isinstance(news_items[0], dict) else {}
        dt = item_datetime(first_news) or item_datetime(record) or datetime.now()
        text_parts = []
        for item in news_items[:3]:
            if isinstance(item, dict):
                text_parts.append(str(item.get("text") or ""))
        reason = str(stock.get("reason") or "").strip()
        text = " ".join(part for part in text_parts if part).strip() or reason
        concept = str(stock.get("concept") or "").strip()
        ai_score = safe_float(stock.get("score"), 0)
        combined_text = f"{concept} {reason} {text}"
        event_type = self.classifier.classify_event_type(combined_text)
        industry = self.classifier.classify_industry(combined_text, concept)
        sentiment = self.classifier.sentiment(combined_text, ai_score=ai_score)
        impact = self.classifier.impact(combined_text, event_type, sentiment, ai_score=ai_score)
        source = str(first_news.get("source") or "AI分析记录")
        identity = f"{record.get('record_key', '')}:{code}:{dt.isoformat()}:{reason[:80]}"
        return NewsEvent(
            event_id=short_hash(identity),
            date=dt.strftime("%Y-%m-%d"),
            timestamp=int(dt.timestamp()),
            source=source,
            text=text[:700],
            code=code,
            name=self.universe.name(code, stock.get("name")),
            industry=industry,
            event_type=event_type,
            sentiment=sentiment,
            impact_score=impact,
            ai_score=ai_score,
            reason=reason or text[:180],
        )

    def _events_from_records(self) -> List[NewsEvent]:
        events: List[NewsEvent] = []
        for record in self.load_analysis_records():
            if not isinstance(record, dict):
                continue
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            stocks = result.get("stocks") if isinstance(result.get("stocks"), list) else []
            for stock in stocks:
                event = self._event_from_stock_result(record, stock)
                if event:
                    events.append(event)
        return events

    def _events_from_raw_news(self, days: int = 7) -> List[NewsEvent]:
        history = self.load_news_history()
        dated = []
        for item in history:
            if not isinstance(item, dict):
                continue
            dt = item_datetime(item)
            if dt:
                dated.append((dt.strftime("%Y-%m-%d"), dt, item))
        if not dated:
            return []
        latest_date = max(item[0] for item in dated)
        ordered_dates = sorted({item[0] for item in dated})
        allowed_dates = set(ordered_dates[-max(1, days) :])
        allowed_dates.add(latest_date)

        events: List[NewsEvent] = []
        for date, dt, item in dated:
            if date not in allowed_dates:
                continue
            text = str(item.get("text") or "").strip()
            mentions = self.universe.extract_mentions(text, limit=6)
            if not mentions:
                continue
            event_type = self.classifier.classify_event_type(text)
            industry = self.classifier.classify_industry(text)
            sentiment = self.classifier.sentiment(text)
            impact = self.classifier.impact(text, event_type, sentiment)
            source = str(item.get("source") or "新闻")
            for code, name in mentions:
                if not self.universe.is_tradeable_a_share(code):
                    continue
                identity = f"raw:{date}:{code}:{text[:120]}"
                events.append(
                    NewsEvent(
                        event_id=short_hash(identity),
                        date=date,
                        timestamp=int(dt.timestamp()),
                        source=source,
                        text=text[:700],
                        code=code,
                        name=name,
                        industry=industry,
                        event_type=event_type,
                        sentiment=sentiment,
                        impact_score=impact,
                        ai_score=0.0,
                        reason=text[:180],
                    )
                )
        return events

    def _events_from_sqlite(self, limit: int = 200000) -> List[NewsEvent]:
        rows = self._sqlite_rows(
            """
            SELECT event_id, date, timestamp, source, text, code, name, industry, event_type,
                   sentiment, impact_score, ai_score, reason
            FROM news_events
            WHERE code IS NOT NULL AND date IS NOT NULL
            ORDER BY date DESC, COALESCE(timestamp, 0) DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 200000), 500000)),),
        )
        events: List[NewsEvent] = []
        for row in rows:
            code = digits6(row.get("code"))
            date = str(row.get("date") or "").strip()[:10]
            if not code or not date or not self.universe.is_tradeable_a_share(code):
                continue
            timestamp = int(safe_float(row.get("timestamp"), 0))
            if timestamp <= 0:
                dt = parse_time(date) or datetime.now()
                timestamp = int(dt.timestamp())
            event = NewsEvent(
                event_id=str(row.get("event_id") or short_hash(f"sqlite:{date}:{code}:{row.get('text') or row.get('reason') or ''}")),
                date=date,
                timestamp=timestamp,
                source=str(row.get("source") or "sqlite"),
                text=str(row.get("text") or row.get("reason") or "")[:700],
                code=code,
                name=self.universe.name(code, row.get("name")),
                industry=str(row.get("industry") or ""),
                event_type=str(row.get("event_type") or ""),
                sentiment=safe_float(row.get("sentiment"), 0),
                impact_score=clamp(safe_float(row.get("impact_score"), 50)),
                ai_score=safe_float(row.get("ai_score"), 0),
                reason=str(row.get("reason") or row.get("text") or "")[:240],
            )
            if not self._is_sample_event(event):
                events.append(event)
        return events

    def _is_sample_event(self, event: NewsEvent) -> bool:
        if is_sample_code(event.code):
            return True
        return contains_sample_marker(
            {
                "name": event.name,
                "industry": event.industry,
                "event_type": event.event_type,
                "text": event.text,
                "reason": event.reason,
            }
        )

    def _events_from_lhb(self, days: int = 120) -> List[NewsEvent]:
        records = self.load_lhb_records(limit=200000)
        if not records:
            return []
        dates = sorted({str(row.get("trade_date") or "") for row in records if row.get("trade_date")})
        allowed_dates = set(dates[-max(1, days) :])
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in records:
            date = str(row.get("trade_date") or "")
            code = digits6(row.get("stock_code"))
            if not date or date not in allowed_dates or not code or not self.universe.is_tradeable_a_share(code):
                continue
            bucket = grouped.setdefault(
                (date, code),
                {
                    "date": date,
                    "code": code,
                    "name": row.get("stock_name") or self.universe.name(code),
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "seats": [],
                    "hot_money": set(),
                },
            )
            buy_amount = safe_float(row.get("buy_amount"), 0)
            sell_amount = safe_float(row.get("sell_amount"), 0)
            seat = str(row.get("buyer_seat_name") or "").strip()
            hot = str(row.get("hot_money") or "").strip()
            bucket["buy_amount"] += buy_amount
            bucket["sell_amount"] += sell_amount
            if seat:
                bucket["seats"].append({"seat": seat, "buy_amount": buy_amount, "sell_amount": sell_amount, "hot_money": hot})
            if hot:
                bucket["hot_money"].add(hot)

        events: List[NewsEvent] = []
        for (date, code), bucket in grouped.items():
            seats = sorted(bucket["seats"], key=lambda item: safe_float(item.get("buy_amount"), 0), reverse=True)
            top_seats = [str(item.get("seat") or "") for item in seats[:3] if item.get("seat")]
            buy_amount = safe_float(bucket.get("buy_amount"), 0)
            sell_amount = safe_float(bucket.get("sell_amount"), 0)
            net_amount = buy_amount - sell_amount
            gross_amount = max(1.0, buy_amount + sell_amount)
            sentiment = clamp((net_amount / gross_amount) * 1.35, -1.0, 1.0)
            hot_labels = sorted(str(item) for item in bucket.get("hot_money", set()) if item)
            hot_boost = 5.0 if hot_labels else 0.0
            impact = clamp(52 + sentiment * 18 + min(abs(net_amount) / 10_000_000, 18) + hot_boost)
            seat_text = "、".join(top_seats[:3]) if top_seats else "席位未披露"
            hot_text = f"；活跃席位标签：{'、'.join(hot_labels[:3])}" if hot_labels else ""
            reason = (
                f"龙虎榜净买入{net_amount / 10000:.1f}万，买入{buy_amount / 10000:.1f}万，"
                f"卖出{sell_amount / 10000:.1f}万；主要席位：{seat_text}{hot_text}"
            )
            dt = parse_time(date) or datetime.now()
            events.append(
                NewsEvent(
                    event_id=short_hash(f"lhb:{date}:{code}:{round(net_amount, 2)}:{seat_text}"),
                    date=date,
                    timestamp=int(dt.timestamp()),
                    source="龙虎榜",
                    text=reason,
                    code=code,
                    name=str(bucket.get("name") or self.universe.name(code)),
                    industry="龙虎榜席位",
                    event_type="龙虎榜席位",
                    sentiment=sentiment,
                    impact_score=impact,
                    ai_score=0.0,
                    reason=reason,
                )
            )
        return events

    def events(self, force: bool = False) -> List[NewsEvent]:
        key = self._source_mtime_key()
        if not force and key == self._events_cache_key and self._events_cache:
            return list(self._events_cache)
        if not force:
            cached_payload = read_json(EVENTS_CACHE_FILE, {})
            if isinstance(cached_payload, dict) and cached_payload.get("source_key") == key:
                cached_events = []
                for item in cached_payload.get("events", []):
                    if not isinstance(item, dict):
                        continue
                    try:
                        event = NewsEvent(**item)
                    except Exception:
                        continue
                    if self._is_sample_event(event):
                        continue
                    cached_events.append(event)
                if cached_events:
                    cached_events = cached_events[: self._cache_limit("QT_EVENTS_CACHE_MAX_ITEMS", 30000, maximum=500000)]
                    self._events_cache_key = key
                    self._events_cache = cached_events
                    return list(cached_events)
        seen = set()
        events: List[NewsEvent] = []
        for event in self._events_from_sqlite() + self._events_from_records() + self._events_from_raw_news(days=90) + self._events_from_lhb(days=120):
            if self._is_sample_event(event):
                continue
            dedupe = (event.date, event.code, short_hash(event.text[:120] + event.reason[:80]))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            events.append(event)
        events.sort(key=lambda item: (item.date, item.timestamp, item.code), reverse=True)
        events = events[: self._cache_limit("QT_EVENTS_CACHE_MAX_ITEMS", 30000, maximum=500000)]
        self._events_cache_key = key
        self._events_cache = events
        write_json(
            EVENTS_CACHE_FILE,
            {
                "source_key": key,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "events": [event.compact() for event in events],
            },
        )
        return list(events)

    def latest_event_date(self) -> str:
        events = self.events()
        if events:
            return max(event.date for event in events)
        return datetime.now().strftime("%Y-%m-%d")

    def first_data_date(self) -> str:
        return market_first_data_date(self._sqlite_rows, self._events_cache, self.events)

    def news_feed(
        self,
        as_of: Optional[str] = None,
        limit: int = 120,
        fallback_latest: bool = True,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        code: Optional[str] = None,
    ) -> Dict[str, Any]:
        return build_news_feed_payload(
            self.load_news_history(),
            self.events(),
            extract_mentions=self.universe.extract_mentions,
            is_sample_event=self._is_sample_event,
            as_of=as_of,
            limit=limit,
            fallback_latest=fallback_latest,
            source=source,
            keyword=keyword,
            code=code,
        )

    def latest_price(self, code: str, as_of: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return market_latest_price(code, load_intraday_bars=self.load_intraday_bars, load_kline=self.load_kline, as_of=as_of)

    def future_return(self, code: str, event_date: str, hold_days: int = 3) -> Optional[Dict[str, Any]]:
        code = digits6(code)
        cache_key = (code, event_date, int(hold_days))
        if cache_key in self._future_return_cache:
            cached = self._future_return_cache[cache_key]
            return dict(cached) if isinstance(cached, dict) else cached
        payload = future_return_from_rows(self.load_kline(code), event_date, hold_days=hold_days)
        self._remember_future_return(cache_key, payload)
        return dict(payload) if isinstance(payload, dict) else None

    def ensure_daily_kline_for_events(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        hold_days: int = 3,
        max_codes: int = 300,
        force: bool = False,
    ) -> Dict[str, Any]:
        events = self.events()
        if start_date:
            events = [event for event in events if event.date >= start_date]
        if end_date:
            events = [event for event in events if event.date <= end_date]
        events = [event for event in events if self.universe.is_tradeable_a_share(event.code) and not self._is_sample_event(event)]
        if not events:
            return {
                "status": "ok",
                "message": "没有可用于补齐K线的新闻事件",
                "requested": 0,
                "fetched": 0,
                "added_rows": 0,
                "updated_rows": 0,
            }
        from app.quant.market_data import sync_daily_for_codes

        result = sync_daily_kline_for_events(
            events,
            start_date=start_date,
            end_date=end_date,
            hold_days=hold_days,
            max_codes=max_codes,
            force=force,
            sync_daily_for_codes=sync_daily_for_codes,
        )
        if result.get("fetched") or result.get("added_rows") or result.get("updated_rows"):
            self.clear_market_cache()
        return result

    def technical_profile(self, code: str, as_of: Optional[str] = None) -> Dict[str, Any]:
        return technical_profile_from_rows(self.load_kline(code), as_of=as_of)

    def _aggregate_stats(self, returns: List[float]) -> Dict[str, Any]:
        return aggregate_return_stats(returns)

    def correlation(
        self,
        as_of: Optional[str] = None,
        hold_days: int = 3,
        realized_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        realized_by = realized_by or as_of
        cache_key = (as_of, int(hold_days), realized_by)
        cached = self._correlation_cache.get(cache_key)
        if cached is not None:
            return cached
        max_events = env_int("QT_CORRELATION_MAX_EVENTS", 1200, minimum=100, maximum=50000)
        payload = build_correlation_payload(
            self.events(),
            as_of=as_of,
            hold_days=hold_days,
            realized_by=realized_by,
            future_return=lambda code, date, days: self.future_return(code, date, hold_days=days),
            aggregate_stats=self._aggregate_stats,
            is_sample_event=self._is_sample_event,
            max_events=max_events,
        )
        if len(self._correlation_cache) > 200:
            self._correlation_cache.clear()
        self._correlation_cache[cache_key] = payload
        return payload

    def _load_state(self) -> Dict[str, Any]:
        return load_strategy_state()

    def _save_state(self, state: Dict[str, Any]) -> None:
        save_strategy_state(state)

    def _normalize_strategy_params(self, raw: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        return normalize_strategy_params(raw)

    def strategy_params(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        state = self._load_state()
        thread_override = getattr(self._thread_local, "strategy_params_override", None)
        return strategy_params_from_state(state, thread_override=thread_override, overrides=overrides)

    def strategy_source(self) -> Dict[str, Any]:
        return strategy_source_from_state(self._load_state())

    @contextlib.contextmanager
    def temporary_strategy_params(self, params: Dict[str, Any]):
        old = getattr(self._thread_local, "strategy_params_override", None)
        self._thread_local.strategy_params_override = self._normalize_strategy_params(params)
        try:
            yield
        finally:
            if old is None:
                try:
                    delattr(self._thread_local, "strategy_params_override")
                except AttributeError:
                    pass
            else:
                self._thread_local.strategy_params_override = old

    def update_strategy_params(self, updates: Dict[str, Any], source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        state = self._load_state()
        params = self.strategy_params(updates)
        result = apply_strategy_update(state, params, updates, source)
        self._save_state(state)
        return result

    def reset_strategy_params(self) -> Dict[str, Any]:
        state = self._load_state()
        result = apply_strategy_reset(state)
        self._save_state(state)
        return result

    def model_weights(self) -> Dict[str, float]:
        params = self.strategy_params()
        return {
            "sentiment": params["sentiment_weight"],
            "event": params["event_weight"],
            "technical": params["technical_weight"],
            "risk": params["risk_weight"],
        }

    def calibrate_model(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        backtest = self.backtest(as_of=as_of, hold_days=3, top_n=5)
        bucket = backtest.get("score_buckets", {}).get("80-100", {})
        avg_ret = safe_float(bucket.get("avg_return_pct"), 0)
        win_rate = safe_float(bucket.get("win_rate"), 0)
        weights = self.model_weights()
        if bucket.get("samples", 0) >= 5:
            if avg_ret < 0 or win_rate < 45:
                weights["risk"] += 0.04
                weights["sentiment"] -= 0.02
                weights["event"] -= 0.02
            elif avg_ret > 1.2 and win_rate > 55:
                weights["event"] += 0.03
                weights["sentiment"] += 0.01
                weights["risk"] -= 0.04
        total = sum(max(0.02, val) for val in weights.values())
        weights = {key: round(max(0.02, val) / total, 4) for key, val in weights.items()}
        state = self._load_state()
        state["model_weights"] = weights
        strategy_params = self.strategy_params()
        strategy_params.update(
            {
                "sentiment_weight": weights["sentiment"],
                "event_weight": weights["event"],
                "technical_weight": weights["technical"],
                "risk_weight": weights["risk"],
            }
        )
        state["strategy_params"] = self._normalize_strategy_params(strategy_params)
        state["last_calibration"] = {
            "as_of": as_of or self.latest_event_date(),
            "top_bucket_avg_return_pct": avg_ret,
            "top_bucket_win_rate": win_rate,
            "backtest_trades": backtest.get("trades", 0),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_state(state)
        return {**state["last_calibration"], "model_weights": weights}

    def _historical_score(self, event: NewsEvent, corr: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        code_stats = corr.get("by_code", {}).get(event.code)
        theme_stats = corr.get("by_theme", {}).get(f"{event.industry}|{event.event_type}")
        type_stats = corr.get("by_type", {}).get(event.event_type)
        stats = code_stats or theme_stats or type_stats or corr.get("global", {})
        avg_ret = safe_float(stats.get("avg_return_pct"), 0) / 100.0
        win_rate = safe_float(stats.get("win_rate"), 50) / 100.0
        confidence = safe_float(stats.get("confidence"), 0)
        params = self.strategy_params()
        score = 50 + avg_ret * safe_float(params.get("history_return_coef"), 420) + (win_rate - 0.5) * safe_float(params.get("history_win_coef"), 45)
        score = 50 * (1 - confidence) + score * confidence
        return clamp(score), stats

    def _lhb_factor_profile(self, code: str, as_of: str) -> Dict[str, Any]:
        code = digits6(code)
        if not code or not as_of:
            return {"score": 50.0, "risk": 50.0, "net_buy_amount": 0.0, "hot_seat_count": 0, "sample_count": 0}
        cache_key = f"lhb-by-code:{as_of}"
        by_code = self._lhb_by_code_cache.get(cache_key)
        if by_code is None:
            lookback_days = self._cache_limit("QT_LHB_FACTOR_LOOKBACK_DAYS", 45, maximum=365)
            as_dt = parse_time(as_of)
            start_date = (as_dt - timedelta(days=max(1, lookback_days))).strftime("%Y-%m-%d") if as_dt else None
            rows_cache_key = f"lhb-rows:{start_date or ''}:{as_of}"
            rows_all = self._lhb_rows_cache.get(rows_cache_key)
            if rows_all is None:
                rows_all = self.load_lhb_records(
                    start_date=start_date,
                    end_date=as_of,
                    limit=self._cache_limit("QT_LHB_FACTOR_MAX_ROWS", 50000, maximum=200000),
                )
            by_code = {}
            for row in rows_all:
                row_code = digits6(row.get("stock_code"))
                if row_code:
                    by_code.setdefault(row_code, []).append(row)
            self._remember_lhb_cache(rows_cache_key, rows_all, cache_key, by_code)
        return lhb_factor_profile_from_rows(by_code.get(code, []))

    def factor_profile(self, code: str, as_of: str, technical: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        code = digits6(code)
        params = self.strategy_params()
        factor_signature = "|".join(
            str(params.get(key, ""))
            for key in (
                "factor_score_coef",
                "factor_momentum_weight",
                "factor_volume_weight",
                "factor_breakout_weight",
                "factor_lhb_weight",
            )
        )
        cache_key = (code, f"{as_of or ''}|{factor_signature}")
        cached = self._factor_cache.get(cache_key)
        if cached is not None:
            return cached
        technical = technical if isinstance(technical, dict) else self.technical_profile(code, as_of=as_of)
        lhb = self._lhb_factor_profile(code, as_of)
        payload = factor_profile_payload(params, technical, lhb)
        self._remember_factor(cache_key, payload)
        return payload

    def _agent_scores(self, event_bundle: Dict[str, Any], corr: Dict[str, Any], as_of: str) -> Dict[str, Any]:
        events: List[NewsEvent] = event_bundle["events"]
        main_event = max(events, key=lambda item: item.impact_score)
        avg_sentiment = statistics.mean(event.sentiment for event in events)
        max_ai_score = max((event.ai_score for event in events), default=0.0)
        avg_impact = statistics.mean(event.impact_score for event in events)
        technical = self.technical_profile(main_event.code, as_of=as_of)
        hist_score, hist_stats = self._historical_score(main_event, corr)
        params = self.strategy_params()
        factors = self.factor_profile(main_event.code, as_of, technical=technical)

        sentiment_score = 50 + avg_sentiment * params["sentiment_coef"] + max(0.0, max_ai_score - 5) * params["ai_score_coef"]
        event_score = avg_impact * params["event_impact_weight"] + hist_score * params["history_score_weight"]
        technical_score = safe_float(technical.get("score"), 50) + safe_float(factors.get("technical_adjustment"), 0)
        risk_score = 100 - safe_float(technical.get("risk"), 50) - safe_float(factors.get("risk_adjustment"), 0)
        if avg_sentiment < -0.2:
            risk_score -= params["negative_sentiment_risk_penalty"]
        if main_event.event_type == "风险事件":
            risk_score -= params["risk_event_penalty"]
        risk_score = clamp(risk_score)
        weights = {
            "sentiment": params["sentiment_weight"],
            "event": params["event_weight"],
            "technical": params["technical_weight"],
            "risk": params["risk_weight"],
        }
        buy_score = (
            clamp(sentiment_score) * weights["sentiment"]
            + clamp(event_score) * weights["event"]
            + clamp(technical_score) * weights["technical"]
            + risk_score * weights["risk"]
        )
        sell_score = clamp(
            100 - buy_score
            + max(0.0, -avg_sentiment) * params["sell_negative_sentiment_coef"]
            + max(0.0, safe_float(technical.get("risk"), 50) - 65) * params["sell_technical_risk_coef"]
        )
        agents = [
            {
                "agent": "新闻情绪Agent",
                "score": round(clamp(sentiment_score), 2),
                "vote": "多" if sentiment_score >= 60 else ("空" if sentiment_score <= 42 else "中性"),
                "rationale": f"情绪={avg_sentiment:.2f}, AI最高分={max_ai_score:.1f}",
            },
            {
                "agent": "事件影响Agent",
                "score": round(clamp(event_score), 2),
                "vote": "多" if event_score >= 62 else ("空" if event_score <= 42 else "中性"),
                "rationale": f"{main_event.event_type}/{main_event.industry}, 历史样本={hist_stats.get('samples', 0)}",
            },
            {
                "agent": "技术走势Agent",
                "score": round(clamp(technical_score), 2),
                "vote": "多" if technical_score >= 62 else ("空" if technical_score <= 42 else "中性"),
                "rationale": f"3日={technical.get('ret_3d')}%, 5日={technical.get('ret_5d')}%, 量比={technical.get('volume_ratio')}",
            },
            {
                "agent": "风控Agent",
                "score": round(risk_score, 2),
                "vote": "可交易" if risk_score >= 55 else "降权",
                "rationale": f"波动={technical.get('volatility')}%, 风险={technical.get('risk')}",
            },
        ]
        return {
            "buy_score": round(clamp(buy_score), 2),
            "sell_score": round(sell_score, 2),
            "agents": agents,
            "technical": technical,
            "factors": factors,
            "historical": hist_stats,
            "weights": weights,
            "components": {
                "sentiment_score": round(clamp(sentiment_score), 2),
                "event_score": round(clamp(event_score), 2),
                "technical_score": round(clamp(technical_score), 2),
                "risk_score": round(risk_score, 2),
                "factor_score": safe_float(factors.get("score"), 50),
                "factor_adjustment": safe_float(factors.get("technical_adjustment"), 0),
                "avg_sentiment": round(avg_sentiment, 4),
                "avg_impact": round(avg_impact, 2),
                "max_ai_score": round(max_ai_score, 2),
            },
            "strategy_params": params,
        }

    def recommendations(self, as_of: Optional[str] = None, lookback_days: int = 2, top_n: int = 30) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        all_dates = sorted({event.date for event in self.events() if event.date <= as_of})
        if not all_dates:
            return {"as_of": as_of, "items": [], "latest_events": [], "model_weights": self.model_weights(), "strategy_params": params}
        selected_dates = set(all_dates[-max(1, lookback_days) :])
        selected = [event for event in self.events() if event.date in selected_dates and event.date <= as_of]
        grouped: Dict[str, Dict[str, Any]] = {}
        for event in selected:
            if self._is_sample_event(event):
                continue
            if not self.universe.is_tradeable_a_share(event.code):
                continue
            grouped.setdefault(event.code, {"events": []})["events"].append(event)
        corr = self.correlation(as_of=as_of, hold_days=3, realized_by=as_of)
        items = []
        for code, bundle in grouped.items():
            scores = self._agent_scores(bundle, corr, as_of)
            events = sorted(bundle["events"], key=lambda item: item.impact_score, reverse=True)
            primary = events[0]
            buy_score = safe_float(scores.get("buy_score"), 0)
            sell_score = safe_float(scores.get("sell_score"), 0)
            action = "买入候选" if buy_score >= params["buy_threshold"] else ("重点观察" if buy_score >= params["watch_threshold"] else "暂不买入")
            if sell_score >= params["avoid_sell_threshold"] and buy_score < params["avoid_buy_ceiling"]:
                action = "回避/卖出"
            items.append(
                {
                    "code": code,
                    "name": self.universe.name(code, primary.name),
                    "action": action,
                    "buy_score": round(buy_score, 2),
                    "sell_score": round(sell_score, 2),
                    "short_term_direction": "up" if buy_score >= sell_score else "down",
                    "industry": primary.industry,
                    "event_type": primary.event_type,
                    "event_count": len(events),
                    "latest_event_date": max(event.date for event in events),
                    "impact_score": round(statistics.mean(event.impact_score for event in events), 2),
                    "sentiment": round(statistics.mean(event.sentiment for event in events), 3),
                    "reason": primary.reason,
                    "agents": scores["agents"],
                    "components": scores.get("components", {}),
                    "technical": scores["technical"],
                    "historical": scores["historical"],
                    "events": [event.compact() for event in events[:4]],
                }
            )
        items.sort(key=lambda item: (item["buy_score"], -item["sell_score"], item["impact_score"]), reverse=True)
        latest_events = [event.compact() for event in selected if not self._is_sample_event(event)][:60]
        return {
            "as_of": as_of,
            "lookback_days": lookback_days,
            "top_n": top_n,
            "items": items[:top_n],
            "latest_events": latest_events,
            "correlation": corr.get("global", {}),
            "model_weights": self.model_weights(),
            "strategy_params": params,
        }

    def _all_trading_dates_for_codes(
        self,
        codes: List[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[str]:
        return all_trading_dates_for_codes(
            self._sqlite_rows,
            self.load_kline,
            self._available_intraday_dates,
            codes,
            start_date=start_date,
            end_date=end_date,
        )

    def _row_on_date(self, code: str, date: str) -> Optional[Dict[str, Any]]:
        code = digits6(code)
        if not code:
            return None
        row_map = self._kline_row_map_cache.get(code)
        if row_map is None:
            row_map = {row["date"]: row for row in self.load_kline(code)}
            self._kline_row_map_cache[code] = row_map
            self._prune_cache(self._kline_row_map_cache, self._cache_limit("QT_KLINE_CACHE_MAX_CODES", 480, maximum=3000))
        return row_map.get(date)

    def _next_trading_date(self, dates: List[str], current_date: str) -> str:
        for date in dates:
            if date > current_date:
                return date
        return self._next_calendar_trading_date(current_date)

    def _next_calendar_trading_date(self, current_date: str) -> str:
        try:
            current = datetime.strptime(str(current_date or "")[:10], "%Y-%m-%d")
        except Exception:
            return ""
        holidays = {item.strip() for item in str(os.getenv("TRADING_HOLIDAYS", "") or "").split(",") if item.strip()}
        extra_days = {item.strip() for item in str(os.getenv("TRADING_EXTRA_DAYS", "") or "").split(",") if item.strip()}
        for offset in range(1, 15):
            candidate = current + timedelta(days=offset)
            text = candidate.strftime("%Y-%m-%d")
            if text in holidays:
                continue
            if text in extra_days or candidate.weekday() < 5:
                return text
        return ""

    def _performance_metrics(
        self,
        equity_curve: List[Dict[str, Any]],
        trades: List[Dict[str, Any]],
        initial_cash: float,
        final_value: float,
    ) -> Dict[str, Any]:
        return performance_metrics_payload(equity_curve, trades, initial_cash, final_value)

    def _historical_outcomes_for_replay(
        self,
        scoped_events: List[NewsEvent],
        start_date: Optional[str],
        end_date: Optional[str],
        hold_days: int,
    ) -> List[Dict[str, Any]]:
        history_limit = self._cache_limit("QT_REPLAY_HISTORY_EVENT_LIMIT", 800, maximum=100000)
        return historical_outcomes_for_replay(
            scoped_events,
            self.events(),
            start_date=start_date,
            end_date=end_date,
            hold_days=hold_days,
            history_limit=history_limit,
            future_return=lambda code, date, days: self.future_return(code, date, hold_days=days),
            is_sample_event=self._is_sample_event,
        )

    def walk_forward(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: Optional[float] = None,
        max_positions: Optional[int] = None,
        hold_days: Optional[int] = None,
        top_n: Optional[int] = None,
        auto_fill: bool = False,
    ) -> Dict[str, Any]:
        params = self.strategy_params()
        initial_cash = max(1.0, safe_float(initial_cash, params["account_initial_cash"]))
        max_positions = int(max_positions or params["max_positions"])
        hold_days = int(hold_days or params["max_hold_days"])
        top_n = int(top_n or params["top_n"])
        all_events = self.events()
        all_events = [event for event in all_events if not self._is_sample_event(event)]
        if end_date:
            all_events = [event for event in all_events if event.date <= end_date]
        if start_date:
            all_events = [event for event in all_events if event.date >= start_date]
        if not all_events:
            return empty_replay_result(start_date=start_date, end_date=end_date, initial_cash=initial_cash)

        start_date = start_date or min(event.date for event in all_events)
        end_date = end_date or max(event.date for event in all_events)
        auto_fill_result: Dict[str, Any] = {}
        if auto_fill:
            auto_fill_result = self.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=hold_days,
                max_codes=500,
                force=False,
            )
        codes = sorted({event.code for event in all_events})
        trading_dates = [
            date
            for date in self._all_trading_dates_for_codes(codes, start_date=start_date, end_date=end_date)
            if start_date <= date <= end_date
        ]
        if not trading_dates:
            return empty_replay_result(start_date=start_date, end_date=end_date, initial_cash=initial_cash)

        events_by_date: Dict[str, List[NewsEvent]] = {}
        for event in all_events:
            events_by_date.setdefault(event.date, []).append(event)

        cash = float(initial_cash)
        positions: List[Dict[str, Any]] = []
        pending_buys: List[Dict[str, Any]] = []
        trades: List[Dict[str, Any]] = []
        days: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        prev_total = float(initial_cash)
        historical_outcomes = self._historical_outcomes_for_replay(all_events, start_date, end_date, hold_days)
        replay_corr = ReplayCorrelationState(
            historical_outcomes,
            hold_days=hold_days,
            aggregate_stats=self._aggregate_stats,
        )

        for current_date in trading_dates:
            replay_corr.add_realized_outcomes_until(current_date)
            day_buys = []
            day_sells = []
            day_missed = []
            corr = replay_corr.current_corr(current_date)
            today_events = events_by_date.get(current_date, [])
            today_candidate_scores = build_replay_candidate_scores(
                today_events,
                corr=corr,
                current_date=current_date,
                params=params,
                score_bundle=self._agent_scores,
                stock_name=self.universe.name,
                is_tradeable=self.universe.is_tradeable_a_share,
            )
            today_score_map = {item["code"]: item for item in today_candidate_scores}

            still_pending = []
            held_codes = {pos["code"] for pos in positions}
            for order in pending_buys:
                execute_on = str(order.get("execute_on") or "")
                if execute_on and execute_on > current_date:
                    still_pending.append(order)
                    continue
                if len(positions) >= max_positions or order.get("code") in held_codes:
                    day_missed.append(
                        replay_missed_order(
                            current_date,
                            order,
                            "已持有该股" if order.get("code") in held_codes else "最大持仓数已满",
                        )
                    )
                    continue
                row = self._row_on_date(order["code"], current_date)
                if not row:
                    retries = int(order.get("retries", 0)) + 1
                    if retries <= 5:
                        delayed = dict(order)
                        delayed["retries"] = retries
                        still_pending.append(delayed)
                    else:
                        day_missed.append(
                            replay_missed_order(
                                current_date,
                                order,
                                "执行日无可用K线或停牌",
                            )
                        )
                    continue
                open_price = safe_float(row.get("open") or row.get("close"), 0)
                if open_price <= 0:
                    day_missed.append(
                        replay_missed_order(
                            current_date,
                            order,
                            "执行日开盘价无效",
                        )
                    )
                    continue
                slots_left = max(1, max_positions - len(positions))
                qty = lot_quantity_for_cash(
                    cash=cash,
                    price=open_price,
                    slots_left=slots_left,
                    initial_cash=float(initial_cash),
                    max_positions=max_positions,
                    fee_payload=lambda amount: self._broker_fees("BUY", amount),
                )
                if qty <= 0:
                    day_missed.append(
                        replay_missed_order(
                            current_date,
                            order,
                            "可用资金不足以买入一手",
                        )
                    )
                    continue
                gross_amount = qty * open_price
                fees = self._broker_fees("BUY", gross_amount)
                execution = daily_buy_execution(
                    order=order,
                    date=current_date,
                    price=open_price,
                    qty=qty,
                    fees=fees,
                )
                cash += execution["cash_delta"]
                position = execution["position"]
                positions.append(position)
                held_codes.add(order["code"])
                trade = execution["trade"]
                trades.append(trade)
                day_buys.append(trade)
            pending_buys = still_pending

            remaining_positions = []
            for pos in positions:
                row = self._row_on_date(pos["code"], current_date)
                if not row:
                    remaining_positions.append(pos)
                    continue
                close_price = safe_float(row.get("close"), 0)
                if close_price <= 0:
                    remaining_positions.append(pos)
                    continue
                pos = dict(pos)
                pos["hold_days"] = int(pos.get("hold_days", 0)) + 1
                entry_price = safe_float(pos.get("entry_price"), close_price)
                pnl_pct = (close_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                rec = today_score_map.get(pos["code"], {})
                should_sell = (
                    pnl_pct <= params["stop_loss_pct"]
                    or pnl_pct >= params["take_profit_pct"]
                    or pos["hold_days"] >= hold_days
                    or safe_float(rec.get("sell_score"), 0) >= params["sell_score_threshold"]
                )
                if should_sell:
                    qty = safe_float(pos.get("qty"), 0)
                    gross_amount = qty * close_price
                    fees = self._broker_fees("SELL", gross_amount)
                    execution = daily_sell_execution(
                        position=pos,
                        date=current_date,
                        price=close_price,
                        fees=fees,
                        stop_loss_pct=params["stop_loss_pct"],
                        take_profit_pct=params["take_profit_pct"],
                        sell_score=safe_float(rec.get("sell_score"), 0),
                        sell_score_threshold=params["sell_score_threshold"],
                    )
                    cash += execution["cash_delta"]
                    trade = execution["trade"]
                    trades.append(trade)
                    day_sells.append(trade)
                else:
                    pos["last_price"] = round(close_price, 3)
                    pos["pnl_pct"] = round(pnl_pct, 3)
                    remaining_positions.append(pos)
            positions = remaining_positions

            next_date = self._next_trading_date(trading_dates, current_date)
            signal_plan = build_daily_signal_orders(
                today_candidate_scores,
                current_date=current_date,
                next_date=next_date,
                positions=positions,
                pending_buys=pending_buys,
                top_n=top_n,
            )
            pending_buys.extend(signal_plan["orders"])
            signal_items = signal_plan["signals"]

            position_snapshots = []
            market_value = 0.0
            for pos in positions:
                row = self._row_on_date(pos["code"], current_date)
                close_price = safe_float(row.get("close"), pos.get("last_price", 0)) if row else safe_float(pos.get("last_price"), 0)
                snapshot, value = replay_position_snapshot(pos, close_price)
                market_value += value
                position_snapshots.append(snapshot)

            valuation = replay_day_valuation(
                date=current_date,
                cash=cash,
                market_value=market_value,
                prev_total=prev_total,
                initial_cash=float(initial_cash),
                position_count=len(position_snapshots),
            )
            prev_total = valuation["total_value"]
            day_record = {
                "date": current_date,
                "event_count": len(today_events),
                "signals": signal_items,
                "buys": day_buys,
                "sells": day_sells,
                "missed": day_missed,
                "pending_buys": list(pending_buys),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "total_value": valuation["total_value"],
                "daily_return_pct": valuation["daily_return_pct"],
                "positions": position_snapshots,
            }
            days.append(day_record)
            equity_curve.append(valuation["equity_point"])

        metrics = replay_final_metrics(
            equity_curve,
            trades,
            float(initial_cash),
            performance_metrics=self._performance_metrics,
        )
        return {
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": round(float(initial_cash), 2),
            **metrics,
            "strategy_params": params,
            "trades": trades,
            "days": days,
            "equity_curve": equity_curve,
            "auto_fill": auto_fill_result,
        }

    def _intraday_exit(
        self,
        position: Dict[str, Any],
        date: str,
        start_dt: Optional[datetime] = None,
        take_profit_pct: float = 8.0,
        stop_loss_pct: float = -5.0,
    ) -> Optional[Dict[str, Any]]:
        bars = self.load_intraday_bars(position.get("code"), date)
        if not bars:
            return None
        entry_price = safe_float(position.get("entry_price"), 0)
        if entry_price <= 0:
            return None
        stop_price = entry_price * (1 + stop_loss_pct / 100.0)
        take_price = entry_price * (1 + take_profit_pct / 100.0)
        for bar in bars:
            if start_dt is not None and bar["dt"] <= start_dt:
                continue
            # Conservative assumption: if stop and take-profit are both touched in
            # the same 5-minute bar, the stop-loss happens first.
            if safe_float(bar.get("low"), 0) <= stop_price:
                return {
                    "price": round(stop_price, 3),
                    "time": bar["time"],
                    "reason": "分时止损",
                    "mode": "intraday_5m",
                }
            if safe_float(bar.get("high"), 0) >= take_price:
                return {
                    "price": round(take_price, 3),
                    "time": bar["time"],
                    "reason": "分时止盈",
                    "mode": "intraday_5m",
                }
        return None

    def walk_forward_intraday(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: Optional[float] = None,
        max_positions: Optional[int] = None,
        hold_days: Optional[int] = None,
        top_n: Optional[int] = None,
        use_daily_fallback: bool = True,
        auto_fill: bool = False,
    ) -> Dict[str, Any]:
        params = self.strategy_params()
        initial_cash = max(1.0, safe_float(initial_cash, params["account_initial_cash"]))
        max_positions = int(max_positions or params["max_positions"])
        hold_days = int(hold_days or params["max_hold_days"])
        top_n = int(top_n or params["top_n"])
        all_events = self.events()
        all_events = [event for event in all_events if not self._is_sample_event(event)]
        if end_date:
            all_events = [event for event in all_events if event.date <= end_date]
        if start_date:
            all_events = [event for event in all_events if event.date >= start_date]
        if not all_events:
            return empty_replay_result(
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                mode="intraday_5m",
            )

        start_date = start_date or min(event.date for event in all_events)
        end_date = end_date or max(event.date for event in all_events)
        auto_fill_result: Dict[str, Any] = {}
        if auto_fill:
            auto_fill_result = self.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=hold_days,
                max_codes=500,
                force=False,
            )
        codes = sorted({event.code for event in all_events})
        trading_dates = [
            date
            for date in self._all_trading_dates_for_codes(codes, start_date=start_date, end_date=end_date)
            if start_date <= date <= end_date
        ]
        if not trading_dates:
            return empty_replay_result(
                start_date=start_date,
                end_date=end_date,
                initial_cash=initial_cash,
                mode="intraday_5m",
            )

        intraday_dates = self._available_intraday_dates(codes, start_date, end_date)
        events_by_date: Dict[str, List[NewsEvent]] = {}
        for event in all_events:
            events_by_date.setdefault(event.date, []).append(event)

        historical_outcomes = self._historical_outcomes_for_replay(all_events, start_date, end_date, hold_days)
        replay_corr = ReplayCorrelationState(
            historical_outcomes,
            hold_days=hold_days,
            aggregate_stats=self._aggregate_stats,
        )

        cash = float(initial_cash)
        positions: List[Dict[str, Any]] = []
        pending_buys: List[Dict[str, Any]] = []
        trades: List[Dict[str, Any]] = []
        days: List[Dict[str, Any]] = []
        equity_curve: List[Dict[str, Any]] = []
        prev_total = float(initial_cash)

        for current_date in trading_dates:
            replay_corr.add_realized_outcomes_until(current_date)
            corr = replay_corr.current_corr(current_date)
            today_events = events_by_date.get(current_date, [])
            day_buys: List[Dict[str, Any]] = []
            day_sells: List[Dict[str, Any]] = []
            day_intraday_codes = intraday_dates.get(current_date, set())

            today_candidate_scores = build_replay_candidate_scores(
                today_events,
                corr=corr,
                current_date=current_date,
                params=params,
                score_bundle=self._agent_scores,
                stock_name=self.universe.name,
                is_tradeable=self.universe.is_tradeable_a_share,
                signal_dt=self._event_signal_dt,
            )
            today_score_map = {item["code"]: item for item in today_candidate_scores}

            still_pending = []
            held_codes = {pos["code"] for pos in positions}
            for order in pending_buys:
                execute_on = str(order.get("execute_on") or "")
                if execute_on and execute_on > current_date:
                    still_pending.append(order)
                    continue
                if len(positions) >= max_positions or order.get("code") in held_codes:
                    continue
                entry_bar = self._first_intraday_bar(order["code"], current_date)
                entry_mode = "intraday_5m"
                entry_time = entry_bar["time"] if entry_bar else f"{current_date} 09:30:00"
                entry_price = safe_float(entry_bar.get("open"), 0) if entry_bar else 0
                if entry_price <= 0 and use_daily_fallback:
                    row = self._row_on_date(order["code"], current_date)
                    entry_price = safe_float(row.get("open"), 0) if row else 0
                    entry_mode = "daily_open_fallback"
                if entry_price <= 0:
                    retries = int(order.get("retries", 0)) + 1
                    if retries <= 5:
                        delayed = dict(order)
                        delayed["retries"] = retries
                        still_pending.append(delayed)
                    continue
                slots_left = max(1, max_positions - len(positions))
                qty = lot_quantity_for_cash(
                    cash=cash,
                    price=entry_price,
                    slots_left=slots_left,
                    initial_cash=float(initial_cash),
                    max_positions=max_positions,
                )
                if qty <= 0:
                    continue
                execution = intraday_buy_execution(
                    order=order,
                    date=current_date,
                    time=entry_time,
                    price=entry_price,
                    qty=qty,
                    mode=entry_mode,
                )
                cash += execution["cash_delta"]
                position = execution["position"]
                positions.append(position)
                held_codes.add(order["code"])
                trade = execution["trade"]
                trades.append(trade)
                day_buys.append(trade)
            pending_buys = still_pending

            remaining_positions = []
            for pos in positions:
                pos = dict(pos)
                row = self._row_on_date(pos["code"], current_date)
                bars = self.load_intraday_bars(pos["code"], current_date)
                entry_dt = parse_time(pos.get("entry_time")) if pos.get("entry_date") == current_date else None
                exit_info = self._intraday_exit(
                    pos,
                    current_date,
                    start_dt=entry_dt,
                    take_profit_pct=params["take_profit_pct"],
                    stop_loss_pct=params["stop_loss_pct"],
                )
                close_price = safe_float(bars[-1].get("close"), 0) if bars else 0
                close_time = bars[-1]["time"] if bars else f"{current_date} 15:00:00"
                if close_price <= 0 and row:
                    close_price = safe_float(row.get("close"), 0)
                has_price = close_price > 0
                if has_price:
                    pos["hold_days"] = int(pos.get("hold_days", 0)) + 1
                entry_price = safe_float(pos.get("entry_price"), close_price)
                pnl_pct = (close_price / entry_price - 1) * 100 if entry_price > 0 and close_price > 0 else 0.0
                rec = today_score_map.get(pos["code"], {})
                if exit_info is None and has_price:
                    if pos["hold_days"] >= hold_days:
                        exit_info = {"price": close_price, "time": close_time, "reason": "持仓到期", "mode": "intraday_5m_eod" if bars else "daily_close_fallback"}
                    elif safe_float(rec.get("sell_score"), 0) >= params["sell_score_threshold"]:
                        exit_info = {"price": close_price, "time": close_time, "reason": "卖出评分触发", "mode": "intraday_5m_eod" if bars else "daily_close_fallback"}
                if exit_info is not None:
                    execution = intraday_sell_execution(
                        position=pos,
                        date=current_date,
                        time=exit_info.get("time", close_time),
                        price=safe_float(exit_info.get("price"), close_price),
                        reason=exit_info.get("reason", ""),
                        mode=exit_info.get("mode", ""),
                    )
                    cash += execution["cash_delta"]
                    trade = execution["trade"]
                    trades.append(trade)
                    day_sells.append(trade)
                else:
                    pos["last_price"] = round(close_price, 3) if close_price > 0 else safe_float(pos.get("last_price"), 0)
                    pos["pnl_pct"] = round(pnl_pct, 3)
                    remaining_positions.append(pos)
            positions = remaining_positions

            signal_items = []
            if today_events:
                held_or_pending = {pos["code"] for pos in positions} | {order["code"] for order in pending_buys}
                next_date = self._next_trading_date(trading_dates, current_date)
                for item in today_candidate_scores[:top_n]:
                    if item.get("action") != BUY_ACTION:
                        continue
                    if item["code"] in held_or_pending or len(positions) + len(pending_buys) >= max_positions:
                        continue
                    entry_bar = self._next_intraday_bar_after(item["code"], current_date, item.get("signal_dt"))
                    order = build_intraday_signal_order(
                        item,
                        current_date=current_date,
                        next_date=next_date,
                        execute_today=bool(entry_bar),
                    )
                    if entry_bar:
                        entry_price = safe_float(entry_bar.get("open"), 0)
                        slots_left = max(1, max_positions - len(positions))
                        qty = lot_quantity_for_cash(
                            cash=cash,
                            price=entry_price,
                            slots_left=slots_left,
                            initial_cash=float(initial_cash),
                            max_positions=max_positions,
                        )
                        if qty <= 0:
                            continue
                        execution = intraday_buy_execution(
                            order=order,
                            date=current_date,
                            time=entry_bar["time"],
                            price=entry_price,
                            qty=qty,
                            mode="intraday_5m",
                        )
                        cash += execution["cash_delta"]
                        position = execution["position"]
                        trade = execution["trade"]
                        trades.append(trade)
                        day_buys.append(trade)
                        exit_info = self._intraday_exit(
                            position,
                            current_date,
                            start_dt=entry_bar["dt"],
                            take_profit_pct=params["take_profit_pct"],
                            stop_loss_pct=params["stop_loss_pct"],
                        )
                        if exit_info is not None:
                            execution = intraday_sell_execution(
                                position=position,
                                date=current_date,
                                time=exit_info.get("time", entry_bar["time"]),
                                price=safe_float(exit_info.get("price"), entry_price),
                                reason=exit_info.get("reason", ""),
                                mode=exit_info.get("mode", ""),
                            )
                            cash += execution["cash_delta"]
                            sell_trade = execution["trade"]
                            trades.append(sell_trade)
                            day_sells.append(sell_trade)
                        else:
                            positions.append(position)
                            held_or_pending.add(item["code"])
                        signal_items.append({**order, "execute_on": current_date, "mode": "intraday_5m"})
                    elif next_date:
                        pending_buys.append(order)
                        held_or_pending.add(item["code"])
                        signal_items.append({**order, "mode": "next_session"})

            position_snapshots = []
            market_value = 0.0
            for pos in positions:
                bars = self.load_intraday_bars(pos["code"], current_date)
                row = self._row_on_date(pos["code"], current_date)
                close_price = safe_float(bars[-1].get("close"), 0) if bars else 0
                if close_price <= 0 and row:
                    close_price = safe_float(row.get("close"), 0)
                if close_price <= 0:
                    close_price = safe_float(pos.get("last_price"), 0)
                snapshot, value = replay_position_snapshot(
                    pos,
                    close_price,
                    include_entry_time=True,
                    include_entry_mode=True,
                    require_close_for_pnl=True,
                )
                market_value += value
                position_snapshots.append(snapshot)

            valuation = replay_day_valuation(
                date=current_date,
                cash=cash,
                market_value=market_value,
                prev_total=prev_total,
                initial_cash=float(initial_cash),
                position_count=len(position_snapshots),
            )
            prev_total = valuation["total_value"]
            day_record = {
                "date": current_date,
                "event_count": len(today_events),
                "intraday_code_count": len(day_intraday_codes),
                "signals": signal_items,
                "buys": day_buys,
                "sells": day_sells,
                "pending_buys": list(pending_buys),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "total_value": valuation["total_value"],
                "daily_return_pct": valuation["daily_return_pct"],
                "positions": position_snapshots,
            }
            days.append(day_record)
            equity_curve.append(valuation["equity_point"])

        metrics = replay_final_metrics(
            equity_curve,
            trades,
            float(initial_cash),
            performance_metrics=self._performance_metrics,
        )
        intraday_trade_count = sum(1 for trade in trades if str(trade.get("mode", "")).startswith("intraday_5m"))
        fallback_trade_count = len(trades) - intraday_trade_count
        return {
            "mode": "intraday_5m",
            "daily_fallback": bool(use_daily_fallback),
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": round(float(initial_cash), 2),
            **metrics,
            "intraday_available_dates": sorted(intraday_dates.keys()),
            "intraday_trade_count": intraday_trade_count,
            "fallback_trade_count": fallback_trade_count,
            "strategy_params": params,
            "trades": trades,
            "days": days,
            "equity_curve": equity_curve,
            "auto_fill": auto_fill_result,
        }

    def _backtest_event_score(self, events: List[NewsEvent]) -> float:
        return backtest_event_score(events)

    def _backtest_data_diagnostics(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        hold_days: int,
    ) -> Dict[str, Any]:
        events = [
            event
            for event in self.events()
            if not self._is_sample_event(event)
        ]
        return backtest_data_diagnostics(
            events=events,
            start_date=start_date,
            end_date=end_date,
            hold_days=hold_days,
            load_kline=self.load_kline,
            future_return=lambda code, event_date, days: self.future_return(code, event_date, hold_days=days),
            is_tradeable=self.universe.is_tradeable_a_share,
            sqlite_file=QUANT_DB_FILE,
        )

    def backtest(
        self,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_cash: Optional[float] = None,
        max_positions: Optional[int] = None,
        hold_days: int = 3,
        top_n: int = 5,
        auto_fill: bool = False,
    ) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        end_date = end_date or as_of
        hold_days = max(1, min(int(hold_days or 3), 60))
        top_n = max(1, min(int(top_n or 5), 50))
        auto_fill_result: Dict[str, Any] = {}
        if auto_fill:
            auto_fill_result = self.ensure_daily_kline_for_events(
                start_date=start_date,
                end_date=end_date,
                hold_days=hold_days,
                max_codes=max(50, int(top_n or 5) * 40),
                force=False,
            )
        groups: Dict[Tuple[str, str], List[NewsEvent]] = {}
        for event in self.events():
            if event.date >= end_date:
                continue
            if self._is_sample_event(event):
                continue
            if start_date and event.date < start_date:
                continue
            groups.setdefault((event.date, event.code), []).append(event)
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for (date, code), events in groups.items():
            score = self._backtest_event_score(events)
            if score < 58:
                continue
            by_date.setdefault(date, []).append({"date": date, "code": code, "events": events, "score": score})

        trades = []
        for date, candidates in sorted(by_date.items()):
            ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)[: max(1, top_n)]
            for item in ranked:
                realized = self.future_return(item["code"], item["date"], hold_days=hold_days)
                if not realized:
                    continue
                primary = max(item["events"], key=lambda event: event.impact_score)
                trades.append(
                    {
                        "date": item["date"],
                        "code": item["code"],
                        "name": self.universe.name(item["code"], primary.name),
                        "score": round(item["score"], 2),
                        "industry": primary.industry,
                        "event_type": primary.event_type,
                        **realized,
                    }
                )

        outcome_summary = backtest_event_outcome_summary(
            trades,
            top_n=top_n,
            aggregate_stats=self._aggregate_stats,
        )

        timeline = self.walk_forward(
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            max_positions=max_positions,
            hold_days=hold_days,
            top_n=top_n,
            auto_fill=False,
        )
        diagnostics = self._backtest_data_diagnostics(start_date=start_date, end_date=end_date, hold_days=hold_days)
        timeline_trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
        timeline_performance = timeline.get("performance") if isinstance(timeline.get("performance"), dict) else {}
        backtest_account = self.account_from_trades(
            timeline_trades,
            initial_cash=timeline.get("initial_cash", initial_cash),
            as_of=end_date,
            limit=500,
        )
        status = "ok" if (trades or timeline_trades or diagnostics.get("event_count", 0) > 0) else "no_data"
        message = "backtest completed"
        if status == "no_data":
            message = "no events or market data available for the requested range"
        elif not timeline_trades:
            message = "events were found, but no closed strategy trades were generated; check thresholds and K-line coverage"

        return {
            "status": status,
            "message": message,
            "as_of": as_of,
            "start_date": start_date,
            "end_date": end_date,
            "hold_days": hold_days,
            "top_n": top_n,
            "trades": len(trades),
            "event_outcome_trades": len(trades),
            "timeline_trade_count": len(timeline_trades),
            "closed_trades": int(timeline.get("closed_trades") or 0),
            "initial_cash": timeline.get("initial_cash"),
            "final_value": timeline.get("final_value"),
            "return_pct": timeline.get("return_pct", 0.0),
            "annualized_return_pct": timeline.get("annualized_return_pct", timeline_performance.get("annualized_return_pct", 0.0)),
            "sharpe_ratio": timeline.get("sharpe_ratio", timeline_performance.get("sharpe_ratio", 0.0)),
            "profit_factor": timeline.get("profit_factor", timeline_performance.get("profit_factor", 0.0)),
            "total_fees": timeline.get("total_fees", timeline_performance.get("total_fees", 0.0)),
            "exposure_pct": timeline.get("exposure_pct", timeline_performance.get("exposure_pct", 0.0)),
            "timeline_win_rate": timeline.get("win_rate", 0.0),
            "timeline_max_drawdown_pct": timeline.get("max_drawdown_pct", 0.0),
            "avg_return_pct": outcome_summary["avg_return_pct"],
            "median_return_pct": outcome_summary["median_return_pct"],
            "win_rate": outcome_summary["win_rate"],
            "compounded_return_pct": outcome_summary["compounded_return_pct"],
            "max_drawdown_pct": outcome_summary["max_drawdown_pct"],
            "score_buckets": outcome_summary["score_buckets"],
            "recent_trades": trades[-80:],
            "trade_records": timeline_trades[-500:],
            "account": backtest_account.get("account", {}),
            "positions": backtest_account.get("positions", []),
            "delivery_records": backtest_account.get("delivery_records", []),
            "daily_settlements": backtest_account.get("daily_settlements", []),
            "days": timeline.get("days", [])[-260:] if isinstance(timeline.get("days"), list) else [],
            "equity_curve": timeline.get("equity_curve", []),
            "performance": timeline_performance,
            "strategy_params": timeline.get("strategy_params", self.strategy_params()),
            "data_diagnostics": diagnostics,
            "auto_fill": auto_fill_result,
        }

    def rebuild_paper_from_replay(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        mode: str = "daily",
    ) -> Dict[str, Any]:
        start_date = str(start_date or self.first_data_date()).strip()
        end_date = str(end_date or self.latest_event_date()).strip()
        mode = str(mode or "daily").strip().lower()
        if mode == "intraday":
            timeline = self.walk_forward_intraday(start_date=start_date, end_date=end_date, use_daily_fallback=True)
        else:
            timeline = self.walk_forward(start_date=start_date, end_date=end_date)
        days = timeline.get("days") if isinstance(timeline.get("days"), list) else []
        trades = timeline.get("trades") if isinstance(timeline.get("trades"), list) else []
        last_day = days[-1] if days else {}
        positions = []
        for pos in last_day.get("positions", []) if isinstance(last_day.get("positions"), list) else []:
            if not isinstance(pos, dict):
                continue
            code = digits6(pos.get("code"))
            if not code:
                continue
            positions.append(
                {
                    "code": code,
                    "name": pos.get("name") or self.universe.name(code),
                    "qty": safe_float(pos.get("qty"), 0),
                    "entry_price": safe_float(pos.get("entry_price"), 0),
                    "entry_date": str(pos.get("entry_date") or ""),
                    "last_price": safe_float(pos.get("last_price"), 0),
                    "buy_score": safe_float(pos.get("buy_score"), 0),
                    "reason": pos.get("reason", ""),
                }
            )
        params = self.strategy_params()
        state = self._load_state()
        state["initial_cash"] = timeline.get("initial_cash", params["account_initial_cash"])
        state["cash"] = safe_float(last_day.get("cash"), timeline.get("final_value", params["account_initial_cash"]))
        state["positions"] = positions
        state["trades"] = trades[-2000:]
        state["as_of"] = end_date
        state["paper_replay"] = {
            "mode": timeline.get("mode") or mode,
            "start_date": start_date,
            "end_date": end_date,
            "return_pct": timeline.get("return_pct", 0),
            "closed_trades": timeline.get("closed_trades", 0),
            "win_rate": timeline.get("win_rate", 0),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_state(state)
        portfolio = self.paper_portfolio(as_of=end_date)
        return {
            "status": "ok",
            "mode": timeline.get("mode") or mode,
            "start_date": start_date,
            "end_date": end_date,
            "timeline": {
                "return_pct": timeline.get("return_pct", 0),
                "final_value": timeline.get("final_value", 0),
                "closed_trades": timeline.get("closed_trades", 0),
                "win_rate": timeline.get("win_rate", 0),
                "trade_count": len(trades),
                "day_count": len(days),
            },
            "portfolio": portfolio,
        }

    def paper_portfolio(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        state = self._load_state()
        positions = state.get("positions") if isinstance(state.get("positions"), list) else []
        params = self.strategy_params()
        cash = safe_float(state.get("cash"), params["account_initial_cash"])
        updated_positions = []
        total_value = cash
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            code = digits6(pos.get("code"))
            if is_sample_code(code) or contains_sample_marker(pos):
                continue
            price_row = self.latest_price(code, as_of=as_of)
            if not price_row:
                updated_positions.append(pos)
                continue
            last_price = safe_float(price_row.get("close"), 0)
            qty = safe_float(pos.get("qty"), 0)
            entry_price = safe_float(pos.get("entry_price"), last_price)
            market_value = qty * last_price
            pnl_pct = (last_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
            enriched = dict(pos)
            enriched.update(
                {
                    "last_price": round(last_price, 3),
                    "last_date": price_row["date"],
                    "market_value": round(market_value, 2),
                    "pnl_pct": round(pnl_pct, 3),
                }
            )
            total_value += market_value
            updated_positions.append(enriched)
        return {
            "as_of": as_of,
            "cash": round(cash, 2),
            "positions": updated_positions,
            "trades": state.get("trades", [])[-100:],
            "total_value": round(total_value, 2),
            "model_weights": self.model_weights(),
            "strategy_params": self.strategy_params(),
            "last_calibration": state.get("last_calibration", {}),
        }

    def _broker_fees(self, side: str, amount: float) -> Dict[str, float]:
        return accounting_broker_fees(side, amount, DEFAULT_BROKER_FEE_PARAMS)

    def _trade_clock(self, trade: Dict[str, Any]) -> str:
        return accounting_trade_clock(trade)

    def account_from_trades(
        self,
        trades: List[Dict[str, Any]],
        initial_cash: Optional[float] = None,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        limit: int = 0,
        drop_unmatched_sells: bool = False,
    ) -> Dict[str, Any]:
        return account_from_trades_payload(
            trades,
            initial_cash=initial_cash,
            as_of=as_of,
            start_date=start_date,
            limit=limit,
            drop_unmatched_sells=drop_unmatched_sells,
            strategy_params=self.strategy_params(),
            latest_event_date=self.latest_event_date,
            latest_price=self.latest_price,
            stock_name=self.universe.name,
            is_sample_trade=contains_sample_marker,
            fee_params=DEFAULT_BROKER_FEE_PARAMS,
        )

    def trading_account(self, as_of: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        limit = max(1, min(int(limit or 500), 2000))
        state = self._load_state()
        portfolio = self.paper_portfolio(as_of=as_of)
        raw_trades = state.get("trades") if isinstance(state.get("trades"), list) else []
        raw_trades = [trade for trade in raw_trades if isinstance(trade, dict) and not contains_sample_marker(trade)]
        visible_trades = [trade for trade in raw_trades if not as_of or str(trade.get("date", "")) <= as_of]

        lots_by_code: Dict[str, List[Dict[str, Any]]] = {}
        deals: List[Dict[str, Any]] = []
        daily_settlement: Dict[str, Dict[str, float]] = {}
        total_fees = 0.0
        realized_pnl = 0.0
        params = self.strategy_params()
        initial_asset = safe_float(state.get("initial_cash"), params["account_initial_cash"])
        adjusted_cash = initial_asset

        for index, trade in enumerate(visible_trades, start=1):
            side = str(trade.get("side") or "").upper()
            if side not in {"BUY", "SELL"}:
                continue
            code = digits6(trade.get("code"))
            if not code:
                continue
            qty = safe_float(trade.get("qty"), 0)
            price = safe_float(trade.get("price"), 0)
            amount = qty * price
            if qty <= 0 or price <= 0 or amount <= 0:
                continue

            fees = self._broker_fees(side, amount)
            total_fees += fees["total_fee"]
            trade_date = str(trade.get("date") or "")
            trade_time = self._trade_clock(trade)
            name = str(trade.get("name") or self.universe.name(code))
            cash_flow = 0.0
            cost_amount = 0.0
            deal_realized = 0.0

            if side == "BUY":
                cash_flow = -(amount + fees["total_fee"])
                cost_amount = amount + fees["total_fee"]
                lots_by_code.setdefault(code, []).append(
                    {
                        "qty": qty,
                        "price": price,
                        "cost_amount": cost_amount,
                        "entry_date": trade_date,
                        "name": name,
                        "reason": trade.get("reason", ""),
                        "buy_score": safe_float(trade.get("score"), 0),
                    }
                )
            else:
                sell_qty_left = qty
                queue = lots_by_code.setdefault(code, [])
                while sell_qty_left > 0 and queue:
                    lot = queue[0]
                    lot_qty = safe_float(lot.get("qty"), 0)
                    if lot_qty <= 0:
                        queue.pop(0)
                        continue
                    matched = min(sell_qty_left, lot_qty)
                    lot_cost = safe_float(lot.get("cost_amount"), 0) * matched / lot_qty
                    cost_amount += lot_cost
                    lot["qty"] = lot_qty - matched
                    lot["cost_amount"] = safe_float(lot.get("cost_amount"), 0) - lot_cost
                    sell_qty_left -= matched
                    if lot["qty"] <= 0.000001:
                        queue.pop(0)
                if sell_qty_left > 0:
                    pnl_pct = safe_float(trade.get("pnl_pct"), 0)
                    fallback_cost_price = price / (1 + pnl_pct / 100) if pnl_pct > -99.0 else price
                    cost_amount += sell_qty_left * fallback_cost_price
                cash_flow = amount - fees["total_fee"]
                deal_realized = cash_flow - cost_amount
                realized_pnl += deal_realized
            adjusted_cash += cash_flow

            direction = "买入" if side == "BUY" else "卖出"
            deal = {
                "deal_id": f"{trade_date.replace('-', '')}-{index:05d}",
                "date": trade_date,
                "time": trade_time,
                "side": side,
                "direction": direction,
                "code": code,
                "name": name,
                "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                "price": round(price, 3),
                "amount": round(amount, 2),
                "commission": fees["commission"],
                "stamp_duty": fees["stamp_duty"],
                "transfer_fee": fees["transfer_fee"],
                "total_fee": fees["total_fee"],
                "net_amount": round(cash_flow, 2),
                "cost_amount": round(cost_amount, 2),
                "realized_pnl": round(deal_realized, 2),
                "score": round(safe_float(trade.get("score"), 0), 2) if trade.get("score") is not None else None,
                "pnl_pct": round(safe_float(trade.get("pnl_pct"), 0), 3) if trade.get("pnl_pct") is not None else None,
                "reason": trade.get("reason", ""),
            }
            deals.append(deal)

            bucket = daily_settlement.setdefault(
                trade_date,
                {
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "commission": 0.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                    "total_fee": 0.0,
                    "net_amount": 0.0,
                    "realized_pnl": 0.0,
                    "deal_count": 0.0,
                },
            )
            if side == "BUY":
                bucket["buy_amount"] += amount
            else:
                bucket["sell_amount"] += amount
            bucket["commission"] += fees["commission"]
            bucket["stamp_duty"] += fees["stamp_duty"]
            bucket["transfer_fee"] += fees["transfer_fee"]
            bucket["total_fee"] += fees["total_fee"]
            bucket["net_amount"] += cash_flow
            bucket["realized_pnl"] += deal_realized
            bucket["deal_count"] += 1

        remaining_lots = {
            code: [lot for lot in lots if safe_float(lot.get("qty"), 0) > 0]
            for code, lots in lots_by_code.items()
        }
        position_sources: List[Dict[str, Any]] = []
        for code, lots in remaining_lots.items():
            qty = sum(safe_float(lot.get("qty"), 0) for lot in lots)
            if qty <= 0:
                continue
            cost_amount = sum(safe_float(lot.get("cost_amount"), 0) for lot in lots)
            first_lot = lots[0] if lots else {}
            price_row = self.latest_price(code, as_of=as_of)
            last_price = safe_float((price_row or {}).get("close"), safe_float(first_lot.get("price"), 0))
            position_sources.append(
                {
                    "code": code,
                    "name": first_lot.get("name") or self.universe.name(code),
                    "qty": qty,
                    "entry_price": safe_float(first_lot.get("price"), 0),
                    "entry_date": str(first_lot.get("entry_date") or ""),
                    "buy_score": safe_float(first_lot.get("buy_score"), 0),
                    "reason": first_lot.get("reason", ""),
                    "last_price": last_price,
                    "last_date": (price_row or {}).get("date", as_of),
                    "last_time": (price_row or {}).get("time", ""),
                    "price_source": (price_row or {}).get("source", "daily"),
                    "_lot_cost_amount": cost_amount,
                    "_lot_qty": qty,
                }
            )

        existing_codes = {digits6(pos.get("code")) for pos in position_sources}
        for pos in portfolio.get("positions", []):
            code = digits6(pos.get("code"))
            entry_date = str(pos.get("entry_date") or "")
            if not code or code in existing_codes or (entry_date and entry_date > as_of):
                continue
            position_sources.append(pos)
            existing_codes.add(code)

        enriched_positions = []
        position_cost = 0.0
        market_value = 0.0
        for pos in position_sources:
            code = digits6(pos.get("code"))
            qty = safe_float(pos.get("qty"), 0)
            last_price = safe_float(pos.get("last_price"), pos.get("entry_price", 0))
            raw_entry_price = safe_float(pos.get("entry_price"), last_price)
            lots = remaining_lots.get(code, [])
            lot_qty = sum(safe_float(lot.get("qty"), 0) for lot in lots)
            lot_cost = safe_float(pos.get("_lot_cost_amount"), 0) or sum(safe_float(lot.get("cost_amount"), 0) for lot in lots)
            if lot_qty > 0:
                cost_price = lot_cost / lot_qty
            else:
                cost_price = raw_entry_price
                lot_cost = qty * cost_price
            cost_amount = qty * cost_price
            value = qty * last_price
            pnl_amount = value - cost_amount
            pnl_pct = pnl_amount / cost_amount * 100 if cost_amount > 0 else 0.0
            position_cost += cost_amount
            market_value += value
            entry_date = str(pos.get("entry_date") or "")
            enriched_positions.append(
                {
                    **pos,
                    "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
                    "available_qty": int(qty) if entry_date < as_of and float(qty).is_integer() else (round(qty, 2) if entry_date < as_of else 0),
                    "frozen_qty": 0 if entry_date < as_of else (int(qty) if float(qty).is_integer() else round(qty, 2)),
                    "cost_price": round(cost_price, 3),
                    "cost_amount": round(cost_amount, 2),
                    "last_price": round(last_price, 3),
                    "market_value": round(value, 2),
                    "pnl_amount": round(pnl_amount, 2),
                    "pnl_pct": round(pnl_pct, 3),
                }
            )

        state_cash = safe_float(state.get("cash"), initial_asset)
        total_asset = adjusted_cash + market_value
        total_pnl = total_asset - initial_asset
        settlement_rows = []
        for date, item in daily_settlement.items():
            settlement_rows.append(
                {
                    "date": date,
                    "buy_amount": round(item["buy_amount"], 2),
                    "sell_amount": round(item["sell_amount"], 2),
                    "commission": round(item["commission"], 2),
                    "stamp_duty": round(item["stamp_duty"], 2),
                    "transfer_fee": round(item["transfer_fee"], 2),
                    "total_fee": round(item["total_fee"], 2),
                    "net_amount": round(item["net_amount"], 2),
                    "realized_pnl": round(item["realized_pnl"], 2),
                    "deal_count": int(item["deal_count"]),
                }
            )

        deals.sort(key=lambda item: (item.get("time", ""), item.get("deal_id", "")), reverse=True)
        settlement_rows.sort(key=lambda item: item["date"], reverse=True)
        today_deals = [deal for deal in deals if deal.get("date") == as_of]
        return {
            "status": "ok",
            "as_of": as_of,
            "account": {
                "total_asset": round(total_asset, 2),
                "cash": round(adjusted_cash, 2),
                "available_cash": round(max(0.0, adjusted_cash), 2),
                "frozen_cash": 0.0,
                "state_cash_gross": round(state_cash, 2),
                "market_value": round(market_value, 2),
                "position_cost": round(position_cost, 2),
                "unrealized_pnl": round(market_value - position_cost, 2),
                "realized_pnl": round(realized_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "return_pct": round(total_pnl / initial_asset * 100, 3) if initial_asset > 0 else 0.0,
                "position_count": len(enriched_positions),
                "deal_count": len(deals),
                "total_fees": round(total_fees, 2),
            },
            "fee_rules": DEFAULT_BROKER_FEE_PARAMS,
            "positions": enriched_positions,
            "today_deals": today_deals[:limit],
            "history_deals": deals[:limit],
            "delivery_records": deals[:limit],
            "daily_settlements": settlement_rows[:limit],
            "portfolio": portfolio,
        }

    def run_paper_trading(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        state = self._load_state()
        recommendations = self.recommendations(as_of=as_of, lookback_days=2, top_n=20)
        by_code = {item["code"]: item for item in recommendations.get("items", [])}
        state.setdefault("initial_cash", params["account_initial_cash"])
        cash = safe_float(state.get("cash"), params["account_initial_cash"])
        positions = state.get("positions") if isinstance(state.get("positions"), list) else []
        trades = state.get("trades") if isinstance(state.get("trades"), list) else []
        next_positions = []

        for pos in positions:
            code = digits6(pos.get("code"))
            if is_sample_code(code) or contains_sample_marker(pos):
                continue
            price_row = self.latest_price(code, as_of=as_of)
            if not code or not price_row:
                next_positions.append(pos)
                continue
            last_price = safe_float(price_row.get("close"), 0)
            entry_price = safe_float(pos.get("entry_price"), last_price)
            qty = safe_float(pos.get("qty"), 0)
            pnl_pct = (last_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
            rec = by_code.get(code, {})
            entry_date = str(pos.get("entry_date") or as_of)
            hold_days = sum(1 for row in self.load_kline(code) if entry_date <= row["date"] <= as_of)
            should_sell = (
                pnl_pct <= params["stop_loss_pct"]
                or pnl_pct >= params["take_profit_pct"]
                or hold_days >= int(params["paper_max_hold_days"])
                or safe_float(rec.get("sell_score"), 0) >= params["sell_score_threshold"]
            )
            if should_sell and qty > 0 and last_price > 0:
                cash += qty * last_price
                trades.append(
                    {
                        "side": "SELL",
                        "date": as_of,
                        "code": code,
                        "name": self.universe.name(code),
                        "qty": qty,
                        "price": round(last_price, 3),
                        "pnl_pct": round(pnl_pct, 3),
                        "reason": "止盈/止损/持仓到期/卖出评分触发",
                    }
                )
            else:
                next_positions.append(pos)

        held = {digits6(pos.get("code")) for pos in next_positions}
        slots = max(0, int(params["max_positions"]) - len(next_positions))
        buy_candidates = [
            item
            for item in recommendations.get("items", [])
            if item["action"] == "买入候选"
            and item["code"] not in held
            and not contains_sample_marker(item)
            and safe_float(item.get("buy_score"), 0) >= params["buy_threshold"]
        ][:slots]
        for item in buy_candidates:
            price_row = self.latest_price(item["code"], as_of=as_of)
            if not price_row:
                continue
            price = safe_float(price_row.get("close"), 0)
            if price <= 0:
                continue
            allocation = min(cash / max(1, slots), params["paper_position_value"])
            qty = math.floor(allocation / price / 100) * 100
            if qty <= 0:
                continue
            cash -= qty * price
            next_positions.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "qty": qty,
                    "entry_price": round(price, 3),
                    "entry_date": as_of,
                    "buy_score": item["buy_score"],
                    "reason": item["reason"][:180],
                }
            )
            trades.append(
                {
                    "side": "BUY",
                    "date": as_of,
                    "code": item["code"],
                    "name": item["name"],
                    "qty": qty,
                    "price": round(price, 3),
                    "score": item["buy_score"],
                    "reason": item["reason"][:180],
                }
            )
            slots = max(1, slots - 1)

        state["cash"] = round(cash, 2)
        state["positions"] = next_positions
        state["trades"] = trades[-500:]
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        state["as_of"] = as_of
        self._save_state(state)
        return self.paper_portfolio(as_of=as_of)

    def fit_strategy(
        self,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        apply_best: bool = True,
    ) -> Dict[str, Any]:
        end_date = end_date or as_of or self.latest_event_date()
        start_date = start_date or self.first_data_date()
        base = self.strategy_params()

        def normalized_candidate(name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
            return {"name": name, "params": self._normalize_strategy_params({**base, **updates})}

        candidates = [
            normalized_candidate("当前默认基础参数", {}),
            normalized_candidate(
                "进攻型",
                {
                    "buy_threshold": base["buy_threshold"] - 4,
                    "watch_threshold": base["watch_threshold"] - 3,
                    "take_profit_pct": base["take_profit_pct"] + 2,
                    "sentiment_weight": base["sentiment_weight"] + 0.04,
                    "event_weight": base["event_weight"] + 0.04,
                    "risk_weight": base["risk_weight"] - 0.05,
                },
            ),
            normalized_candidate(
                "保守型",
                {
                    "buy_threshold": base["buy_threshold"] + 4,
                    "sell_score_threshold": base["sell_score_threshold"] - 4,
                    "stop_loss_pct": base["stop_loss_pct"] + 1,
                    "risk_weight": base["risk_weight"] + 0.08,
                    "sentiment_weight": base["sentiment_weight"] - 0.03,
                    "event_weight": base["event_weight"] - 0.03,
                },
            ),
            normalized_candidate(
                "短线快进快出",
                {
                    "max_hold_days": max(1, base["max_hold_days"] - 1),
                    "paper_max_hold_days": max(2, base["paper_max_hold_days"] - 2),
                    "take_profit_pct": max(3, base["take_profit_pct"] - 2),
                    "stop_loss_pct": base["stop_loss_pct"] + 1,
                    "technical_weight": base["technical_weight"] + 0.08,
                },
            ),
            normalized_candidate(
                "事件驱动",
                {
                    "event_weight": base["event_weight"] + 0.1,
                    "history_score_weight": base["history_score_weight"] + 0.12,
                    "event_impact_weight": base["event_impact_weight"] - 0.12,
                    "technical_weight": base["technical_weight"] - 0.05,
                },
            ),
            normalized_candidate(
                "风控优先",
                {
                    "risk_weight": base["risk_weight"] + 0.12,
                    "stop_loss_pct": base["stop_loss_pct"] + 1,
                    "sell_score_threshold": base["sell_score_threshold"] - 6,
                    "take_profit_pct": base["take_profit_pct"] - 1,
                },
            ),
        ]

        results = []
        for item in candidates:
            with self.temporary_strategy_params(item["params"]):
                timeline = self.walk_forward(
                    start_date=start_date,
                    end_date=end_date,
                )
            return_pct = safe_float(timeline.get("return_pct"), 0)
            drawdown = abs(safe_float(timeline.get("max_drawdown_pct"), 0))
            win_rate = safe_float(timeline.get("win_rate"), 0)
            closed_trades = safe_float(timeline.get("closed_trades"), 0)
            performance = timeline.get("performance") if isinstance(timeline.get("performance"), dict) else {}
            sharpe_ratio = safe_float(performance.get("sharpe_ratio"), 0)
            profit_factor = safe_float(performance.get("profit_factor"), 0)
            trade_penalty = 10.0 if closed_trades < 5 else 0.0
            objective = (
                return_pct
                - drawdown * 0.75
                + sharpe_ratio * 3.0
                + min(max(profit_factor, 0), 4) * 1.2
                + win_rate * 0.025
                + min(closed_trades, 50) * 0.02
                - trade_penalty
            )
            results.append(
                {
                    "name": item["name"],
                    "objective": round(objective, 4),
                    "return_pct": round(return_pct, 3),
                    "max_drawdown_pct": round(safe_float(timeline.get("max_drawdown_pct"), 0), 3),
                    "sharpe_ratio": round(sharpe_ratio, 4),
                    "profit_factor": round(profit_factor, 4),
                    "win_rate": round(win_rate, 2),
                    "closed_trades": int(closed_trades),
                    "params": item["params"],
                }
            )

        results.sort(key=lambda item: item["objective"], reverse=True)
        best = results[0] if results else {"params": base}
        applied = False
        if apply_best and best.get("params"):
            self.update_strategy_params(
                best["params"],
                source={
                    "type": "fit_strategy",
                    "name": f"参数拟合：{best.get('name', '最佳方案')}",
                    "description": "来自后台参数拟合并复制为系统默认基础参数。",
                    "model_id": "",
                },
            )
            applied = True
            state = self._load_state()
            state["last_fit"] = {
                "as_of": end_date,
                "start_date": start_date,
                "best_name": best.get("name", ""),
                "objective": best.get("objective", 0),
                "return_pct": best.get("return_pct", 0),
                "max_drawdown_pct": best.get("max_drawdown_pct", 0),
                "win_rate": best.get("win_rate", 0),
                "closed_trades": best.get("closed_trades", 0),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            self._save_state(state)
        return {
            "status": "ok",
            "as_of": end_date,
            "start_date": start_date,
            "applied": applied,
            "best": best,
            "candidates": results,
            "strategy_params": self.strategy_params(),
        }

    def daily_plan(
        self,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        limit_days: int = 80,
    ) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        limit_days = max(1, min(int(limit_days or 80), 500))
        inferred_start = False
        if not start_date:
            event_dates = sorted({event.date for event in self.events() if event.date <= as_of})
            if event_dates:
                window = max(limit_days * 3, limit_days + 8)
                start_date = event_dates[max(0, len(event_dates) - window)]
                inferred_start = True
        recommendations = self.recommendations(as_of=as_of, lookback_days=2, top_n=100)
        buy_list = [
            item
            for item in recommendations.get("items", [])
            if item.get("action") == "买入候选"
            and not contains_sample_marker(item)
        ]
        timeline = self.walk_forward(start_date=start_date, end_date=as_of)
        trades = timeline.get("trades", [])
        open_buys: Dict[str, List[Dict[str, Any]]] = {}
        completed = []
        all_buys = []

        for trade in trades:
            code = digits6(trade.get("code"))
            if not code:
                continue
            side = str(trade.get("side") or "").upper()
            if side == "BUY":
                buy = dict(trade)
                buy.setdefault("signal_date", trade.get("date", ""))
                buy["status"] = "持仓中"
                open_buys.setdefault(code, []).append(buy)
                all_buys.append(buy)
            elif side == "SELL":
                queue = open_buys.get(code) or []
                buy = queue.pop(0) if queue else {}
                entry_price = safe_float(buy.get("price"), 0)
                exit_price = safe_float(trade.get("price"), 0)
                pnl_pct = safe_float(trade.get("pnl_pct"), 0)
                if pnl_pct == 0 and entry_price > 0 and exit_price > 0:
                    pnl_pct = (exit_price / entry_price - 1) * 100
                completed.append(
                    {
                        "signal_date": buy.get("signal_date") or trade.get("date", ""),
                        "buy_date": buy.get("date", ""),
                        "sell_date": trade.get("date", ""),
                        "code": code,
                        "name": trade.get("name") or buy.get("name") or self.universe.name(code),
                        "qty": safe_float(buy.get("qty"), trade.get("qty", 0)),
                        "buy_price": round(entry_price, 3),
                        "sell_price": round(exit_price, 3),
                        "buy_score": safe_float(buy.get("score"), 0),
                        "pnl_pct": round(pnl_pct, 3),
                        "status": "已卖出",
                        "buy_reason": buy.get("reason", ""),
                        "sell_reason": trade.get("reason", ""),
                    }
                )

        held_records = []
        for queue in open_buys.values():
            for buy in queue:
                code = digits6(buy.get("code"))
                price_row = self.latest_price(code, as_of=as_of)
                last_price = safe_float((price_row or {}).get("close"), buy.get("price", 0))
                entry_price = safe_float(buy.get("price"), last_price)
                pnl_pct = (last_price / entry_price - 1) * 100 if entry_price > 0 else 0.0
                held_records.append(
                    {
                        "signal_date": buy.get("signal_date") or buy.get("date", ""),
                        "buy_date": buy.get("date", ""),
                        "sell_date": "",
                        "code": code,
                        "name": buy.get("name") or self.universe.name(code),
                        "qty": safe_float(buy.get("qty"), 0),
                        "buy_price": round(entry_price, 3),
                        "sell_price": 0.0,
                        "last_price": round(last_price, 3),
                        "buy_score": safe_float(buy.get("score"), 0),
                        "pnl_pct": round(pnl_pct, 3),
                        "status": "持仓中",
                        "buy_reason": buy.get("reason", ""),
                        "sell_reason": "",
                    }
                )

        missed_records = []
        for day in timeline.get("days", []):
            for miss in day.get("missed", []):
                code = digits6(miss.get("code"))
                if not code:
                    continue
                missed_records.append(
                    {
                        "signal_date": miss.get("signal_date") or day.get("date", ""),
                        "buy_date": "",
                        "sell_date": "",
                        "code": code,
                        "name": miss.get("name") or self.universe.name(code),
                        "qty": 0,
                        "buy_price": 0.0,
                        "sell_price": 0.0,
                        "buy_score": safe_float(miss.get("score"), 0),
                        "pnl_pct": 0.0,
                        "status": "未成交",
                        "unfilled_reason": miss.get("unfilled_reason", ""),
                        "buy_reason": miss.get("reason", ""),
                        "sell_reason": "",
                    }
                )

        outcomes = completed + held_records + missed_records
        outcome_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for item in outcomes:
            outcome_map[(str(item.get("signal_date") or ""), digits6(item.get("code")))] = item

        day_rows = []
        for day in timeline.get("days", []):
            signal_items = []
            for signal in day.get("signals", []):
                code = digits6(signal.get("code"))
                outcome = outcome_map.get((day.get("date", ""), code), {})
                if not outcome:
                    execute_on = str(signal.get("execute_on", "") or "")
                    outcome = {
                        "status": "未成交" if execute_on and execute_on <= as_of else "待执行",
                        "pnl_pct": 0.0,
                        "unfilled_reason": "回放结束前未建仓" if execute_on and execute_on <= as_of else "等待下一交易日执行",
                    }
                signal_items.append(
                    {
                        "signal_date": day.get("date", ""),
                        "execute_on": signal.get("execute_on", ""),
                        "code": code,
                        "name": signal.get("name") or self.universe.name(code),
                        "buy_score": safe_float(signal.get("buy_score"), 0),
                        "sell_score": safe_float(signal.get("sell_score"), 0),
                        "reason": signal.get("reason", ""),
                        "outcome": outcome,
                    }
                )
            if signal_items:
                sold = [item.get("outcome", {}) for item in signal_items if item.get("outcome", {}).get("status") == "已卖出"]
                held = [item.get("outcome", {}) for item in signal_items if item.get("outcome", {}).get("status") == "持仓中"]
                avg_pnl = statistics.mean(safe_float(item.get("pnl_pct"), 0) for item in sold) if sold else 0.0
                day_rows.append(
                    {
                        "date": day.get("date", ""),
                        "signal_count": len(signal_items),
                        "sold_count": len(sold),
                        "holding_count": len(held),
                        "avg_sold_pnl_pct": round(avg_pnl, 3),
                        "signals": signal_items,
                    }
                )

        day_rows = day_rows[-limit_days:]
        current_rules = {
            "buy_threshold": params["buy_threshold"],
            "sell_score_threshold": params["sell_score_threshold"],
            "stop_loss_pct": params["stop_loss_pct"],
            "take_profit_pct": params["take_profit_pct"],
            "max_hold_days": params["max_hold_days"],
            "max_positions": params["max_positions"],
            "top_n": params["top_n"],
        }
        return {
            "status": "ok",
            "as_of": as_of,
            "current_rules": current_rules,
            "buy_list": buy_list,
            "history_days": list(reversed(day_rows)),
            "outcomes": sorted(outcomes, key=lambda item: (item.get("signal_date", ""), item.get("code", "")), reverse=True),
            "timeline_summary": {
                "start_date": timeline.get("start_date", ""),
                "end_date": timeline.get("end_date", ""),
                "start_date_inferred": inferred_start,
                "return_pct": timeline.get("return_pct", 0),
                "closed_trades": timeline.get("closed_trades", 0),
                "win_rate": timeline.get("win_rate", 0),
                "trade_count": len(trades),
            },
        }

    def dashboard(self, as_of: Optional[str] = None, include_heavy: bool = True) -> Dict[str, Any]:
        as_of = as_of or self.latest_event_date()
        params = self.strategy_params()
        recs = self.recommendations(as_of=as_of, lookback_days=2, top_n=30)
        backtest = self.backtest(as_of=as_of, hold_days=int(params["max_hold_days"]), top_n=int(params["top_n"])) if include_heavy else {}
        timeline = self.walk_forward(end_date=as_of) if include_heavy else {}
        portfolio = self.paper_portfolio(as_of=as_of)
        events = self.events()
        lhb_summary = self.lhb_summary(end_date=as_of)
        state = self._load_state()
        kline_codes = {path.stem for path in KLINE_DAY_DIR.glob("*.json")} if KLINE_DAY_DIR.exists() else set()
        for row in self._sqlite_rows("SELECT DISTINCT code FROM market_daily_bars WHERE code IS NOT NULL AND code != ''"):
            clean_code = digits6(row.get("code"))
            if clean_code:
                kline_codes.add(clean_code)
        return {
            "status": "ok",
            "version": "quant-refactor-0.1",
            "as_of": as_of,
            "data": {
                "news_count": len(self.load_news_history()),
                "ai_record_count": len(self.load_analysis_records()),
                "event_count": len(events),
                "stock_count": len(self.universe.code_to_name),
                "kline_stock_count": len(kline_codes),
                "lhb_record_count": lhb_summary.get("rows", 0),
            },
            "recommendations": recs,
            "backtest": backtest,
            "timeline": timeline,
            "portfolio": portfolio,
            "strategy_params": params,
            "last_fit": state.get("last_fit", {}),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }


quant_engine = QuantEngine()
