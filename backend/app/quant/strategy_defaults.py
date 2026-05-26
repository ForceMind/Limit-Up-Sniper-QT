from __future__ import annotations


DEFAULT_AI_MODEL = "deepseek-v4-flash"

DEFAULT_BROKER_FEE_PARAMS = {
    "commission_rate": 0.00025,
    "min_commission": 5.0,
    "stamp_duty_rate": 0.0005,
    "transfer_fee_rate": 0.00001,
}

DEFAULT_STRATEGY_PARAMS = {
    "sentiment_weight": 0.35,
    "event_weight": 0.25,
    "technical_weight": 0.25,
    "risk_weight": 0.15,
    "buy_threshold": 72.0,
    "watch_threshold": 60.0,
    "avoid_sell_threshold": 70.0,
    "avoid_buy_ceiling": 65.0,
    "sell_score_threshold": 72.0,
    "stop_loss_pct": -5.0,
    "take_profit_pct": 8.0,
    "max_hold_days": 3.0,
    "max_positions": 5.0,
    "top_n": 5.0,
    "paper_max_hold_days": 6.0,
    "account_initial_cash": 200000.0,
    "paper_position_value": 30000.0,
    "sentiment_coef": 32.0,
    "ai_score_coef": 5.0,
    "event_impact_weight": 0.62,
    "history_score_weight": 0.38,
    "history_return_coef": 420.0,
    "history_win_coef": 45.0,
    "sell_negative_sentiment_coef": 22.0,
    "sell_technical_risk_coef": 0.55,
    "negative_sentiment_risk_penalty": 15.0,
    "risk_event_penalty": 20.0,
    "factor_score_coef": 0.28,
    "factor_momentum_weight": 0.35,
    "factor_volume_weight": 0.20,
    "factor_breakout_weight": 0.20,
    "factor_lhb_weight": 0.25,
}
