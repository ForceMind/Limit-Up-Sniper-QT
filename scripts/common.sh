#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

load_env() {
  if [[ -f "$ROOT_DIR/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.env"
    set +a
  fi
}

load_env

APP_DIR="${QUANT_APP_DIR:-$ROOT_DIR}"
SERVICE_NAME="${QUANT_SERVICE_NAME:-qt}"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  PYTHON_BIN="python3"
fi
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT_DIR/backups}"
HOST="${QUANT_HOST:-0.0.0.0}"
PORT="${QUANT_PORT:-8000}"

if [[ -t 1 ]]; then
  COLOR_BLUE=$'\033[1;34m'
  COLOR_GREEN=$'\033[1;32m'
  COLOR_YELLOW=$'\033[1;33m'
  COLOR_RED=$'\033[1;31m'
  COLOR_DIM=$'\033[2m'
  COLOR_RESET=$'\033[0m'
else
  COLOR_BLUE=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_RED=""
  COLOR_DIM=""
  COLOR_RESET=""
fi

section() {
  echo
  echo "${COLOR_BLUE}==> $*${COLOR_RESET}"
}

info() {
  echo "${COLOR_BLUE}[信息]${COLOR_RESET} $*"
}

success() {
  echo "${COLOR_GREEN}[完成]${COLOR_RESET} $*"
}

warn() {
  echo "${COLOR_YELLOW}[注意]${COLOR_RESET} $*" >&2
}

error() {
  echo "${COLOR_RED}[错误]${COLOR_RESET} $*" >&2
}

die() {
  error "$*"
  exit 1
}

require_project_root() {
  if [[ ! -d "$ROOT_DIR/backend/app" || ! -d "$ROOT_DIR/frontend" ]]; then
    die "请在项目目录中运行，或确保 scripts/ 仍位于项目根目录下"
  fi
}

ensure_data_dir() {
  mkdir -p "$ROOT_DIR/backend/data"
}

venv_python() {
  echo "$VENV_DIR/bin/python"
}

venv_pip() {
  echo "$VENV_DIR/bin/pip"
}

systemctl_available() {
  command -v systemctl >/dev/null 2>&1 && [[ "$(id -u)" -eq 0 || -n "${SUDO_USER:-}" ]]
}

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    return 1
  fi
}

refresh_systemd_service() {
  command -v systemctl >/dev/null 2>&1 || return 1

  local service_file="/etc/systemd/system/${SERVICE_NAME}.service"
  local tmp_service
  tmp_service="$(mktemp)"
  cat > "$tmp_service" <<EOF
[Unit]
Description=QT Quant Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${ROOT_DIR}
EnvironmentFile=-${ROOT_DIR}/.env
ExecStart=/usr/bin/env bash ${ROOT_DIR}/scripts/run_quant_server.sh
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

  section "刷新 systemd 服务配置"
  if run_as_root cp "$tmp_service" "$service_file"; then
    rm -f "$tmp_service"
    run_as_root systemctl daemon-reload
    run_as_root systemctl enable "$SERVICE_NAME" >/dev/null
    success "systemd 服务已指向当前项目：$ROOT_DIR"
    systemctl show "$SERVICE_NAME" -p ExecStart -p WorkingDirectory --no-pager || true
    return 0
  fi

  rm -f "$tmp_service"
  warn "无法写入 systemd 服务配置，将改用 nohup 启动方式"
  return 1
}

api_url() {
  echo "http://127.0.0.1:${PORT}/api/auth/status"
}

openapi_url() {
  echo "http://127.0.0.1:${PORT}/openapi.json"
}

version_url() {
  echo "http://127.0.0.1:${PORT}/api/version"
}

local_app_version() {
  if [[ -f "$ROOT_DIR/VERSION" ]]; then
    tr -d '[:space:]' < "$ROOT_DIR/VERSION"
  else
    echo "unknown"
  fi
}

clear_sample_api_url() {
  echo "http://127.0.0.1:${PORT}/api/admin/data/clear_sample_state"
}

print_backend_route_help() {
  warn "页面文件可能已经更新，但当前 Python 后端进程仍是旧代码，或者 systemd 指向了别的目录。"
  echo "请在服务器执行下面命令确认实际运行路径："
  echo "  systemctl show ${SERVICE_NAME} -p MainPID -p ExecStart -p WorkingDirectory"
  echo "  ps -ef | grep uvicorn | grep -v grep"
  echo "  curl -s http://127.0.0.1:${PORT}/api/version"
  echo "  curl -s http://127.0.0.1:${PORT}/openapi.json | grep -E 'front/snapshot|model/backtest|kline/fill|lhb/sync'"
}

wait_for_api_ready() {
  command -v curl >/dev/null 2>&1 || return 0
  local status=""
  for _ in $(seq 1 20); do
    status="$(curl -sS -o /dev/null -w "%{http_code}" --max-time 3 "$(api_url)" 2>/dev/null || true)"
    if [[ "$status" != "000" && -n "$status" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

check_python_bin() {
  if [[ -x "$(venv_python)" ]]; then
    echo "$(venv_python)"
  elif command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "$PYTHON_BIN"
  elif command -v python3 >/dev/null 2>&1; then
    echo "python3"
  elif command -v python >/dev/null 2>&1; then
    echo "python"
  else
    echo ""
  fi
}

backend_feature_modules() {
  cat <<'EOF'
version	版本接口	GET:/api/version
auth	认证注册	GET:/api/auth/status,POST:/api/auth/setup,POST:/api/auth/login,POST:/api/auth/register
frontend	前台终端	GET:/api/front/public_snapshot,GET:/api/front/snapshot,GET:/api/front/profile,POST:/api/front/profile
admin	后台控制	GET:/api/admin/snapshot,POST:/api/admin/system/startup,POST:/api/admin/backup,GET:/api/admin/data/export,POST:/api/admin/data/import,POST:/api/admin/data/clear_sample_state,GET:/api/admin/access_logs,POST:/api/admin/restart
jobs	任务调度	GET:/api/jobs/status,GET:/api/jobs/logs,POST:/api/jobs/{job_name}/pause,POST:/api/jobs/{job_name}/resume,POST:/api/jobs/news/fetch,POST:/api/jobs/market/sync,POST:/api/jobs/ai/analyze,POST:/api/jobs/trading/run,POST:/api/jobs/strategy/replay,POST:/api/jobs/daily/run
data	数据管理	GET:/api/data/coverage,POST:/api/data/kline/fill,GET:/api/data/lhb/status,POST:/api/data/lhb/sync,GET:/api/data/biying/status,POST:/api/data/biying/sync_intraday
quant	量化回测	GET:/api/quant/dashboard,GET:/api/quant/recommendations,GET:/api/quant/daily_plan,GET:/api/quant/timeline,GET:/api/quant/intraday_timeline,GET:/api/quant/backtest,POST:/api/quant/backtest,GET:/api/quant/trading_account,GET:/api/quant/portfolio,POST:/api/quant/run
strategy	策略库	GET:/api/quant/strategy_params,POST:/api/quant/strategy_params,POST:/api/quant/strategy_params/reset,POST:/api/quant/fit_strategy,GET:/api/quant/models,GET:/api/quant/model/backtest,POST:/api/quant/model/apply,GET:/api/quant/evolution/status,POST:/api/quant/evolve_strategy,POST:/api/quant/evolution/pause,POST:/api/quant/evolution/resume
ai	AI监控	GET:/api/ai/usage,GET:/api/ai/records,GET:/api/ai/failures
notify	通知服务	GET:/api/notifications/status,POST:/api/notifications/test
EOF
}

check_running_version() {
  section "应用版本验证"
  if ! command -v curl >/dev/null 2>&1; then
    warn "未安装 curl，无法验证版本"
    return 0
  fi

  local version_file expected running_json py running_version
  expected="$(local_app_version)"
  version_file="$(mktemp)"
  if ! curl -fsS --max-time 8 "$(version_url)" -o "$version_file" 2>/dev/null; then
    rm -f "$version_file"
    error "当前后端缺少版本接口：$(version_url)"
    print_backend_route_help
    return 1
  fi

  py="$(check_python_bin)"
  if [[ -n "$py" ]]; then
    running_version="$("$py" - "$version_file" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload.get("backend_version") or payload.get("version") or "unknown")
PY
)"
  else
    running_json="$(cat "$version_file")"
    running_version="$(printf '%s' "$running_json" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    running_version="${running_version:-unknown}"
  fi
  rm -f "$version_file"

  echo "本地代码版本：$expected"
  echo "运行后端版本：$running_version"
  if [[ "$expected" == "unknown" || "$running_version" == "unknown" ]]; then
    warn "版本号无法完整识别"
    return 0
  fi
  if [[ "$expected" != "$running_version" ]]; then
    error "运行后端版本与本地代码版本不一致"
    print_backend_route_help
    return 1
  fi
  success "前后端版本一致：v$running_version"
}

check_backend_feature_routes() {
  if ! command -v curl >/dev/null 2>&1; then
    warn "未安装 curl，无法验证当前后端接口版本"
    return 0
  fi

  local openapi_file
  openapi_file="$(mktemp)"
  if ! curl -fsS --max-time 8 "$(openapi_url)" -o "$openapi_file" 2>/dev/null; then
    rm -f "$openapi_file"
    error "无法读取当前后端路由表：$(openapi_url)"
    return 1
  fi

  local modules_file py result failed=0
  modules_file="$(mktemp)"
  backend_feature_modules > "$modules_file"
  py="$(check_python_bin)"
  if [[ -z "$py" ]]; then
    warn "找不到 Python，退回基础接口 grep 验证"
    grep -q '"/api/version"' "$openapi_file" \
      && grep -q '"/api/quant/model/backtest"' "$openapi_file" \
      && grep -q '"/api/data/kline/fill"' "$openapi_file" \
      && grep -q '"/api/data/lhb/sync"' "$openapi_file"
    result=$?
    rm -f "$openapi_file" "$modules_file"
    return "$result"
  fi

  while IFS='|' read -r state label detail; do
    case "$state" in
      OK)
        success "$label：$detail"
        ;;
      MISS)
        error "$label：缺少 $detail"
        failed=1
        ;;
    esac
  done < <("$py" - "$openapi_file" "$modules_file" <<'PY'
import json
import sys

openapi_file, modules_file = sys.argv[1], sys.argv[2]
with open(openapi_file, encoding="utf-8") as fh:
    paths = json.load(fh).get("paths", {})

for raw in open(modules_file, encoding="utf-8"):
    raw = raw.rstrip("\n")
    if not raw or raw.startswith("#"):
        continue
    _key, label, required = raw.split("\t", 2)
    missing = []
    total = 0
    for endpoint in [item.strip() for item in required.split(",") if item.strip()]:
        total += 1
        method, path = endpoint.split(":", 1)
        methods = paths.get(path) or {}
        if method.lower() not in methods:
            missing.append(endpoint)
    if missing:
        print(f"MISS|{label}|{', '.join(missing)}")
    else:
        print(f"OK|{label}|{total} 个接口")
PY
)"

  rm -f "$openapi_file" "$modules_file"
  if [[ "$failed" -ne 0 ]]; then
    print_backend_route_help
    return 1
  fi
  success "后端模块化接口验证通过"
}

verify_running_backend() {
  section "后端运行验证"
  if ! command -v curl >/dev/null 2>&1; then
    warn "未安装 curl，跳过后端版本验证"
    return 0
  fi
  if ! wait_for_api_ready; then
    error "重启后 API 没有在 20 秒内响应：$(api_url)"
    return 1
  fi
  check_running_version
  check_backend_feature_routes
}
