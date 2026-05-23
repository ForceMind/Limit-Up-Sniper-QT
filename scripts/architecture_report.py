from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WATCH_DIRS = ("backend", "frontend", "scripts", "docs")
WATCH_EXTENSIONS = {".py", ".html", ".js", ".sh", ".md"}
LINE_LIMITS = {
    ".py": 900,
    ".html": 1200,
    ".js": 1000,
    ".sh": 700,
    ".md": 1200,
}
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
SENSITIVE_DATA_NAMES = {
    ".env",
    "auth.json",
    "config.json",
    "admin_credentials.json",
    "admin_sessions.json",
    "ws_token_secret.txt",
    "quant_data.sqlite3",
}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def count_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def human_size(value: int) -> str:
    n = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{int(value)} B"


def git_ls_files() -> set[str]:
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


def tracked_source_files() -> list[tuple[int, Path]]:
    files: list[tuple[int, Path]] = []
    for dirname in WATCH_DIRS:
        root = ROOT / dirname
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in WATCH_EXTENSIONS:
                continue
            files.append((count_lines(path), path))
    return sorted(files, reverse=True, key=lambda item: item[0])


def data_boundary_summary() -> tuple[list[str], list[str]]:
    tracked = git_ls_files()
    tracked_data = sorted(path for path in tracked if path.startswith("backend/data/"))
    disallowed = [path for path in tracked_data if path not in ALLOWED_DATA_FILES]
    data_dir = ROOT / "backend" / "data"
    local_sensitive = sorted(name for name in SENSITIVE_DATA_NAMES if (data_dir / name).exists())

    lines: list[str] = []
    findings: list[str] = []
    lines.append("== 服务器数据边界 ==")
    lines.append(f"Git 跟踪 backend/data：{len(tracked_data)} 个文件")
    if disallowed:
        findings.append("P0 backend/data 出现非白名单文件被 Git 跟踪，生产数据库、配置、日志和备份不能进入仓库。")
        for path in disallowed[:10]:
            lines.append(f"  - 风险：{path}")
    else:
        lines.append("  - 未发现生产数据被 Git 跟踪")

    db_path = data_dir / "quant_data.sqlite3"
    if db_path.exists():
        lines.append(f"SQLite 主库：backend/data/quant_data.sqlite3 / {human_size(db_path.stat().st_size)} / 本地运行数据，不进 Git")
    else:
        lines.append("SQLite 主库：本地未发现，服务器首次运行或迁移后会生成")

    if local_sensitive:
        lines.append("本地敏感运行文件：")
        for name in local_sensitive:
            lines.append(f"  - backend/data/{name}")
        lines.append("  - 这些文件应只留在服务器或本机运行目录，不通过 Git 迁移")
    else:
        lines.append("本地未发现敏感运行文件")

    return lines, findings


def route_summary() -> tuple[int, dict[str, int]]:
    main_file = ROOT / "backend" / "app" / "main.py"
    if not main_file.exists():
        return 0, {}
    methods: dict[str, int] = {}
    total = 0
    pattern = re.compile(r"^@app\.([a-zA-Z_]+)")
    for line in main_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        method = match.group(1)
        methods[method] = methods.get(method, 0) + 1
        total += 1
    return total, dict(sorted(methods.items(), key=lambda item: item[1], reverse=True))


def print_top_files(files: list[tuple[int, Path]]) -> None:
    print("== 文件规模 Top 20 ==")
    for lines, path in files[:20]:
        rel = path.relative_to(ROOT).as_posix()
        limit = LINE_LIMITS.get(path.suffix, 1000)
        marker = " 需要拆分" if lines > limit else ""
        print(f"{lines:5d}  {rel}{marker}")


def print_findings(files: list[tuple[int, Path]], route_total: int, extra_findings: list[str]) -> None:
    line_map = {path.relative_to(ROOT).as_posix(): lines for lines, path in files}
    findings: list[str] = []

    if line_map.get("backend/app/main.py", 0) > 900 or route_total > 50:
        findings.append(
            "P0 backend/app/main.py 同时承担认证、前台、后台、任务、数据和静态页面托管；"
            "应先拆成 api/auth.py、api/front.py、api/admin.py、api/jobs.py、api/data.py、api/quant.py。"
        )
    if line_map.get("backend/app/quant/engine.py", 0) > 1200:
        findings.append(
            "P0 backend/app/quant/engine.py 混合数据读取、因子、评分、回放、账户和策略参数；"
            "应先抽 repositories.py、factors.py、backtest.py、accounting.py。"
        )
    if line_map.get("frontend/admin/index.html", 0) > 1500:
        findings.append(
            "P1 frontend/admin/index.html 是单文件后台；用户管理、数据管理、任务日志和策略页需要拆成独立组件或独立页面。"
        )
    if line_map.get("frontend/index.html", 0) > 1200:
        findings.append(
            "P1 frontend/index.html 已包含登录、概览、账户、策略、新闻多套状态；应把 API client、状态管理和视图渲染分离。"
        )
    if line_map.get("scripts/common.sh", 0) > 600:
        findings.append(
            "P1 scripts/common.sh 已经承担部署、Nginx、迁移、验证和 systemd 通用逻辑；应拆 deploy_common.sh、nginx.sh、sqlite.sh。"
        )
    findings.extend(extra_findings)

    print()
    print("== 架构问题优先级 ==")
    if not findings:
        print("未发现超过当前阈值的高风险结构。")
        return
    for finding in findings:
        print(f"- {finding}")


def main() -> None:
    files = tracked_source_files()
    route_total, methods = route_summary()
    data_lines, data_findings = data_boundary_summary()
    print("涨停狙击手架构体检")
    print(f"项目根目录：{ROOT}")
    print(f"FastAPI 路由装饰器：{route_total} 个")
    if methods:
        print("路由类型：" + ", ".join(f"{name}={count}" for name, count in methods.items()))
    print()
    print_top_files(files)
    print()
    print("\n".join(data_lines))
    print_findings(files, route_total, data_findings)


if __name__ == "__main__":
    main()
