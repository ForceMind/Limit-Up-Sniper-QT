from __future__ import annotations

import os
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]


def configured_data_dir() -> Path:
    raw = str(os.getenv("QUANT_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return BACKEND_DIR / "data"


DATA_DIR = configured_data_dir()
QUANT_DB_FILE = DATA_DIR / "quant_data.sqlite3"
KLINE_DAY_DIR = DATA_DIR / "kline_day_cache"
KLINE_MIN_DIR = DATA_DIR / "kline_cache"
STATE_FILE = DATA_DIR / "quant_state.json"
EVENTS_CACHE_FILE = DATA_DIR / "quant_events_cache.json"
LHB_HISTORY_FILE = DATA_DIR / "lhb_history.csv"
