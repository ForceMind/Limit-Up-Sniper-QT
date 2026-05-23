import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.quant.engine import QuantEngine
from app.quant.monitoring import ai_usage_summary, data_coverage


def test_quant_engine_builds_events_and_recommendations():
    engine = QuantEngine()

    events = engine.events()
    assert events

    recommendations = engine.recommendations(top_n=5)
    assert recommendations["items"]
    first = recommendations["items"][0]
    assert first["code"]
    assert first["buy_score"] >= 0
    assert first["sell_score"] >= 0
    assert first["agents"]


def test_quant_backtest_returns_metrics():
    engine = QuantEngine()

    result = engine.backtest(hold_days=3, top_n=5)
    assert result["trades"] >= 0
    assert "score_buckets" in result
    assert "58-65" in result["score_buckets"]
    assert "performance" in result
    assert "sharpe_ratio" in result["performance"]
    assert "data_diagnostics" in result
    assert result["data_diagnostics"]["event_count"] >= 0


def test_quant_walk_forward_is_chronological():
    engine = QuantEngine()

    result = engine.walk_forward(hold_days=3, top_n=3)
    dates = [day["date"] for day in result["days"]]
    assert dates == sorted(dates)
    assert result["final_value"] >= 0
    assert "performance" in result
    assert "profit_factor" in result["performance"]
    for trade in result["trades"]:
        if trade["side"] == "BUY":
            assert trade["signal_date"] < trade["date"]


def test_intraday_walk_forward_uses_minute_bars_when_available():
    engine = QuantEngine()

    result = engine.walk_forward_intraday(start_date="2026-05-19", end_date="2026-05-19", top_n=20)

    assert result["mode"] == "intraday_5m"
    assert "2026-05-19" in result["intraday_available_dates"]
    assert result["intraday_trade_count"] > 0
    assert result["fallback_trade_count"] == 0
    buy_trades = [trade for trade in result["trades"] if trade["side"] == "BUY"]
    assert buy_trades
    assert all(trade["mode"] == "intraday_5m" for trade in buy_trades)
    assert all(trade["time"].startswith("2026-05-19 ") for trade in buy_trades)


def test_daily_plan_exposes_current_buys_and_history_outcomes():
    engine = QuantEngine()

    result = engine.daily_plan(as_of="2026-05-19", limit_days=20)

    assert result["status"] == "ok"
    assert "buy_list" in result
    assert "history_days" in result
    assert "timeline_summary" in result
    assert result["current_rules"]["buy_threshold"] > 0
    if result["history_days"]:
        day = result["history_days"][0]
        assert "signals" in day


def test_trading_account_exposes_positions_deals_and_delivery_records():
    engine = QuantEngine()

    result = engine.trading_account(as_of="2026-05-19")

    assert result["status"] == "ok"
    assert "account" in result
    assert "positions" in result
    assert "history_deals" in result
    assert "delivery_records" in result
    assert "daily_settlements" in result
    if result["history_deals"]:
        deal = result["history_deals"][0]
        assert "commission" in deal
        assert "net_amount" in deal


def test_account_from_trades_can_start_from_follow_date_without_old_positions():
    engine = QuantEngine()
    trades = [
        {"date": "2026-03-01", "time": "09:30:00", "side": "BUY", "code": "600000", "name": "浦发银行", "qty": 100, "price": 10},
        {"date": "2026-03-05", "time": "14:55:00", "side": "SELL", "code": "600000", "name": "浦发银行", "qty": 100, "price": 12},
        {"date": "2026-03-06", "time": "09:30:00", "side": "BUY", "code": "600003", "name": "东北高速", "qty": 100, "price": 8},
        {"date": "2026-03-08", "time": "14:55:00", "side": "SELL", "code": "600003", "name": "东北高速", "qty": 100, "price": 9},
    ]

    result = engine.account_from_trades(
        trades,
        initial_cash=100_000,
        as_of="2026-03-08",
        start_date="2026-03-04",
        drop_unmatched_sells=True,
    )

    assert result["start_date"] == "2026-03-04"
    assert result["account"]["deal_count"] == 2
    assert {deal["code"] for deal in result["history_deals"]} == {"600003"}
    assert result["positions"] == []


def test_monitoring_exposes_data_coverage_and_ai_usage():
    coverage = data_coverage(as_of="2026-05-19", top_n=20)

    assert coverage["status"] == "ok"
    assert coverage["summary"]["target_count"] >= 0
    assert "daily_coverage" in coverage
    assert "minute_coverage" in coverage
    assert "targets" in coverage

    usage = ai_usage_summary()
    assert usage["status"] == "ok"
    assert "by_model" in usage


def test_news_feed_supports_source_and_keyword_filters():
    engine = QuantEngine()

    by_source = engine.news_feed(as_of="2026-05-19", source="Fixture")
    assert by_source["status"] == "ok"
    assert by_source["items"]
    assert all(item["source"] == "Fixture" for item in by_source["items"])

    by_keyword = engine.news_feed(as_of="2026-05-19", keyword="液冷")
    assert by_keyword["items"]
    assert all("液冷" in item["text"] for item in by_keyword["items"])

    no_match = engine.news_feed(as_of="2026-05-19", keyword="不存在的筛选词", fallback_latest=False)
    assert no_match["items"] == []
    assert no_match["has_requested_date_data"] is False
