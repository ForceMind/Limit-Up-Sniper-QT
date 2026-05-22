from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.quant.data_transfer import clear_sample_quant_state  # noqa: E402
from app.quant.engine import DATA_DIR  # noqa: E402


def main() -> int:
    result = clear_sample_quant_state(DATA_DIR)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("cleared"):
        print("已清理样例持仓。请刷新后台页面；如果服务仍显示旧数据，请执行 qt restart。")
    else:
        print(result.get("message") or "未发现需要清理的样例持仓。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
