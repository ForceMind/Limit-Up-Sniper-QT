from __future__ import annotations

import argparse
import os
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "data" / "quant_data.sqlite3"
DEFAULT_OUT_DIR = PROJECT_ROOT / "backups"
RUNTIME_TABLES = (
    "strategy_daily_signals",
    "strategy_runtime_positions",
    "strategy_runtime_trades",
    "strategy_runtime_snapshots",
    "strategy_runtime_settlements",
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _default_output() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUT_DIR / f"qt_strategy_runtime_export_{stamp}.tar.gz"


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})")]


def package_runtime(db_path: Path, output: Path, model_prefix: str) -> tuple[Path, dict[str, int]]:
    db_path = db_path.resolve()
    output = output.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    fd, temp_name = tempfile.mkstemp(prefix="qt_strategy_runtime_", suffix=".sqlite3")
    os.close(fd)
    temp_db = Path(temp_name)
    try:
        target = sqlite3.connect(temp_db)
        source = sqlite3.connect(db_path)
        try:
            target.execute("ATTACH DATABASE ? AS source", (str(db_path),))
            for table in RUNTIME_TABLES:
                table_q = _quote_identifier(table)
                ddl = source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
                if not ddl or not ddl[0]:
                    continue
                target.execute(str(ddl[0]))
                columns = _table_columns(source, table)
                if not columns:
                    continue
                cols_sql = ", ".join(_quote_identifier(col) for col in columns)
                has_model_id = "model_id" in columns
                if model_prefix and has_model_id:
                    target.execute(
                        f"INSERT INTO {table_q} ({cols_sql}) SELECT {cols_sql} FROM source.{table_q} WHERE model_id LIKE ?",
                        (f"{model_prefix}%",),
                    )
                else:
                    target.execute(f"INSERT INTO {table_q} ({cols_sql}) SELECT {cols_sql} FROM source.{table_q}")
                counts[table] = int(target.execute(f"SELECT COUNT(*) FROM {table_q}").fetchone()[0] or 0)
            target.commit()
            target.execute("DETACH DATABASE source")
        finally:
            source.close()
            target.close()
        with tarfile.open(output, "w:gz") as archive:
            archive.add(temp_db, arcname="backend/data/quant_data.sqlite3")
        return output, counts
    finally:
        temp_db.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package only strategy runtime SQLite tables for server merge.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Source SQLite database.")
    parser.add_argument("--out", default=str(_default_output()), help="Output .tar.gz file.")
    parser.add_argument("--model-prefix", default="capital_", help="Only export model_id starting with this prefix. Empty means all runtime rows.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output, counts = package_runtime(Path(args.db), Path(args.out), str(args.model_prefix or ""))
    print(f"策略运行数据包: {output}")
    print(f"文件数量: 1")
    print(f"表记录: {counts}")
    print("未打包: 新闻、行情、K线 JSON、.env、账号、会话、密钥")


if __name__ == "__main__":
    main()
