from __future__ import annotations

from typing import Any, Callable, Dict, Optional


class QuantBasicService:
    def __init__(
        self,
        *,
        quant_engine: Any,
        trade_notifier: Any,
        safe_news_feed: Callable[..., Dict[str, Any]],
    ) -> None:
        self._quant_engine = quant_engine
        self._trade_notifier = trade_notifier
        self._safe_news_feed = safe_news_feed

    def dashboard_payload(self, as_of: Optional[str] = None, light: bool = False) -> Dict[str, Any]:
        return self._quant_engine.dashboard(as_of=as_of, include_heavy=not light)

    def recommendations_payload(
        self,
        as_of: Optional[str] = None,
        lookback_days: int = 2,
        top_n: int = 30,
    ) -> Dict[str, Any]:
        return self._quant_engine.recommendations(as_of=as_of, lookback_days=lookback_days, top_n=top_n)

    def daily_plan_payload(
        self,
        as_of: Optional[str] = None,
        start_date: Optional[str] = None,
        limit_days: int = 80,
    ) -> Dict[str, Any]:
        return self._quant_engine.daily_plan(as_of=as_of, start_date=start_date, limit_days=limit_days)

    def strategy_params_payload(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "strategy_params": self._quant_engine.strategy_params(),
            "strategy_source": self._quant_engine.strategy_source(),
            "model_weights": self._quant_engine.model_weights(),
        }

    def update_strategy_params_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._quant_engine.update_strategy_params(payload)

    def reset_strategy_params_payload(self) -> Dict[str, Any]:
        return self._quant_engine.reset_strategy_params()

    def events_payload(self, as_of: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        events = self._quant_engine.events()
        if as_of:
            events = [event for event in events if event.date <= as_of]
        return {"items": [event.compact() for event in events[:limit]], "count": len(events)}

    def news_payload(
        self,
        as_of: Optional[str] = None,
        limit: int = 120,
        fallback_latest: bool = True,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        code: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._safe_news_feed(
            as_of=as_of,
            limit=limit,
            fallback_latest=fallback_latest,
            source=source,
            keyword=keyword,
            code=code,
        )

    def correlation_payload(self, as_of: Optional[str] = None, hold_days: int = 3) -> Dict[str, Any]:
        return self._quant_engine.correlation(as_of=as_of, hold_days=hold_days)

    def portfolio_payload(self, as_of: Optional[str] = None) -> Dict[str, Any]:
        return self._quant_engine.paper_portfolio(as_of=as_of)

    def trading_account_payload(self, as_of: Optional[str] = None, limit: int = 500) -> Dict[str, Any]:
        return self._quant_engine.trading_account(as_of=as_of, limit=limit)

    def run_payload(self, as_of: Optional[str] = None, calibrate: bool = True) -> Dict[str, Any]:
        calibration = self._quant_engine.calibrate_model(as_of=as_of) if calibrate else None
        portfolio = self._quant_engine.run_paper_trading(as_of=as_of)
        notification = self._trade_notifier.notify_trade_events(
            portfolio.get("trades", []) if isinstance(portfolio.get("trades"), list) else [],
            as_of=portfolio["as_of"],
            source="manual_quant_run",
        )
        recommendations = self._quant_engine.recommendations(as_of=portfolio["as_of"], lookback_days=2, top_n=30)
        return {
            "status": "ok",
            "as_of": portfolio["as_of"],
            "calibration": calibration,
            "portfolio": portfolio,
            "notification": notification,
            "recommendations": recommendations,
        }

    def news_history_payload(self, limit: int = 200) -> Dict[str, Any]:
        items = self._quant_engine.load_news_history()[:limit]
        return {"items": items, "count": len(items)}
