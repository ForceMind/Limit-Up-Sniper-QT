#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import subprocess
import sys
import json
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
ALLOWED_DATA_FILES = {
    "backend/data/.gitkeep",
    "backend/data/config.example.json",
    "backend/data/biying_stock_list.json",
    "backend/data/news_history.json",
    "backend/data/news_analysis_records.json",
    "backend/data/kline_day_cache/600001.json",
    "backend/data/kline_day_cache/600002.json",
    "backend/data/kline_cache/600001_2026-05-19.csv",
    "backend/data/kline_cache/600002_2026-05-19.csv",
}
DATA_FILE_SIZE_LIMITS = {
    "backend/data/biying_stock_list.json": 20_000,
    "backend/data/news_history.json": 20_000,
    "backend/data/news_analysis_records.json": 80_000,
    "backend/data/kline_day_cache/600001.json": 80_000,
    "backend/data/kline_day_cache/600002.json": 80_000,
    "backend/data/kline_cache/600001_2026-05-19.csv": 20_000,
    "backend/data/kline_cache/600002_2026-05-19.csv": 20_000,
}


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
    if rel.startswith("backend/data/") and rel not in ALLOWED_DATA_FILES:
        return "backend/data production files must not be tracked"
    if rel in DATA_FILE_SIZE_LIMITS and full_path.stat().st_size > DATA_FILE_SIZE_LIMITS[rel]:
        return "fixture data file is larger than allowed sample size"
    fixture_reason = fixture_data_reason(full_path, rel)
    if fixture_reason:
        return fixture_reason
    if FORBIDDEN_NAME_HINTS.search(rel):
        return "forbidden sensitive filename"
    return ""


def fixture_data_reason(path: Path, rel: str) -> str:
    if rel == "backend/data/news_history.json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return "fixture news file must be valid JSON"
        if not isinstance(payload, list) or len(payload) > 20:
            return "fixture news file must stay small"
        for item in payload:
            if not isinstance(item, dict) or str(item.get("source") or "") != "Fixture":
                return "fixture news file contains non-Fixture source"
    if rel == "backend/data/news_analysis_records.json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return "fixture AI record file must be valid JSON"
        if not isinstance(payload, list) or len(payload) > 20:
            return "fixture AI record file must stay small"
        text = json.dumps(payload, ensure_ascii=False)
        if "Fixture" not in text or "样例" not in text:
            return "fixture AI record file must contain only sample records"
    if rel == "backend/data/biying_stock_list.json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return "fixture stock list must be valid JSON"
        stocks = payload.get("stocks") if isinstance(payload, dict) else {}
        if set(stocks.keys()) - {"600001", "600002"}:
            return "fixture stock list must only contain sample stock codes"
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
        if "==" in line or "!=" in line:
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
