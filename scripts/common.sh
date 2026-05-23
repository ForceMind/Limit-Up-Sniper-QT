#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
export LANG=C
export PYTHONIOENCODING="${PYTHONIOENCODING:-UTF-8}"
QT_EMPTY=""

zh() {
  printf '%b' "$1"
}

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
NGINX_UPLOAD_MAX_SIZE="${QT_NGINX_UPLOAD_MAX_SIZE:-1024m}"
NGINX_PROXY_TIMEOUT="${QT_NGINX_PROXY_TIMEOUT:-1800}"

is_truthy() {
  local value="${1:-}"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "TRUE" || "$value" == "yes" || "$value" == "YES" || "$value" == "on" || "$value" == "ON" ]]
}

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
  echo "${COLOR_BLUE}[""$(zh '\xe4\xbf\xa1\xe6\x81\xaf')""]${COLOR_RESET} $*"
}

success() {
  echo "${COLOR_GREEN}[""$(zh '\xe5\xae\x8c\xe6\x88\x90')""]${COLOR_RESET} $*"
}

warn() {
  echo "${COLOR_YELLOW}[""$(zh '\xe6\xb3\xa8\xe6\x84\x8f')""]${COLOR_RESET} $*" >&2
}

error() {
  echo "${COLOR_RED}[""$(zh '\xe9\x94\x99\xe8\xaf\xaf')""]${COLOR_RESET} $*" >&2
}

die() {
  error "$*"
  exit 1
}

require_project_root() {
  if [[ ! -d "$ROOT_DIR/backend/app" || ! -d "$ROOT_DIR/frontend" ]]; then
    die "$(zh '\xe8\xaf\xb7\xe5\x9c\xa8\xe9\xa1\xb9\xe7\x9b\xae\xe7\x9b\xae\xe5\xbd\x95\xe4\xb8\xad\xe8\xbf\x90\xe8\xa1\x8c\xef\xbc\x8c\xe6\x88\x96\xe7\xa1\xae\xe4\xbf\x9d')"" scripts/ ""$(zh '\xe4\xbb\x8d\xe4\xbd\x8d\xe4\xba\x8e\xe9\xa1\xb9\xe7\x9b\xae\xe6\xa0\xb9\xe7\x9b\xae\xe5\xbd\x95\xe4\xb8\x8b')"
  fi
}

ensure_data_dir() {
  mkdir -p "$ROOT_DIR/backend/data"
}

runtime_data_dir() {
  local data_dir="${QUANT_DATA_DIR:-$ROOT_DIR/backend/data}"
  if [[ "$data_dir" != /* ]]; then
    data_dir="$ROOT_DIR/$data_dir"
  fi
  printf '%s\n' "$data_dir"
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

auto_migrate_sqlite() {
  local skip="${QT_SKIP_AUTO_MIGRATE:-0}"
  if is_truthy "$skip"; then
    warn "已跳过 SQLite 自动迁移：QT_SKIP_AUTO_MIGRATE=$skip"
    return 0
  fi

  local script="$ROOT_DIR/scripts/migrate_data_to_sqlite.py"
  if [[ ! -f "$script" ]]; then
    warn "未找到 SQLite 迁移脚本，跳过自动迁移"
    return 0
  fi

  local data_dir db_file python_bin marker mode force need reason
  data_dir="$(runtime_data_dir)"
  db_file="$data_dir/quant_data.sqlite3"
  marker="$data_dir/.sqlite_migration_state"
  mode="${QT_AUTO_MIGRATE_MODE:-smart}"
  force="${QT_FORCE_AUTO_MIGRATE:-0}"
  python_bin="$(check_python_bin)"
  mkdir -p "$data_dir"

  section "SQLite 数据自动迁移"
  if is_truthy "$force"; then
    need=1
    reason="已设置 QT_FORCE_AUTO_MIGRATE=$force"
  elif [[ "$mode" == "always" ]]; then
    need=1
    reason="QT_AUTO_MIGRATE_MODE=always"
  elif [[ ! -f "$db_file" ]]; then
    need=1
    reason="SQLite 数据库不存在"
  elif [[ ! -f "$marker" ]]; then
    need=1
    reason="首次记录迁移状态"
  elif [[ "$script" -nt "$marker" ]]; then
    need=1
    reason="迁移脚本已更新"
  else
    need=0
    reason="数据库已迁移且迁移脚本未变化"
  fi

  if [[ "$need" -eq 0 ]]; then
    success "SQLite 已完成迁移，本次跳过全量整理：$reason"
    echo "如需强制重新合并 JSON/CSV 数据：QT_FORCE_AUTO_MIGRATE=1 qt update，或执行 qt migrate"
    check_sqlite_schema
    return 0
  fi

  info "数据目录：$data_dir"
  info "迁移原因：$reason"
  "$python_bin" "$script" --source "$data_dir" --db "$db_file"
  {
    echo "updated_at=$(date -Iseconds)"
    echo "script=$script"
    echo "db=$db_file"
    echo "reason=$reason"
  } > "$marker"
  check_sqlite_schema
  success "SQLite 数据自动迁移完成：$db_file"
}

check_sqlite_schema() {
  local skip="${QT_SKIP_AUTO_MIGRATE:-0}"
  if is_truthy "$skip"; then
    warn "已跳过 SQLite 表结构验证：QT_SKIP_AUTO_MIGRATE=$skip"
    return 0
  fi

  local data_dir db_file py
  data_dir="$(runtime_data_dir)"
  db_file="$data_dir/quant_data.sqlite3"
  py="$(check_python_bin)"

  section "SQLite 数据表验证"
  if [[ ! -f "$db_file" ]]; then
    warn "未找到 SQLite 数据库，跳过表结构验证：$db_file"
    return 0
  fi
  if [[ -z "$py" ]]; then
    warn "找不到 Python，跳过 SQLite 表结构验证"
    return 0
  fi

  "$py" - "$db_file" <<'PY'
import sqlite3
import sys

db_file = sys.argv[1]
required = {
    "news_raw": ["id", "date", "text"],
    "news_events": ["event_id", "date", "code", "impact_score"],
    "market_daily_bars": ["code", "date", "open", "close", "volume"],
    "market_minute_bars": ["code", "date", "time", "close"],
    "lhb_records": ["record_id", "trade_date", "stock_code", "buyer_seat_name"],
    "strategy_runs": ["run_id", "status", "started_at", "raw_json"],
    "strategy_model_metrics": ["metric_id", "run_id", "generation", "raw_json"],
    "strategy_candidates": ["candidate_id", "run_id", "generation", "rank", "elimination_reason", "raw_json"],
    "strategy_models": ["model_id", "run_id", "params_json", "backtest_json", "raw_json"],
    "strategy_model_records": ["record_id", "model_id", "record_type", "raw_json"],
    "paper_accounts": ["as_of", "cash", "raw_json"],
    "paper_positions": ["position_id", "code", "raw_json"],
    "paper_trades": ["trade_id", "code", "raw_json"],
    "access_logs": ["access_id", "ip", "user_agent", "raw_json"],
    "job_logs": ["log_id", "job", "message", "raw_json"],
}

conn = sqlite3.connect(db_file)
try:
    table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {row[0] for row in table_rows}
    errors = []
    for table, columns in required.items():
        if table not in tables:
            errors.append(f"缺少表 {table}")
            continue
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column not in existing:
                errors.append(f"{table} 缺少字段 {column}")
    if errors:
        print("SQLite 表结构验证失败：")
        for item in errors:
            print(f"- {item}")
        sys.exit(1)
    print("SQLite 表结构验证通过：策略模型、模型成交记录、行情、新闻、日志表都已存在")
finally:
    conn.close()
PY
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

  section "$(zh '\xe5\x88\xb7\xe6\x96\xb0')"" systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe9\x85\x8d\xe7\xbd\xae')"
  if run_as_root cp "$tmp_service" "$service_file"; then
    rm -f "$tmp_service"
    run_as_root systemctl daemon-reload
    run_as_root systemctl enable "$SERVICE_NAME" >/dev/null
    success "systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe5\xb7\xb2\xe6\x8c\x87\xe5\x90\x91\xe5\xbd\x93\xe5\x89\x8d\xe9\xa1\xb9\xe7\x9b\xae\xef\xbc\x9a')""$ROOT_DIR"
    systemctl show "$SERVICE_NAME" -p ExecStart -p WorkingDirectory --no-pager || true
    return 0
  fi

  rm -f "$tmp_service"
  warn "$(zh '\xe6\x97\xa0\xe6\xb3\x95\xe5\x86\x99\xe5\x85\xa5')"" systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe9\x85\x8d\xe7\xbd\xae\xef\xbc\x8c\xe5\xb0\x86\xe6\x94\xb9\xe7\x94\xa8')"" nohup ""$(zh '\xe5\x90\xaf\xe5\x8a\xa8\xe6\x96\xb9\xe5\xbc\x8f')"
  return 1
}

find_nginx_qt_configs() {
  local dirs=("/etc/nginx/conf.d" "/etc/nginx/sites-enabled" "/etc/nginx/sites-available")
  local dir file real
  for dir in "${dirs[@]}"; do
    [[ -d "$dir" ]] || continue
    while IFS= read -r -d '' file; do
      case "$(basename "$file")" in
        *.qt_upload_backup_*|*.bak|*.old|*~) continue ;;
      esac
      if grep -Eq "127[.]0[.]0[.]1:${PORT}|localhost:${PORT}|server_name[[:space:]].*(qt|zhangting)" "$file" 2>/dev/null; then
        real="$(readlink -f "$file" 2>/dev/null || printf '%s' "$file")"
        case "$(basename "$real")" in
          *.qt_upload_backup_*|*.bak|*.old|*~) continue ;;
        esac
        printf '%s\n' "$real"
      fi
    done < <(find "$dir" -maxdepth 1 \( -type f -o -type l \) -print0 2>/dev/null)
  done | awk '!seen[$0]++'
}

cleanup_nginx_upload_backups() {
  local keep_days="${QT_NGINX_BACKUP_KEEP_DAYS:-7}"
  [[ "$keep_days" =~ ^[0-9]+$ ]] || keep_days=7
  local dirs=("/etc/nginx/conf.d" "/etc/nginx/sites-enabled" "/etc/nginx/sites-available")
  local dir file removed=0
  for dir in "${dirs[@]}"; do
    [[ -d "$dir" ]] || continue
    while IFS= read -r -d '' file; do
      if run_as_root rm -f "$file"; then
        removed=$((removed + 1))
      fi
    done < <(
      find "$dir" -maxdepth 1 -type f -name "*.qt_upload_backup_*" \
        \( -name "*.qt_upload_backup_*.qt_upload_backup_*" -o -mtime "+$keep_days" \) \
        -print0 2>/dev/null
    )
  done
  if [[ "$removed" -gt 0 ]]; then
    success "已清理 Nginx 历史上传配置备份：${removed} 个"
  fi
}

patch_nginx_upload_config() {
  local file="$1"
  local limit="${2:-$NGINX_UPLOAD_MAX_SIZE}"
  local timeout="${3:-$NGINX_PROXY_TIMEOUT}"
  [[ -f "$file" ]] || return 1
  local has_proxy=0 needs_buffering=0 needs_timeout=0 needs_body=0
  grep -qE "proxy_pass[[:space:]]+http://(127[.]0[.]0[.]1|localhost)" "$file" && has_proxy=1
  if [[ "$has_proxy" -eq 1 ]] && ! grep -qE "proxy_request_buffering[[:space:]]+off;" "$file"; then
    needs_buffering=1
  fi
  if ! grep -qE "client_max_body_size[[:space:]]+${limit};" "$file"; then
    needs_body=1
  fi
  if [[ "$has_proxy" -eq 1 ]]; then
    grep -qE "proxy_read_timeout[[:space:]]+${timeout};" "$file" || needs_timeout=1
    grep -qE "proxy_send_timeout[[:space:]]+${timeout};" "$file" || needs_timeout=1
    grep -qE "proxy_connect_timeout[[:space:]]+60;" "$file" || needs_timeout=1
  fi
  if [[ "$needs_body" -eq 0 && "$needs_buffering" -eq 0 && "$needs_timeout" -eq 0 ]]; then
    success "Nginx 上传限制和等待超时已是 ${limit}/${timeout}s: $file"
    return 2
  fi
  local backup="${file}.qt_upload_backup_$(date +%Y%m%d_%H%M%S)"
  run_as_root cp "$file" "$backup" || return 1

  if [[ "$needs_body" -eq 1 ]]; then
    if grep -qE "client_max_body_size[[:space:]]+" "$file"; then
      run_as_root sed -i -E "s/client_max_body_size[[:space:]]+[^;]+;/client_max_body_size ${limit};/g" "$file"
    elif grep -qE "server[[:space:]]*\\{" "$file"; then
      run_as_root sed -i -E "/server[[:space:]]*\\{/a\\    client_max_body_size ${limit};" "$file"
    else
      warn "未找到 server { 段，跳过 $file"
      return 1
    fi
  fi

  if [[ "$has_proxy" -eq 1 && "$needs_buffering" -eq 1 ]]; then
    if grep -qE "proxy_http_version[[:space:]]+1[.]1;" "$file"; then
      run_as_root sed -i -E "/proxy_http_version[[:space:]]+1[.]1;/a\\        proxy_request_buffering off;" "$file" || true
    else
      run_as_root sed -i -E "/proxy_pass[[:space:]]+http/a\\        proxy_request_buffering off;" "$file" || true
    fi
  fi

  if [[ "$has_proxy" -eq 1 && "$needs_timeout" -eq 1 ]]; then
    if grep -qE "proxy_connect_timeout[[:space:]]+" "$file"; then
      run_as_root sed -i -E "s/proxy_connect_timeout[[:space:]]+[^;]+;/proxy_connect_timeout 60;/g" "$file"
    else
      run_as_root sed -i -E "/proxy_pass[[:space:]]+http/a\\        proxy_connect_timeout 60;" "$file" || true
    fi
    if grep -qE "proxy_send_timeout[[:space:]]+" "$file"; then
      run_as_root sed -i -E "s/proxy_send_timeout[[:space:]]+[^;]+;/proxy_send_timeout ${timeout};/g" "$file"
    else
      run_as_root sed -i -E "/proxy_pass[[:space:]]+http/a\\        proxy_send_timeout ${timeout};" "$file" || true
    fi
    if grep -qE "proxy_read_timeout[[:space:]]+" "$file"; then
      run_as_root sed -i -E "s/proxy_read_timeout[[:space:]]+[^;]+;/proxy_read_timeout ${timeout};/g" "$file"
    else
      run_as_root sed -i -E "/proxy_pass[[:space:]]+http/a\\        proxy_read_timeout ${timeout};" "$file" || true
    fi
  fi
  success "Nginx 上传限制和等待超时已设置为 ${limit}/${timeout}s: $file"
}

ensure_nginx_upload_limit() {
  section "Nginx 上传限制和等待超时"
  if ! command -v nginx >/dev/null 2>&1; then
    warn "$(zh '\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"" nginx""$(zh '\xef\xbc\x8c\xe8\xb7\xb3\xe8\xbf\x87')"" Nginx ""$(zh '\xe4\xb8\x8a\xe4\xbc\xa0\xe9\x99\x90\xe5\x88\xb6\xe4\xbf\xae\xe5\xa4\x8d')"
    return 0
  fi
  cleanup_nginx_upload_backups
  local files=()
  while IFS= read -r file; do
    [[ -n "$file" ]] && files+=("$file")
  done < <(find_nginx_qt_configs)
  if [[ "${#files[@]}" -eq 0 ]]; then
    warn "$(zh '\xe6\x9c\xaa\xe6\x89\xbe\xe5\x88\xb0\xe6\x8c\x87\xe5\x90\x91')"" 127.0.0.1:${PORT} ""$(zh '\xe7\x9a\x84')"" Nginx ""$(zh '\xe7\xab\x99\xe7\x82\xb9\xe9\x85\x8d\xe7\xbd\xae')"
    echo "请手动在对应 server/location 段加上：client_max_body_size ${NGINX_UPLOAD_MAX_SIZE}; proxy_read_timeout ${NGINX_PROXY_TIMEOUT}; proxy_send_timeout ${NGINX_PROXY_TIMEOUT};"
    return 0
  fi
  local file changed=0 rc=0
  for file in "${files[@]}"; do
    set +e
    patch_nginx_upload_config "$file" "$NGINX_UPLOAD_MAX_SIZE" "$NGINX_PROXY_TIMEOUT"
    rc=$?
    set -e
    if [[ "$rc" -eq 0 ]]; then
      changed=1
    elif [[ "$rc" -eq 2 ]]; then
      true
    else
      warn "Nginx 配置处理失败，已跳过：$file"
    fi
  done
  if [[ "$changed" -eq 1 ]]; then
    if run_as_root nginx -t; then
      if command -v systemctl >/dev/null 2>&1; then
        run_as_root systemctl reload nginx || run_as_root nginx -s reload || true
      else
        run_as_root nginx -s reload || true
      fi
      success "Nginx ""$(zh '\xe5\xb7\xb2\xe9\x87\x8d\xe8\xbd\xbd\xef\xbc\x8c\xe5\x8f\xaf\xe9\x87\x8d\xe6\x96\xb0\xe4\xb8\x8a\xe4\xbc\xa0\xe6\x95\xb0\xe6\x8d\xae\xe5\x8c\x85')"
    else
      error "Nginx ""$(zh '\xe9\x85\x8d\xe7\xbd\xae\xe6\xa3\x80\xe6\x9f\xa5\xe5\xa4\xb1\xe8\xb4\xa5\xef\xbc\x8c\xe5\xb7\xb2\xe7\x95\x99\xe4\xb8\x8b')"" .qt_upload_backup_* ""$(zh '\xe5\xa4\x87\xe4\xbb\xbd')"
      return 1
    fi
  fi
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
  warn "$(zh '\xe9\xa1\xb5\xe9\x9d\xa2\xe6\x96\x87\xe4\xbb\xb6\xe5\x8f\xaf\xe8\x83\xbd\xe5\xb7\xb2\xe7\xbb\x8f\xe6\x9b\xb4\xe6\x96\xb0\xef\xbc\x8c\xe4\xbd\x86\xe5\xbd\x93\xe5\x89\x8d')"" Python ""$(zh '\xe5\x90\x8e\xe7\xab\xaf\xe8\xbf\x9b\xe7\xa8\x8b\xe4\xbb\x8d\xe6\x98\xaf\xe6\x97\xa7\xe4\xbb\xa3\xe7\xa0\x81\xef\xbc\x8c\xe6\x88\x96\xe8\x80\x85')"" systemd ""$(zh '\xe6\x8c\x87\xe5\x90\x91\xe4\xba\x86\xe5\x88\xab\xe7\x9a\x84\xe7\x9b\xae\xe5\xbd\x95\xe3\x80\x82')"
  echo "$(zh '\xe8\xaf\xb7\xe5\x9c\xa8\xe6\x9c\x8d\xe5\x8a\xa1\xe5\x99\xa8\xe6\x89\xa7\xe8\xa1\x8c\xe4\xb8\x8b\xe9\x9d\xa2\xe5\x91\xbd\xe4\xbb\xa4\xe7\xa1\xae\xe8\xae\xa4\xe5\xae\x9e\xe9\x99\x85\xe8\xbf\x90\xe8\xa1\x8c\xe8\xb7\xaf\xe5\xbe\x84\xef\xbc\x9a')"
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

admin_entry_path_local() {
  local py
  py="$(check_python_bin)"
  [[ -n "$py" ]] || return 1
  PYTHONPATH="$ROOT_DIR/backend" "$py" - <<'PY'
from app.quant.security import ensure_admin_entry_path
print(ensure_admin_entry_path())
PY
}

print_admin_entry_path() {
  local admin_path
  admin_path="$(admin_entry_path_local 2>/dev/null || true)"
  if [[ -n "$admin_path" ]]; then
    echo "后台入口路径：$admin_path"
  else
    warn "暂时无法读取后台入口路径；请确认依赖已安装并且 backend/data 可写。"
  fi
}

backend_feature_modules() {
  cat <<'EOF'
version	version	GET:/api/version
auth	auth	GET:/api/auth/status,POST:/api/auth/setup,POST:/api/auth/login,POST:/api/auth/register
frontend	frontend	GET:/api/front/public_snapshot,GET:/api/front/snapshot,GET:/api/front/profile,POST:/api/front/profile,GET:/api/front/trading_account,GET:/api/front/recommendations,GET:/api/front/daily_plan
admin	admin	GET:/api/admin/snapshot,POST:/api/admin/system/startup,POST:/api/admin/backup,GET:/api/admin/data/export,POST:/api/admin/data/import,GET:/api/admin/data/import/{job_id},GET:/api/admin/database/tables,GET:/api/admin/database/table/{table_name},POST:/api/admin/data/clear_sample_state,GET:/api/admin/access_logs,GET:/api/admin/frontend_users,POST:/api/admin/frontend_users,PATCH:/api/admin/frontend_users/{username},POST:/api/admin/frontend_users/{username}/password,POST:/api/admin/frontend_users/{username}/ban,POST:/api/admin/frontend_users/{username}/unban,DELETE:/api/admin/frontend_users/{username},POST:/api/admin/restart
jobs	jobs	GET:/api/jobs/status,GET:/api/jobs/logs,POST:/api/jobs/{job_name}/pause,POST:/api/jobs/{job_name}/resume,POST:/api/jobs/news/fetch,POST:/api/jobs/market/sync,POST:/api/jobs/ai/analyze,POST:/api/jobs/trading/run,POST:/api/jobs/strategy/replay,POST:/api/jobs/daily/run
data	data	GET:/api/data/coverage,POST:/api/data/kline/fill,GET:/api/data/lhb/status,POST:/api/data/lhb/sync,GET:/api/data/biying/status,POST:/api/data/biying/sync_intraday
quant	quant	GET:/api/quant/dashboard,GET:/api/quant/recommendations,GET:/api/quant/daily_plan,GET:/api/quant/timeline,GET:/api/quant/intraday_timeline,GET:/api/quant/backtest,POST:/api/quant/backtest,GET:/api/quant/trading_account,GET:/api/quant/portfolio,POST:/api/quant/run
strategy	strategy	GET:/api/quant/strategy_params,POST:/api/quant/strategy_params,POST:/api/quant/strategy_params/reset,POST:/api/quant/fit_strategy,GET:/api/quant/models,GET:/api/quant/model/backtest,POST:/api/quant/model/apply,GET:/api/quant/evolution/status,GET:/api/quant/evolution/trace,POST:/api/quant/evolve_strategy,POST:/api/quant/evolution/pause,POST:/api/quant/evolution/resume
ai	ai	GET:/api/ai/usage,GET:/api/ai/records,GET:/api/ai/failures
notify	notify	GET:/api/notifications/status,POST:/api/notifications/test
EOF
}

check_running_version() {
  section "$(zh '\xe5\xba\x94\xe7\x94\xa8\xe7\x89\x88\xe6\x9c\xac\xe9\xaa\x8c\xe8\xaf\x81')"
  if ! command -v curl >/dev/null 2>&1; then
    warn "$(zh '\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"" curl""$(zh '\xef\xbc\x8c\xe6\x97\xa0\xe6\xb3\x95\xe9\xaa\x8c\xe8\xaf\x81\xe7\x89\x88\xe6\x9c\xac')"
    return 0
  fi

  local version_file expected running_json py running_version
  expected="$(local_app_version)"
  version_file="$(mktemp)"
  if ! curl -fsS --max-time 8 "$(version_url)" -o "$version_file" 2>/dev/null; then
    rm -f "$version_file"
    error "$(zh '\xe5\xbd\x93\xe5\x89\x8d\xe5\x90\x8e\xe7\xab\xaf\xe7\xbc\xba\xe5\xb0\x91\xe7\x89\x88\xe6\x9c\xac\xe6\x8e\xa5\xe5\x8f\xa3\xef\xbc\x9a')""$(version_url)"
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

  echo "$(zh '\xe6\x9c\xac\xe5\x9c\xb0\xe4\xbb\xa3\xe7\xa0\x81\xe7\x89\x88\xe6\x9c\xac\xef\xbc\x9a')""$expected"
  echo "$(zh '\xe8\xbf\x90\xe8\xa1\x8c\xe5\x90\x8e\xe7\xab\xaf\xe7\x89\x88\xe6\x9c\xac\xef\xbc\x9a')""$running_version"
  if [[ "$expected" == "unknown" || "$running_version" == "unknown" ]]; then
    warn "$(zh '\xe7\x89\x88\xe6\x9c\xac\xe5\x8f\xb7\xe6\x97\xa0\xe6\xb3\x95\xe5\xae\x8c\xe6\x95\xb4\xe8\xaf\x86\xe5\x88\xab')"
    return 0
  fi
  if [[ "$expected" != "$running_version" ]]; then
    error "$(zh '\xe8\xbf\x90\xe8\xa1\x8c\xe5\x90\x8e\xe7\xab\xaf\xe7\x89\x88\xe6\x9c\xac\xe4\xb8\x8e\xe6\x9c\xac\xe5\x9c\xb0\xe4\xbb\xa3\xe7\xa0\x81\xe7\x89\x88\xe6\x9c\xac\xe4\xb8\x8d\xe4\xb8\x80\xe8\x87\xb4')"
    print_backend_route_help
    return 1
  fi
  success "$(zh '\xe5\x89\x8d\xe5\x90\x8e\xe7\xab\xaf\xe7\x89\x88\xe6\x9c\xac\xe4\xb8\x80\xe8\x87\xb4\xef\xbc\x9a')""v$running_version"
}

check_backend_feature_routes() {
  if ! command -v curl >/dev/null 2>&1; then
    warn "$(zh '\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"" curl""$(zh '\xef\xbc\x8c\xe6\x97\xa0\xe6\xb3\x95\xe9\xaa\x8c\xe8\xaf\x81\xe5\xbd\x93\xe5\x89\x8d\xe5\x90\x8e\xe7\xab\xaf\xe6\x8e\xa5\xe5\x8f\xa3\xe7\x89\x88\xe6\x9c\xac')"
    return 0
  fi

  local openapi_file
  openapi_file="$(mktemp)"
  if ! curl -fsS --max-time 8 "$(openapi_url)" -o "$openapi_file" 2>/dev/null; then
    rm -f "$openapi_file"
    error "$(zh '\xe6\x97\xa0\xe6\xb3\x95\xe8\xaf\xbb\xe5\x8f\x96\xe5\xbd\x93\xe5\x89\x8d\xe5\x90\x8e\xe7\xab\xaf\xe8\xb7\xaf\xe7\x94\xb1\xe8\xa1\xa8\xef\xbc\x9a')""$(openapi_url)"
    return 1
  fi

  local modules_file py result failed=0
  modules_file="$(mktemp)"
  backend_feature_modules > "$modules_file"
  py="$(check_python_bin)"
  if [[ -z "$py" ]]; then
    warn "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0')"" Python""$(zh '\xef\xbc\x8c\xe9\x80\x80\xe5\x9b\x9e\xe5\x9f\xba\xe7\xa1\x80\xe6\x8e\xa5\xe5\x8f\xa3')"" grep ""$(zh '\xe9\xaa\x8c\xe8\xaf\x81')"
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
        success "$label""$(zh '\xef\xbc\x9a')""$detail"
        ;;
      MISS)
        error "$label""$(zh '\xef\xbc\x9a\xe7\xbc\xba\xe5\xb0\x91')"" $detail"
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
        print(f"OK|{label}|{total} interfaces")
PY
)

  rm -f "$openapi_file" "$modules_file"
  if [[ "$failed" -ne 0 ]]; then
    print_backend_route_help
    return 1
  fi
  success "$(zh '\xe5\x90\x8e\xe7\xab\xaf\xe6\xa8\xa1\xe5\x9d\x97\xe5\x8c\x96\xe6\x8e\xa5\xe5\x8f\xa3\xe9\xaa\x8c\xe8\xaf\x81\xe9\x80\x9a\xe8\xbf\x87')"
}

verify_running_backend() {
  section "$(zh '\xe5\x90\x8e\xe7\xab\xaf\xe8\xbf\x90\xe8\xa1\x8c\xe9\xaa\x8c\xe8\xaf\x81')"
  if ! command -v curl >/dev/null 2>&1; then
    warn "$(zh '\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"" curl""$(zh '\xef\xbc\x8c\xe8\xb7\xb3\xe8\xbf\x87\xe5\x90\x8e\xe7\xab\xaf\xe7\x89\x88\xe6\x9c\xac\xe9\xaa\x8c\xe8\xaf\x81')"
    return 0
  fi
  if ! wait_for_api_ready; then
    error "$(zh '\xe9\x87\x8d\xe5\x90\xaf\xe5\x90\x8e')"" API ""$(zh '\xe6\xb2\xa1\xe6\x9c\x89\xe5\x9c\xa8')"" 20 ""$(zh '\xe7\xa7\x92\xe5\x86\x85\xe5\x93\x8d\xe5\xba\x94\xef\xbc\x9a')""$(api_url)"
    return 1
  fi
  check_running_version
  check_backend_feature_routes
  check_sqlite_schema
}
