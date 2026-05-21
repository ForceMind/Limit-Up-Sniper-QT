#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
AUTH_FILE = ROOT / "backend" / "data" / "auth.json"
PBKDF2_ITERATIONS = 200_000


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_auth() -> Dict[str, Any]:
    try:
        if not AUTH_FILE.exists():
            return {}
        payload = json.loads(AUTH_FILE.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_auth(payload: Dict[str, Any]) -> None:
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_suffix(f"{AUTH_FILE.suffix}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(AUTH_FILE)


def hash_password(password: str) -> Dict[str, Any]:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return {
        "algorithm": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt,
        "hash": digest,
    }


def clean_username(value: str) -> str:
    return str(value or "").strip()


def prompt_username(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        username = clean_username(input(f"{label}{suffix}：") or default)
        if len(username) >= 3:
            return username
        print("用户名至少 3 位。")


def prompt_password(label: str) -> str:
    while True:
        password = getpass.getpass(f"{label}：")
        confirm = getpass.getpass("再次输入确认：")
        if password != confirm:
            print("两次密码不一致，请重试。")
            continue
        if len(password) < 8:
            print("密码至少 8 位。")
            continue
        return password


def ensure_payload() -> Dict[str, Any]:
    payload = read_auth()
    if not payload:
        payload = {
            "version": 1,
            "created_at": now_iso(),
            "token_secret": secrets.token_urlsafe(32),
            "users": {},
        }
    payload.setdefault("version", 1)
    payload.setdefault("created_at", now_iso())
    payload.setdefault("token_secret", secrets.token_urlsafe(32))
    if not isinstance(payload.get("users"), dict):
        payload["users"] = {}
    payload["updated_at"] = now_iso()
    return payload


def set_user(scope: str, username: str, password: str) -> Dict[str, Any]:
    if scope not in {"admin", "frontend"}:
        raise ValueError("scope 只能是 admin 或 frontend")
    username = clean_username(username)
    if len(username) < 3:
        raise ValueError("用户名至少 3 位")
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    payload = ensure_payload()
    payload["users"][scope] = {
        "username": username,
        "password": hash_password(password),
        "updated_at": now_iso(),
    }
    write_auth(payload)
    return payload


def auth_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    users = payload.get("users") if isinstance(payload.get("users"), dict) else {}
    admin = users.get("admin") if isinstance(users.get("admin"), dict) else {}
    frontend = users.get("frontend") if isinstance(users.get("frontend"), dict) else {}
    return {
        "admin_username": str(admin.get("username") or ""),
        "frontend_username": str(frontend.get("username") or ""),
        "admin_configured": bool(admin.get("username") and admin.get("password")),
        "frontend_configured": bool(frontend.get("username") and frontend.get("password")),
    }


def cmd_status(_: argparse.Namespace) -> int:
    payload = read_auth()
    summary = auth_summary(payload)
    print(f"认证文件：{AUTH_FILE}")
    if not AUTH_FILE.exists():
        print("状态：未初始化")
        print("提示：可以在网页后台首次初始化，也可以在 qt 面板里创建账号。")
        return 0
    print("状态：已存在")
    print(f"后台账号：{summary['admin_username'] or '-'} ({'已配置' if summary['admin_configured'] else '未配置'})")
    print(f"前台账号：{summary['frontend_username'] or '-'} ({'已配置' if summary['frontend_configured'] else '未配置'})")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    payload = read_auth()
    summary = auth_summary(payload)
    admin_default = args.admin or summary["admin_username"] or "admin"
    frontend_default = args.frontend or summary["frontend_username"] or "trader"
    admin_user = prompt_username("后台用户名", admin_default)
    admin_password = prompt_password("后台密码")
    frontend_user = prompt_username("前台用户名", frontend_default)
    frontend_password = prompt_password("前台密码")
    set_user("admin", admin_user, admin_password)
    set_user("frontend", frontend_user, frontend_password)
    print("前后台账号已保存。")
    print(f"后台账号：{admin_user}")
    print(f"前台账号：{frontend_user}")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    scope = args.scope
    payload = read_auth()
    users = payload.get("users") if isinstance(payload.get("users"), dict) else {}
    current = users.get(scope) if isinstance(users.get(scope), dict) else {}
    label = "后台管理员" if scope == "admin" else "前台交易终端"
    username = args.username or prompt_username(f"{label}用户名", str(current.get("username") or ""))
    password = args.password or prompt_password(f"{label}密码")
    set_user(scope, username, password)
    print(f"{label}账号已更新：{username}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    if not AUTH_FILE.exists():
        print("认证文件不存在，无需删除。")
        return 0
    if not args.force:
        confirm = input("确认删除认证文件并回到首次初始化？输入 DELETE 确认：").strip()
        if confirm != "DELETE":
            print("已取消。")
            return 1
    AUTH_FILE.unlink()
    print("认证文件已删除。下次访问 /admin 会重新进入首次初始化。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理涨停狙击手前台/后台账号")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="查看账号状态")
    status.set_defaults(func=cmd_status)

    init = sub.add_parser("init", help="初始化或重建前后台账号")
    init.add_argument("--admin", default="", help="后台默认用户名")
    init.add_argument("--frontend", default="", help="前台默认用户名")
    init.set_defaults(func=cmd_init)

    set_cmd = sub.add_parser("set", help="修改单个账号")
    set_cmd.add_argument("--scope", choices=["admin", "frontend"], required=True)
    set_cmd.add_argument("--username", default="")
    set_cmd.add_argument("--password", default="")
    set_cmd.set_defaults(func=cmd_set)

    delete = sub.add_parser("delete", help="删除认证文件，回到首次初始化")
    delete.add_argument("--force", action="store_true")
    delete.set_defaults(func=cmd_delete)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
