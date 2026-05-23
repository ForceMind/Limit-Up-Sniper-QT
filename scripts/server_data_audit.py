#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "backend" / "data"
SENSITIVE_NAMES = {
    ".env",
    "config.json",
    "auth.json",
    "admin_credentials.json",
    "admin_sessions.json",
    "ws_token_secret.txt",
}
HIGH_RISK_JSON_KEYS = {
    "password_plain",
    "plain_password",
}
RUNTIME_DATA_PATTERNS = (
    "quant_data.sqlite3",
    "quant_*.json",
    "*.jsonl",
    "*.sqlite3",
    "*.db",
    "*.tar.gz",
    "*.zip",
)


def human_size(value: int) -> str:
    n = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{int(value)} B"


def git_tracked_files() -> set[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    except Exception:
        return set()
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except Exception:
        return str(path)


def mode_text(path: Path) -> str:
    try:
        return oct(stat.S_IMODE(path.stat().st_mode))
    except OSError:
        return "-"


def is_group_or_world_readable(path: Path) -> bool:
    if os.name == "nt":
        return False
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return False
    return bool(mode & (stat.S_IRGRP | stat.S_IROTH))


def sqlite_summary(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False}
    out: dict[str, Any] = {"exists": True, "size_bytes": db_path.stat().st_size, "tables": {}}
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (name,) in rows:
                try:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{str(name).replace(chr(34), chr(34) * 2)}"').fetchone()[0]
                except sqlite3.Error:
                    count = 0
                out["tables"][str(name)] = int(count)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    return out


def find_runtime_files(data_dir: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in RUNTIME_DATA_PATTERNS:
        files.update(path for path in data_dir.glob(pattern) if path.is_file())
    for name in SENSITIVE_NAMES:
        path = data_dir / name
        if path.exists():
            files.add(path)
    return sorted(files, key=lambda item: str(item))


def inspect_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    if isinstance(payload, dict):
        return {"type": "object", "keys": sorted(payload.keys())[:20], "payload": payload}
    if isinstance(payload, list):
        return {"type": "array", "items": len(payload)}
    return {"type": type(payload).__name__}


def sensitive_json_warnings(path: Path, meta: dict[str, Any]) -> list[str]:
    payload = meta.get("payload") if isinstance(meta, dict) else None
    if not isinstance(payload, dict):
        return []
    found: set[str] = set()

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                clean_key = str(key)
                lowered = clean_key.lower()
                child_prefix = f"{prefix}.{clean_key}" if prefix else clean_key
                if lowered in HIGH_RISK_JSON_KEYS:
                    if child not in ("", None, False):
                        found.add(child_prefix)
                walk(child, child_prefix)
        elif isinstance(value, list):
            for idx, child in enumerate(value[:20]):
                walk(child, f"{prefix}[{idx}]")

    walk(payload)
    relative = rel(path)
    warnings = []
    for key in sorted(found):
        if key == "password_hash":
            continue
        warnings.append(f"{relative} 包含明文密码字段 {key}，确认不再需要后应删除或只保留哈希")
    return warnings


def audit(data_dir: Path) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    lines: list[str] = []
    tracked = git_tracked_files()
    data_dir = data_dir.resolve()

    lines.append("服务器数据安全体检")
    lines.append(f"项目根目录：{ROOT}")
    lines.append(f"数据目录：{data_dir}")
    lines.append("")

    db_path = data_dir / "quant_data.sqlite3"
    db = sqlite_summary(db_path)
    if db.get("exists"):
        lines.append(f"SQLite：{rel(db_path)} / {human_size(int(db.get('size_bytes') or 0))}")
        largest_tables = sorted((db.get("tables") or {}).items(), key=lambda item: item[1], reverse=True)[:12]
        for name, count in largest_tables:
            lines.append(f"  - {name}: {count}")
    else:
        warnings.append("SQLite 数据库不存在，生产数据可能仍散落在 JSON/CSV")
        lines.append("SQLite：不存在")

    lines.append("")
    lines.append("敏感与运行数据文件：")
    runtime_files = find_runtime_files(data_dir)
    if not runtime_files:
        lines.append("  - 未发现敏感或运行数据文件")
    for path in runtime_files:
        relative = rel(path)
        tracked_flag = relative in tracked
        size = human_size(path.stat().st_size)
        mode = mode_text(path)
        detail = ""
        meta: dict[str, Any] = {}
        if path.suffix == ".json":
            meta = inspect_json(path)
            if meta:
                printable_meta = {key: value for key, value in meta.items() if key != "payload"}
                detail = f" / {printable_meta}"
        lines.append(f"  - {relative} / {size} / mode {mode}{detail}")
        if tracked_flag:
            warnings.append(f"{relative} 已被 Git 跟踪，生产数据不能入库")
        if path.name in SENSITIVE_NAMES and is_group_or_world_readable(path):
            warnings.append(f"{relative} 权限过宽，建议 chmod 600")
        warnings.extend(sensitive_json_warnings(path, meta))

    backup_dirs = [ROOT / "backups", data_dir.parent / "backups", ROOT / "backend" / "backups"]
    lines.append("")
    lines.append("备份目录：")
    for backup_dir in backup_dirs:
        if not backup_dir.exists():
            continue
        backups = sorted((path for path in backup_dir.glob("*") if path.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)
        total = sum(path.stat().st_size for path in backups)
        lines.append(f"  - {rel(backup_dir)}: {len(backups)} files / {human_size(total)}")
        for path in backups[:5]:
            lines.append(f"    - {path.name} / {human_size(path.stat().st_size)}")
        for path in backups:
            if rel(path) in tracked:
                warnings.append(f"{rel(path)} 备份文件已被 Git 跟踪")

    lines.append("")
    if warnings:
        lines.append("风险：")
        lines.extend(f"  - {item}" for item in warnings)
    else:
        lines.append("结论：未发现会直接泄露到 Git 的服务器数据风险。")
    return lines, warnings


def fix_permissions(data_dir: Path) -> list[str]:
    lines = ["权限修复："]
    if os.name == "nt":
        lines.append("  - Windows 本地环境跳过 chmod，服务器 Linux 环境执行时会自动收紧权限")
        return lines

    data_dir = data_dir.resolve()
    targets: list[Path] = []
    if data_dir.exists():
        try:
            data_dir.chmod(0o700)
            lines.append(f"  - {rel(data_dir)} -> 700")
        except OSError as exc:
            lines.append(f"  - {rel(data_dir)} 权限修复失败：{exc}")
    targets.extend(find_runtime_files(data_dir))

    backup_dirs = [ROOT / "backups", data_dir.parent / "backups", ROOT / "backend" / "backups"]
    for backup_dir in backup_dirs:
        if not backup_dir.exists():
            continue
        try:
            backup_dir.chmod(0o700)
            lines.append(f"  - {rel(backup_dir)} -> 700")
        except OSError as exc:
            lines.append(f"  - {rel(backup_dir)} 权限修复失败：{exc}")
        targets.extend(path for path in backup_dir.glob("*") if path.is_file())

    seen: set[Path] = set()
    for path in sorted(targets, key=lambda item: str(item)):
        if path in seen or not path.exists() or not path.is_file():
            continue
        seen.add(path)
        try:
            path.chmod(0o600)
            lines.append(f"  - {rel(path)} -> 600")
        except OSError as exc:
            lines.append(f"  - {rel(path)} 权限修复失败：{exc}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="服务器数据安全体检")
    parser.add_argument("data_dir", nargs="?", default=str(DEFAULT_DATA_DIR), help="backend/data 目录路径")
    parser.add_argument("--fix-permissions", action="store_true", help="在 Linux 服务器上收紧数据目录和敏感文件权限")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    if args.fix_permissions:
        print("\n".join(fix_permissions(data_dir)))
        print()
    lines, warnings = audit(data_dir)
    print("\n".join(lines))
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
