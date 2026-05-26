from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional


SafeFloat = Callable[[Any, float], float]


class LightDashboardReadService:
    def __init__(
        self,
        *,
        data_dir: Callable[[], Path],
        safe_float: SafeFloat,
        stock_count: Callable[[], int],
        strategy_params: Callable[[], Dict[str, Any]],
        strategy_source: Callable[[], Dict[str, Any]],
        now: Callable[[], datetime],
    ) -> None:
        self._data_dir = data_dir
        self._safe_float = safe_float
        self._stock_count = stock_count
        self._strategy_params = strategy_params
        self._strategy_source = strategy_source
        self._now = now

    def scalar(self, sql: str, params: Optional[list[Any]] = None) -> Any:
        db_path = self._data_dir() / "quant_data.sqlite3"
        if not db_path.exists():
            return None
        try:
            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(sql, params or []).fetchone()
            finally:
                conn.close()
        except Exception:
            return None
        return row[0] if row else None

    def count(self, table: str) -> int:
        if not table.replace("_", "").isalnum():
            return 0
        value = self.scalar(f"SELECT COUNT(*) FROM {table}")
        return int(self._safe_float(value, 0))

    def light_dashboard_payload(
        self,
        as_of: Optional[str],
        news_payload: Optional[Dict[str, Any]] = None,
        model_signals: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        signal_items: list[Dict[str, Any]] = []
        groups = model_signals.get("items") if isinstance(model_signals, dict) and isinstance(model_signals.get("items"), list) else []
        for group in groups:
            if not isinstance(group, dict):
                continue
            signals = group.get("signals") if isinstance(group.get("signals"), list) else []
            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                signal_items.append(
                    {
                        **signal,
                        "model_id": group.get("model_id"),
                        "model_name": group.get("model_name"),
                        "action": signal.get("action") or "买入候选",
                    }
                )
        signal_items.sort(key=lambda item: self._safe_float(item.get("buy_score"), 0), reverse=True)

        news_items = news_payload.get("items") if isinstance(news_payload, dict) and isinstance(news_payload.get("items"), list) else []
        news_events = news_payload.get("events") if isinstance(news_payload, dict) and isinstance(news_payload.get("events"), list) else []
        kline_stock_count = self.scalar("SELECT COUNT(DISTINCT code) FROM market_daily_bars WHERE code IS NOT NULL AND code != ''")
        return {
            "status": "ok",
            "as_of": str(as_of or (model_signals or {}).get("data_date") or ""),
            "data": {
                "news_count": self.count("news_raw") or len(news_items),
                "ai_record_count": self.count("news_analysis"),
                "event_count": self.count("news_events") or len(news_events),
                "stock_count": self._stock_count(),
                "kline_stock_count": int(self._safe_float(kline_stock_count, 0)),
                "lhb_record_count": self.count("lhb_records"),
            },
            "recommendations": {
                "status": "ok",
                "as_of": (model_signals or {}).get("data_date") or as_of,
                "items": signal_items[:30],
                "latest_events": news_events[:60],
                "source": "strategy_daily_signals",
            },
            "timeline": {},
            "portfolio": {},
            "strategy_params": self._strategy_params(),
            "strategy_source": self._strategy_source(),
            "generated_at": self._now().isoformat(timespec="seconds"),
            "light": True,
        }
