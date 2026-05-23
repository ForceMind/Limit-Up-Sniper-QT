#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import secrets


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"


def _read_env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_bool(values: dict[str, str], name: str) -> bool:
    return str(values.get(name, "")).strip().lower() not in {"", "0", "false", "no", "off"}


def _set_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        name = line.split("=", 1)[0].strip()
        if name in updates:
            next_lines.append(f"{name}={updates[name]}")
            seen.add(name)
        else:
            next_lines.append(line)
    if next_lines and next_lines[-1].strip():
        next_lines.append("")
    for name, value in updates.items():
        if name not in seen:
            next_lines.append(f"{name}={value}")
    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _disable() -> int:
    _set_env_values(
        ENV_PATH,
        {
            "QT_DEBUG_API_ENABLED": "false",
            "QT_DEBUG_API_KEY": "",
            "QT_DEBUG_API_KEY_SHA256": "",
            "QT_DEBUG_API_ALLOW_WRITE": "false",
        },
    )
    print(f"已关闭临时调试通道：{ENV_PATH}")
    print("请执行 qt restart 让配置生效。")
    return 0


def _status() -> int:
    values = _read_env_values(ENV_PATH)
    enabled = _env_bool(values, "QT_DEBUG_API_ENABLED")
    hash_key = values.get("QT_DEBUG_API_KEY_SHA256", "")
    raw_key = values.get("QT_DEBUG_API_KEY", "")
    write_allowed = _env_bool(values, "QT_DEBUG_API_ALLOW_WRITE")
    subject = values.get("QT_DEBUG_API_SUBJECT", "codex-debug") or "codex-debug"
    print("临时调试通道状态")
    print(f".env 文件：{ENV_PATH}")
    print(f"开关：{'已开启' if enabled and (hash_key or raw_key) else '已关闭'}")
    print(f"密钥配置：{'已配置 SHA256' if hash_key else ('已配置原始密钥' if raw_key else '未配置')}")
    print(f"写接口：{'允许' if write_allowed else '禁止'}")
    print(f"请求头：X-QT-Debug-Key")
    print(f"审计身份：{subject}")
    if enabled and (hash_key or raw_key):
        print("关闭命令：qt debug-off && qt restart")
    else:
        print("开启命令：qt debug-on && qt restart")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a temporary QT debug API key.")
    parser.add_argument("--raw", action="store_true", help="Print only the raw key.")
    parser.add_argument("--write-env", action="store_true", help="Write enabled hash config into .env.")
    parser.add_argument("--disable", action="store_true", help="Disable debug API in .env.")
    parser.add_argument("--status", action="store_true", help="Print debug API status from .env.")
    args = parser.parse_args()

    if args.status:
        return _status()
    if args.disable:
        return _disable()

    key = "qt_dbg_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    if args.raw:
        print(key)
        return 0

    if args.write_env:
        _set_env_values(
            ENV_PATH,
            {
                "QT_DEBUG_API_ENABLED": "true",
                "QT_DEBUG_API_KEY": "",
                "QT_DEBUG_API_KEY_SHA256": digest,
                "QT_DEBUG_API_ALLOW_WRITE": "false",
                "QT_DEBUG_API_SUBJECT": "codex-debug",
            },
        )

    print("临时调试密钥已生成。只在服务器 .env 中保存 SHA256，不要把原始密钥提交到 Git。")
    if args.write_env:
        print(f"已写入服务器 .env：{ENV_PATH}")
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
    print("关闭调试通道时，执行 qt debug-off，然后执行 qt restart。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
