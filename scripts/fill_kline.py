from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.quant.engine import quant_engine


def main() -> int:
    parser = argparse.ArgumentParser(description="补齐有新闻事件股票的日K数据")
    parser.add_argument("--start-date", default="2026-03-01", help="开始日期，默认 2026-03-01")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"), help="结束日期，默认今天")
    parser.add_argument("--max-codes", type=int, default=300, help="最多补齐股票数量")
    parser.add_argument("--force", action="store_true", help="强制重新拉取")
    args = parser.parse_args()
    result = quant_engine.ensure_daily_kline_for_events(
        start_date=args.start_date,
        end_date=args.end_date,
        max_codes=args.max_codes,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
