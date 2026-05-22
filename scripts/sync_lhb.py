from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.quant.lhb_sync import sync_lhb


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取东方财富龙虎榜席位数据")
    parser.add_argument("--start-date", default="2026-03-01", help="开始日期，默认 2026-03-01")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y-%m-%d"), help="结束日期，默认今天")
    parser.add_argument("--max-stock-days", type=int, default=300, help="最多拉取股票-日期组合数量")
    parser.add_argument("--force", action="store_true", help="强制重新拉取已有日期")
    args = parser.parse_args()
    result = sync_lhb(
        start_date=args.start_date,
        end_date=args.end_date,
        max_stock_days=args.max_stock_days,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
