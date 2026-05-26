from __future__ import annotations

import sqlite3


STRATEGY_EVOLUTION_SCHEMA_VERSION = 2026052501

STRATEGY_EVOLUTION_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT,
    started_at TEXT,
    finished_at TEXT,
    duration_ms REAL,
    generations INTEGER,
    population_size INTEGER,
    start_date TEXT,
    end_date TEXT,
    applied INTEGER,
    objective REAL,
    return_pct REAL,
    max_drawdown_pct REAL,
    win_rate REAL,
    closed_trades INTEGER,
    best_params_json TEXT,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_runs_finished ON strategy_runs(finished_at);

CREATE TABLE IF NOT EXISTS strategy_model_metrics (
    metric_id TEXT PRIMARY KEY,
    run_id TEXT,
    generation INTEGER,
    best_objective REAL,
    best_return_pct REAL,
    best_drawdown_pct REAL,
    best_win_rate REAL,
    population INTEGER,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_metrics_run ON strategy_model_metrics(run_id);

CREATE TABLE IF NOT EXISTS strategy_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT,
    generation INTEGER,
    rank INTEGER,
    selected INTEGER,
    selection_role TEXT,
    elimination_reason TEXT,
    objective REAL,
    return_pct REAL,
    max_drawdown_pct REAL,
    sharpe_ratio REAL,
    profit_factor REAL,
    win_rate REAL,
    closed_trades INTEGER,
    params_hash TEXT,
    params_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_candidates_run_generation ON strategy_candidates(run_id, generation, rank);
CREATE INDEX IF NOT EXISTS idx_strategy_candidates_selected ON strategy_candidates(run_id, selected);

CREATE TABLE IF NOT EXISTS strategy_models (
    model_id TEXT PRIMARY KEY,
    run_id TEXT,
    generated_at TEXT,
    rank INTEGER,
    name TEXT,
    source TEXT,
    reusable INTEGER,
    objective REAL,
    return_pct REAL,
    max_drawdown_pct REAL,
    sharpe_ratio REAL,
    profit_factor REAL,
    win_rate REAL,
    closed_trades INTEGER,
    params_json TEXT NOT NULL DEFAULT '{}',
    backtest_json TEXT NOT NULL DEFAULT '{}',
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_models_run ON strategy_models(run_id, rank);
CREATE INDEX IF NOT EXISTS idx_strategy_models_generated ON strategy_models(generated_at);

CREATE TABLE IF NOT EXISTS strategy_model_records (
    record_id TEXT PRIMARY KEY,
    model_id TEXT,
    run_id TEXT,
    record_type TEXT,
    seq INTEGER,
    date TEXT,
    time TEXT,
    side TEXT,
    code TEXT,
    name TEXT,
    qty REAL,
    price REAL,
    pnl_pct REAL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_records_model ON strategy_model_records(model_id, record_type);
CREATE INDEX IF NOT EXISTS idx_strategy_records_code_date ON strategy_model_records(code, date);

CREATE TABLE IF NOT EXISTS strategy_runtime_snapshots (
    cache_key TEXT PRIMARY KEY,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    start_date TEXT,
    as_of TEXT,
    initial_cash REAL,
    record_limit INTEGER,
    source TEXT,
    generated_at TEXT,
    total_asset REAL,
    return_pct REAL,
    position_count INTEGER,
    deal_count INTEGER,
    account_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_model_date ON strategy_runtime_snapshots(model_id, as_of, start_date);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_generated ON strategy_runtime_snapshots(generated_at);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_scope ON strategy_runtime_snapshots(model_id, params_hash, source, generated_at, as_of);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_source ON strategy_runtime_snapshots(model_id, source, generated_at, as_of);

CREATE TABLE IF NOT EXISTS user_follow_periods (
    period_id TEXT PRIMARY KEY,
    username TEXT,
    model_id TEXT,
    simulated_cash REAL,
    started_at TEXT,
    start_date TEXT,
    ended_at TEXT,
    end_date TEXT,
    reason TEXT,
    source TEXT,
    created_at TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_user_follow_periods_user_started ON user_follow_periods(username, started_at);
CREATE INDEX IF NOT EXISTS idx_user_follow_periods_active ON user_follow_periods(username, ended_at);
CREATE INDEX IF NOT EXISTS idx_user_follow_periods_current ON user_follow_periods(username, ended_at, started_at, created_at);

CREATE TABLE IF NOT EXISTS user_follow_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    username TEXT,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    follow_start_date TEXT,
    as_of TEXT,
    initial_cash REAL,
    record_limit INTEGER,
    source TEXT,
    generated_at TEXT,
    total_asset REAL,
    return_pct REAL,
    position_count INTEGER,
    deal_count INTEGER,
    account_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_user_follow_snapshots_user_model ON user_follow_snapshots(username, model_id, follow_start_date, as_of);
CREATE INDEX IF NOT EXISTS idx_user_follow_snapshots_generated ON user_follow_snapshots(generated_at);
CREATE INDEX IF NOT EXISTS idx_user_follow_snapshots_profile ON user_follow_snapshots(username, model_id, follow_start_date, initial_cash, as_of, generated_at);

CREATE TABLE IF NOT EXISTS user_follow_positions (
    position_id TEXT PRIMARY KEY,
    snapshot_id TEXT,
    username TEXT,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    follow_start_date TEXT,
    as_of TEXT,
    code TEXT,
    name TEXT,
    qty REAL,
    available_qty REAL,
    entry_date TEXT,
    entry_price REAL,
    last_price REAL,
    market_value REAL,
    pnl_pct REAL,
    source TEXT,
    generated_at TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_user_follow_positions_user_date ON user_follow_positions(username, as_of);
CREATE INDEX IF NOT EXISTS idx_user_follow_positions_code_date ON user_follow_positions(code, as_of);
CREATE INDEX IF NOT EXISTS idx_user_follow_positions_snapshot ON user_follow_positions(snapshot_id, market_value, code);

CREATE TABLE IF NOT EXISTS user_follow_trades (
    trade_id TEXT PRIMARY KEY,
    snapshot_id TEXT,
    username TEXT,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    follow_start_date TEXT,
    date TEXT,
    time TEXT,
    side TEXT,
    code TEXT,
    name TEXT,
    qty REAL,
    price REAL,
    amount REAL,
    pnl_pct REAL,
    source TEXT,
    generated_at TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_user_follow_trades_user_date ON user_follow_trades(username, date);
CREATE INDEX IF NOT EXISTS idx_user_follow_trades_code_date ON user_follow_trades(code, date);
CREATE INDEX IF NOT EXISTS idx_user_follow_trades_snapshot ON user_follow_trades(snapshot_id, date, time, trade_id);

CREATE TABLE IF NOT EXISTS strategy_daily_signals (
    signal_id TEXT PRIMARY KEY,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    start_date TEXT,
    date TEXT,
    execute_on TEXT,
    mode TEXT,
    code TEXT,
    name TEXT,
    action TEXT,
    buy_score REAL,
    sell_score REAL,
    reason TEXT,
    source TEXT,
    generated_at TEXT,
    initial_cash REAL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_model_date ON strategy_daily_signals(model_id, date);
CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_code_date ON strategy_daily_signals(code, date);
CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_generated ON strategy_daily_signals(generated_at);
CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_runtime ON strategy_daily_signals(model_id, params_hash, generated_at, date);
CREATE INDEX IF NOT EXISTS idx_strategy_daily_signals_feed ON strategy_daily_signals(date, model_id, generated_at, buy_score, sell_score);

CREATE TABLE IF NOT EXISTS strategy_runtime_trades (
    trade_id TEXT PRIMARY KEY,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    start_date TEXT,
    date TEXT,
    time TEXT,
    mode TEXT,
    side TEXT,
    code TEXT,
    name TEXT,
    qty REAL,
    price REAL,
    amount REAL,
    score REAL,
    pnl_pct REAL,
    reason TEXT,
    source TEXT,
    generated_at TEXT,
    initial_cash REAL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_trades_model_date ON strategy_runtime_trades(model_id, date);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_trades_code_date ON strategy_runtime_trades(code, date);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_trades_generated ON strategy_runtime_trades(generated_at);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_trades_runtime ON strategy_runtime_trades(model_id, params_hash, generated_at, date, time);

CREATE TABLE IF NOT EXISTS strategy_runtime_positions (
    position_id TEXT PRIMARY KEY,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    start_date TEXT,
    as_of TEXT,
    mode TEXT,
    code TEXT,
    name TEXT,
    qty REAL,
    entry_date TEXT,
    entry_price REAL,
    last_price REAL,
    market_value REAL,
    pnl_pct REAL,
    source TEXT,
    generated_at TEXT,
    initial_cash REAL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_positions_model_date ON strategy_runtime_positions(model_id, as_of);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_positions_code_date ON strategy_runtime_positions(code, as_of);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_positions_generated ON strategy_runtime_positions(generated_at);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_positions_runtime ON strategy_runtime_positions(model_id, params_hash, generated_at, as_of);

CREATE TABLE IF NOT EXISTS strategy_runtime_settlements (
    settlement_id TEXT PRIMARY KEY,
    model_id TEXT,
    model_version TEXT,
    params_hash TEXT,
    start_date TEXT,
    date TEXT,
    mode TEXT,
    buy_amount REAL,
    sell_amount REAL,
    commission REAL,
    stamp_duty REAL,
    transfer_fee REAL,
    total_fee REAL,
    net_amount REAL,
    realized_pnl REAL,
    deal_count INTEGER,
    source TEXT,
    generated_at TEXT,
    initial_cash REAL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_settlements_model_date ON strategy_runtime_settlements(model_id, date);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_settlements_generated ON strategy_runtime_settlements(generated_at);
CREATE INDEX IF NOT EXISTS idx_strategy_runtime_settlements_runtime ON strategy_runtime_settlements(model_id, params_hash, generated_at, date);
"""


def ensure_strategy_evolution_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(STRATEGY_EVOLUTION_SCHEMA_SQL)
