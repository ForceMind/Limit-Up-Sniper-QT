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

clear_sample_api_url() {
  echo "http://127.0.0.1:${PORT}/api/admin/data/clear_sample_state"
}

print_backend_route_help() {
  warn "页面文件可能已经更新，但当前 Python 后端进程仍是旧代码，或者 systemd 指向了别的目录。"
  echo "请在服务器执行下面命令确认实际运行路径："
  echo "  systemctl show ${SERVICE_NAME} -p MainPID -p ExecStart -p WorkingDirectory"
  echo "  ps -ef | grep uvicorn | grep -v grep"
  echo "  curl -s http://127.0.0.1:${PORT}/openapi.json | grep -E 'clear_sample_state|kline/fill|lhb/sync'"
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

  if grep -q '"/api/admin/data/clear_sample_state"' "$openapi_file" \
    && grep -q '"/api/data/kline/fill"' "$openapi_file" \
    && grep -q '"/api/data/lhb/sync"' "$openapi_file"; then
    rm -f "$openapi_file"
    success "当前运行中的后端已包含：清理样例、补齐日K、龙虎榜同步接口"
    return 0
  fi

  rm -f "$openapi_file"
  error "当前运行中的后端路由表缺少清理样例、补齐日K或龙虎榜同步接口"
  print_backend_route_help
  return 1
}

verify_running_backend() {
  section "后端版本验证"
  if ! command -v curl >/dev/null 2>&1; then
    warn "未安装 curl，跳过后端版本验证"
    return 0
  fi
  if ! wait_for_api_ready; then
    error "重启后 API 没有在 20 秒内响应：$(api_url)"
    return 1
  fi
  check_backend_feature_routes
}
