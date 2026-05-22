from __future__ import annotations

import argparse
import tarfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "backend" / "data"
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "data" / "quant_data.sqlite3"
DEFAULT_OUT_DIR = PROJECT_ROOT / "backups"

SAFE_FILES = {
    "news_history.json",
    "news_analysis_records.json",
    "quant_events_cache.json",
    "ai_cache.json",
    "ai_usage_logs.jsonl",
    "lhb_history.csv",
    "biying_all_market_cache.json",
    "biying_pool_cache.json",
    "biying_stock_list.json",
    "market_pools.json",
    "market_sentiment_cache.json",
    "watchlist.json",
    "news_fetch_state.json",
    "ai_analysis_state.json",
    "biying_intraday_sync_state.json",
    "quant_job_state.json",
    "quant_state.json",
    "strategy_evolution_state.json",
    "trade_notification_state.json",
}

SAFE_DIRS = {
    "kline_day_cache",
    "kline_cache",
}

OPTIONAL_LOG_FILES = {
    "access_logs.json",
    "quant_runtime_logs.jsonl",
    "runtime_logs.jsonl",
    "trade_notification_logs.jsonl",
}

BLOCKED_NAMES = {
    ".env",
    "auth.json",
    "admin_credentials.json",
    "admin_sessions.json",
    "config.json",
    "ws_token_secret.txt",
    "commercial.db",
    "biying_quota.sqlite3",
}


def _safe_members(source_dir: Path, include_logs: bool) -> list[tuple[Path, str]]:
    members: list[tuple[Path, str]] = []
    allowed_files = set(SAFE_FILES)
    if include_logs:
        allowed_files.update(OPTIONAL_LOG_FILES)
    for name in sorted(allowed_files):
        if name in BLOCKED_NAMES:
            continue
        path = source_dir / name
        if path.is_file():
            members.append((path, f"backend/data/{name}"))
    for dirname in sorted(SAFE_DIRS):
        root = source_dir / dirname
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name not in BLOCKED_NAMES:
                members.append((path, f"backend/data/{dirname}/{path.relative_to(root).as_posix()}"))
    return members


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUT_DIR / f"qt_safe_data_export_{stamp}.tar.gz"


def package(source_dir: Path, db_path: Path, output: Path, include_logs: bool) -> tuple[Path, int]:
    source_dir = source_dir.resolve()
    db_path = db_path.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    members = _safe_members(source_dir, include_logs)
    if db_path.is_file():
        members.append((db_path, "backend/data/quant_data.sqlite3"))
    with tarfile.open(output, "w:gz") as archive:
        for path, arcname in members:
            archive.add(path, arcname=arcname)
    return output, len(members)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package allowed quant runtime data for server upload.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_DIR), help="Source backend/data directory.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database to include.")
    parser.add_argument("--out", default=str(_default_output()), help="Output .tar.gz file.")
    parser.add_argument("--include-logs", action="store_true", help="Also include raw access/runtime log files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output, count = package(Path(args.source), Path(args.db), Path(args.out), bool(args.include_logs))
    print(f"安全数据包: {output}")
    print(f"文件数量: {count}")
    print("未打包: config.json, auth.json, admin_credentials.json, admin_sessions.json, ws_token_secret.txt, .env")


if __name__ == "__main__":
    main()
