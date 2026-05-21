#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_SIZE = 2_000_000
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
SECRET_HINTS = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|license[_-]?key|smtp[_-]?password|authorization)",
    re.IGNORECASE,
)
ASSIGNMENT = re.compile(r"[:=]\s*['\"]([^'\"]{8,})['\"]")
FORBIDDEN_EXACT_PATHS = {
    ".env",
    ".env.local",
    ".env.production",
    "backend/data/auth.json",
    "backend/data/config.json",
    "backend/data/admin_credentials.json",
    "backend/data/ws_token_secret.txt",
}
FORBIDDEN_NAME_HINTS = re.compile(
    r"(^|/)(id_rsa|id_dsa|id_ecdsa|id_ed25519)$|"
    r"(^|/)\.env\.(?!example$)[^/]+$|"
    r"(^|/).*?(credential|secret|password|private[_-]?key).*?$|"
    r"\.(pem|p12|pfx)$",
    re.IGNORECASE,
)


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]


def entropy(value: str) -> float:
    if not value:
        return 0.0
    freq = {ch: value.count(ch) for ch in set(value)}
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in freq.values())


def should_skip(path: Path) -> bool:
    parts = set(path.relative_to(ROOT).parts)
    if parts.intersection(SKIP_DIRS):
        return True
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".sqlite3", ".db"}:
        return True
    return path.stat().st_size > MAX_FILE_SIZE


def forbidden_upload_reason(path: Path) -> str:
    full_path = path if path.is_absolute() else ROOT / path
    rel = full_path.resolve().relative_to(ROOT).as_posix()
    if rel in FORBIDDEN_EXACT_PATHS:
        return "forbidden sensitive config path"
    if FORBIDDEN_NAME_HINTS.search(rel):
        return "forbidden sensitive filename"
    return ""


def scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not SECRET_HINTS.search(line):
            continue
        if "HTTPException" in line:
            continue
        if "class=" in line and not re.search(r"(api|secret|password|token|key)\s*[:=]", line, re.IGNORECASE):
            continue
        if "example" in path.name.lower() and re.search(r"[:=]\s*['\"]?['\"]?\s*(,|$)", line):
            continue
        match = ASSIGNMENT.search(line)
        if not match:
            continue
        value = match.group(1).strip()
        if value.lower() in {"true", "false", "none", "null", "changeme", "example"}:
            continue
        if len(value) >= 12 or entropy(value) >= 3.2:
            findings.append((lineno, "possible secret assignment"))
    return findings


def main() -> int:
    findings: list[str] = []
    for path in candidate_files():
        if not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        reason = forbidden_upload_reason(path)
        if reason:
            findings.append(f"{rel}:1: {reason}")
            continue
        if should_skip(path):
            continue
        for lineno, reason in scan_file(path):
            findings.append(f"{rel}:{lineno}: {reason}")
    if findings:
        print("Potential secrets found in candidate files. Values are intentionally hidden:", file=sys.stderr)
        for item in findings:
            print(f"- {item}", file=sys.stderr)
        return 1
    print("No obvious secrets found in candidate files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
