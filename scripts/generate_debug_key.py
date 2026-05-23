#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import secrets


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a temporary QT debug API key.")
    parser.add_argument("--raw", action="store_true", help="Print only the raw key.")
    args = parser.parse_args()

    key = "qt_dbg_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    if args.raw:
        print(key)
        return 0

    print("临时调试密钥已生成。只在服务器 .env 中保存 SHA256，不要把原始密钥提交到 Git。")
    print()
    print("原始密钥（只给调试请求使用）：")
    print(key)
    print()
    print("写入服务器 .env：")
    print("QT_DEBUG_API_ENABLED=true")
    print(f"QT_DEBUG_API_KEY_SHA256={digest}")
    print("QT_DEBUG_API_ALLOW_WRITE=false")
    print("QT_DEBUG_API_SUBJECT=codex-debug")
    print()
    print("关闭调试通道时，把 QT_DEBUG_API_ENABLED 改成 false，然后重启服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
