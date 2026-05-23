#!/usr/bin/env bash
set -euo pipefail
if [[ "${QT_LOCALE_REEXEC:-0}" != "1" && "${LC_ALL:-}" != "C" ]]; then
  export QT_LOCALE_REEXEC=1
  export LC_ALL=C
  export LANG=C
  export PYTHONIOENCODING="${PYTHONIOENCODING:-UTF-8}"
  exec bash "$0" "$@"
fi
export LC_ALL=C
export LANG=C
export PYTHONIOENCODING="${PYTHONIOENCODING:-UTF-8}"

SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

usage() {
  cat <<EOF
涨停狙击手服务器命令

交互面板：
  qt                         打开服务器运维面板
  qt panel                   打开服务器运维面板

直接命令：
  qt install                 首次部署：安装依赖并注册 systemd 服务
  qt update                  一键更新：备份数据、拉取代码、更新依赖、自动迁移SQLite、重启服务
  qt version                 查看前后端版本并验证模块接口
  qt restart                 重启服务
  qt stop                    停止服务
  qt status                  查看服务状态、Git 版本、认证状态
  qt admin-path              查看或生成后台入口路径
  qt nginx-upload            修复 Nginx 上传大小和等待超时
  qt logs                    查看实时日志
  qt backup                  备份 backend/data
  qt restore <tar.gz>        从备份恢复 backend/data
  qt auth                    账号密码管理
  qt debug-status            查看临时调试通道状态
  qt debug-key               生成临时调试密钥和 .env 配置
  qt debug-on                生成临时调试密钥并写入 .env
  qt debug-off               关闭临时调试通道
  qt data-audit [--fix-permissions] 服务器数据安全体检
  qt architecture            项目架构体检
  qt clear-sample            清理样例持仓
  qt fill-kline              补齐有新闻事件股票的日K数据
  qt sync-lhb                拉取龙虎榜席位数据
  qt migrate                 强制把 backend/data 里的 JSON/CSV 合并进 SQLite
  qt scan                    GitHub 上传前安全扫描
  qt doctor                  部署环境检查
  qt help                    显示帮助

说明：
  服务器安装后 /usr/local/bin/qt 会指向 scripts/qt.sh，所以直接输入 qt 会进入面板。
  根目录 bash qt.sh 仍然保留为一键更新入口。
EOF
}

script_path() {
  echo "$SCRIPT_DIR/$1"
}

run_script() {
  local title="$1"
  local script="$2"
  shift 2
  [[ -f "$script" ]] || die "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe8\x84\x9a\xe6\x9c\xac\xef\xbc\x9a')""$script"
  section "$title"
  info "$(zh '\xe6\x89\xa7\xe8\xa1\x8c\xef\xbc\x9a')""bash ${script#$ROOT_DIR/} $*"
  bash "$script" "$@"
}

systemd_unit_exists() {
  command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"
}

api_url() {
  echo "http://127.0.0.1:${PORT}/api/auth/status"
}

run_security_scan() {
  if [[ -x "$(venv_python)" ]]; then
    "$(venv_python)" "$ROOT_DIR/scripts/security_scan.py"
  elif command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    "$PYTHON_BIN" "$ROOT_DIR/scripts/security_scan.py"
  else
    error "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0')"" Python""$(zh '\xef\xbc\x8c\xe6\x97\xa0\xe6\xb3\x95\xe6\x89\xa7\xe8\xa1\x8c\xe5\xae\x89\xe5\x85\xa8\xe6\x89\xab\xe6\x8f\x8f\xe3\x80\x82\xe8\xaf\xb7\xe5\xae\x89\xe8\xa3\x85')"" python3""$(zh '\xef\xbc\x8c\xe6\x88\x96\xe5\x9c\xa8')"" .env ""$(zh '\xe4\xb8\xad\xe8\xae\xbe\xe7\xbd\xae')"" PYTHON_BIN"
    return 1
  fi
}

run_data_audit() {
  run_python_tool "服务器数据安全体检" "$ROOT_DIR/scripts/server_data_audit.py" "$@"
}

run_architecture_report() {
  run_python_tool "项目架构体检" "$ROOT_DIR/scripts/architecture_report.py" "$@"
}

run_auth_tool() {
  local auth_tool="$ROOT_DIR/scripts/manage_auth.py"
  [[ -f "$auth_tool" ]] || die "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe8\xb4\xa6\xe5\x8f\xb7\xe7\xae\xa1\xe7\x90\x86\xe5\xb7\xa5\xe5\x85\xb7\xef\xbc\x9a')""$auth_tool"
  if [[ -x "$(venv_python)" ]]; then
    "$(venv_python)" "$auth_tool" "$@"
  elif command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    "$PYTHON_BIN" "$auth_tool" "$@"
  else
    die "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0')"" Python""$(zh '\xef\xbc\x8c\xe6\x97\xa0\xe6\xb3\x95\xe7\xae\xa1\xe7\x90\x86\xe8\xb4\xa6\xe5\x8f\xb7\xe5\xaf\x86\xe7\xa0\x81')"
  fi
}

cmd_debug_key() {
  require_project_root
  run_python_tool "生成临时调试密钥" "$ROOT_DIR/scripts/generate_debug_key.py" "$@"
}

cmd_debug_status() {
  require_project_root
  run_python_tool "查看临时调试通道状态" "$ROOT_DIR/scripts/generate_debug_key.py" --status
}

cmd_debug_on() {
  require_project_root
  run_python_tool "生成并写入临时调试密钥" "$ROOT_DIR/scripts/generate_debug_key.py" --write-env
}

cmd_debug_off() {
  require_project_root
  run_python_tool "关闭临时调试通道" "$ROOT_DIR/scripts/generate_debug_key.py" --disable
}

run_python_tool() {
  local title="$1"
  local tool="$2"
  shift 2
  [[ -f "$tool" ]] || die "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe5\xb7\xa5\xe5\x85\xb7\xef\xbc\x9a')""$tool"
  section "$title"
  if [[ -x "$(venv_python)" ]]; then
    PYTHONPATH="$ROOT_DIR/backend" "$(venv_python)" "$tool" "$@"
  elif command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHONPATH="$ROOT_DIR/backend" "$PYTHON_BIN" "$tool" "$@"
  else
    die "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0')"" Python""$(zh '\xef\xbc\x8c\xe6\x97\xa0\xe6\xb3\x95\xe6\x89\xa7\xe8\xa1\x8c')""$title"
  fi
}

git_ref() {
  if [[ -d "$ROOT_DIR/.git" ]] && command -v git >/dev/null 2>&1; then
    local branch commit
    branch="$(git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    commit="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || true)"
    if [[ -n "$branch$commit" ]]; then
      echo "${branch:-unknown}@${commit:-unknown}"
    else
      echo "$(zh '\xe6\x9c\xaa\xe8\xaf\x86\xe5\x88\xab')"
    fi
  else
    echo "$(zh '\xe6\x9c\xaa\xe5\x90\xaf\xe7\x94\xa8')"" Git"
  fi
}

process_status() {
  local pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      echo "nohup ""$(zh '\xe8\xbf\x9b\xe7\xa8\x8b\xe8\xbf\x90\xe8\xa1\x8c\xe4\xb8\xad\xef\xbc\x8c')""pid=$pid"
    else
      echo "pid ""$(zh '\xe6\x96\x87\xe4\xbb\xb6\xe5\xad\x98\xe5\x9c\xa8\xef\xbc\x8c\xe4\xbd\x86\xe8\xbf\x9b\xe7\xa8\x8b\xe6\x9c\xaa\xe8\xbf\x90\xe8\xa1\x8c')"
    fi
  else
    echo "$(zh '\xe6\x9c\xaa\xe6\x89\xbe\xe5\x88\xb0')"" nohup pid ""$(zh '\xe6\x96\x87\xe4\xbb\xb6')"
  fi
}

cmd_status() {
  require_project_root
  section "$(zh '\xe9\xa1\xb9\xe7\x9b\xae\xe7\x8a\xb6\xe6\x80\x81')"
  echo "$(zh '\xe9\xa1\xb9\xe7\x9b\xae\xe7\x9b\xae\xe5\xbd\x95\xef\xbc\x9a')""$ROOT_DIR"
  echo "$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe5\x90\x8d\xe7\xa7\xb0\xef\xbc\x9a')""$SERVICE_NAME"
  echo "$(zh '\xe7\x9b\x91\xe5\x90\xac\xe5\x9c\xb0\xe5\x9d\x80\xef\xbc\x9a')""$HOST:$PORT"
  echo "$(zh '\xe5\xba\x94\xe7\x94\xa8\xe7\x89\x88\xe6\x9c\xac\xef\xbc\x9a')""$(local_app_version)"
  echo "$(zh '\xe6\x95\xb0\xe6\x8d\xae\xe7\x9b\xae\xe5\xbd\x95\xef\xbc\x9a')""$ROOT_DIR/backend/data"
  echo "Git ""$(zh '\xe7\x89\x88\xe6\x9c\xac\xef\xbc\x9a')""$(git_ref)"
  if [[ -f "$ROOT_DIR/.env" ]]; then
    echo ".env""$(zh '\xef\xbc\x9a\xe5\xb7\xb2\xe5\xad\x98\xe5\x9c\xa8')"
  else
    echo ".env""$(zh '\xef\xbc\x9a\xe6\x9c\xaa\xe5\x88\x9b\xe5\xbb\xba\xef\xbc\x8c\xe5\x8f\xaf\xe4\xbb\x8e')"" .env.example ""$(zh '\xe5\xa4\x8d\xe5\x88\xb6')"
  fi

  section "$(zh '\xe8\xb4\xa6\xe5\x8f\xb7\xe7\x8a\xb6\xe6\x80\x81')"
  run_auth_tool status || true
  print_admin_entry_path

  section "$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe7\x8a\xb6\xe6\x80\x81')"
  if systemd_unit_exists; then
    info "$(zh '\xe6\xa3\x80\xe6\xb5\x8b\xe5\x88\xb0')"" systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xef\xbc\x9a')""${SERVICE_NAME}.service"
    systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1 \
      && success "systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe8\xbf\x90\xe8\xa1\x8c\xe4\xb8\xad')" \
      || warn "systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xe6\x9c\xaa\xe8\xbf\x90\xe8\xa1\x8c')"
    systemctl show "$SERVICE_NAME" -p MainPID -p ExecStart -p WorkingDirectory --no-pager || true
    systemctl status "$SERVICE_NAME" --no-pager -l || true
  else
    warn "$(zh '\xe6\x9c\xaa\xe6\xa3\x80\xe6\xb5\x8b\xe5\x88\xb0')"" systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xef\xbc\x8c\xe6\xa3\x80\xe6\x9f\xa5')"" nohup ""$(zh '\xe8\xbf\x9b\xe7\xa8\x8b')"
    process_status
  fi

  section "API ""$(zh '\xe5\x81\xa5\xe5\xba\xb7\xe6\xa3\x80\xe6\x9f\xa5')"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 5 "$(api_url)"; then
      echo
      success "API ""$(zh '\xe5\x8f\xaf\xe8\xae\xbf\xe9\x97\xae\xef\xbc\x9a')""$(api_url)"
      verify_running_backend || true
    else
      echo
      warn "API ""$(zh '\xe6\x9a\x82\xe4\xb8\x8d\xe5\x8f\xaf\xe8\xae\xbf\xe9\x97\xae\xef\xbc\x9a')""$(api_url)"
    fi
  else
    warn "$(zh '\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"" curl""$(zh '\xef\xbc\x8c\xe8\xb7\xb3\xe8\xbf\x87')"" API ""$(zh '\xe6\xa3\x80\xe6\x9f\xa5')"
  fi
}

cmd_version() {
  require_project_root
  echo "$(zh '\xe6\x9c\xac\xe5\x9c\xb0\xe5\xba\x94\xe7\x94\xa8\xe7\x89\x88\xe6\x9c\xac\xef\xbc\x9a')""$(local_app_version)"
  echo "Git ""$(zh '\xe7\x89\x88\xe6\x9c\xac\xef\xbc\x9a')""$(git_ref)"
  verify_running_backend
}

cmd_doctor() {
  local failed=0
  section "$(zh '\xe9\x83\xa8\xe7\xbd\xb2\xe7\x8e\xaf\xe5\xa2\x83\xe6\xa3\x80\xe6\x9f\xa5')"

  [[ -d "$ROOT_DIR/backend/app" ]] && success "$(zh '\xe5\x90\x8e\xe7\xab\xaf\xe7\x9b\xae\xe5\xbd\x95\xe5\xad\x98\xe5\x9c\xa8')" || { error "$(zh '\xe7\xbc\xba\xe5\xb0\x91')"" backend/app"; failed=1; }
  [[ -d "$ROOT_DIR/frontend" ]] && success "$(zh '\xe5\x89\x8d\xe7\xab\xaf\xe7\x9b\xae\xe5\xbd\x95\xe5\xad\x98\xe5\x9c\xa8')" || { error "$(zh '\xe7\xbc\xba\xe5\xb0\x91')"" frontend"; failed=1; }
  [[ -f "$ROOT_DIR/backend/requirements.txt" ]] && success "$(zh '\xe4\xbe\x9d\xe8\xb5\x96\xe6\x96\x87\xe4\xbb\xb6\xe5\xad\x98\xe5\x9c\xa8')" || { error "$(zh '\xe7\xbc\xba\xe5\xb0\x91')"" backend/requirements.txt"; failed=1; }
  [[ -f "$ROOT_DIR/.env" ]] && success ".env ""$(zh '\xe5\xb7\xb2\xe5\x88\x9b\xe5\xbb\xba')" || warn ".env ""$(zh '\xe6\x9c\xaa\xe5\x88\x9b\xe5\xbb\xba\xef\xbc\x8c\xe9\xa6\x96\xe6\xac\xa1\xe9\x83\xa8\xe7\xbd\xb2\xe4\xbc\x9a\xe4\xbb\x8e')"" .env.example ""$(zh '\xe5\xa4\x8d\xe5\x88\xb6')"
  [[ -f "$ROOT_DIR/.env.example" ]] && success ".env.example ""$(zh '\xe5\xad\x98\xe5\x9c\xa8')" || warn "$(zh '\xe7\xbc\xba\xe5\xb0\x91')"" .env.example"

  if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    success "Python ""$(zh '\xe5\x8f\xaf\xe7\x94\xa8\xef\xbc\x9a')""$($PYTHON_BIN --version 2>&1)"
  else
    error "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0')"" Python""$(zh '\xef\xbc\x9a')""$PYTHON_BIN"
    failed=1
  fi

  command -v git >/dev/null 2>&1 && success "Git ""$(zh '\xe5\x8f\xaf\xe7\x94\xa8\xef\xbc\x9a')""$(git --version)" || warn "$(zh '\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"" Git""$(zh '\xef\xbc\x8c')""update ""$(zh '\xe4\xbc\x9a\xe8\xb7\xb3\xe8\xbf\x87')"" git pull"
  command -v curl >/dev/null 2>&1 && success "curl ""$(zh '\xe5\x8f\xaf\xe7\x94\xa8')" || warn "$(zh '\xe6\x9c\xaa\xe5\xae\x89\xe8\xa3\x85')"" curl""$(zh '\xef\xbc\x8c')""status ""$(zh '\xe6\x97\xa0\xe6\xb3\x95\xe6\xa3\x80\xe6\x9f\xa5')"" API"
  command -v systemctl >/dev/null 2>&1 && success "systemd ""$(zh '\xe5\x8f\xaf\xe7\x94\xa8')" || warn "$(zh '\xe6\x9c\xaa\xe6\xa3\x80\xe6\xb5\x8b\xe5\x88\xb0')"" systemd""$(zh '\xef\xbc\x8c\xe5\xb0\x86\xe4\xbd\xbf\xe7\x94\xa8')"" nohup ""$(zh '\xe6\x96\xb9\xe5\xbc\x8f\xe8\xbf\x90\xe8\xa1\x8c')"

  section "$(zh '\xe8\x84\x9a\xe6\x9c\xac\xe6\x9d\x83\xe9\x99\x90')"
  chmod +x "$ROOT_DIR/qt.sh" "$ROOT_DIR/scripts/"*.sh 2>/dev/null || warn "$(zh '\xe6\x97\xa0\xe6\xb3\x95\xe4\xbf\xae\xe6\x94\xb9\xe8\x84\x9a\xe6\x9c\xac\xe6\x89\xa7\xe8\xa1\x8c\xe6\x9d\x83\xe9\x99\x90\xef\xbc\x8c\xe5\x90\x8e\xe7\xbb\xad\xe4\xbc\x9a\xe7\x94\xa8')"" bash ""$(zh '\xe6\x98\xbe\xe5\xbc\x8f\xe6\x89\xa7\xe8\xa1\x8c')"
  for file in "$ROOT_DIR/qt.sh" "$ROOT_DIR/scripts/"*.sh; do
    [[ -f "$file" ]] || continue
    [[ -x "$file" ]] && success "${file#$ROOT_DIR/} ""$(zh '\xe5\x8f\xaf\xe6\x89\xa7\xe8\xa1\x8c')" || warn "${file#$ROOT_DIR/} ""$(zh '\xe4\xb8\x8d\xe5\x8f\xaf\xe6\x89\xa7\xe8\xa1\x8c\xef\xbc\x8c\xe4\xbd\x86\xe5\x8f\xaf\xe9\x80\x9a\xe8\xbf\x87')"" bash ""$(zh '\xe8\xbf\x90\xe8\xa1\x8c')"
  done

  section "$(zh '\xe5\xae\x89\xe5\x85\xa8\xe6\x89\xab\xe6\x8f\x8f')"
  run_security_scan || failed=1

  if [[ "$failed" -eq 0 ]]; then
    success "$(zh '\xe7\x8e\xaf\xe5\xa2\x83\xe6\xa3\x80\xe6\x9f\xa5\xe5\xae\x8c\xe6\x88\x90\xef\xbc\x8c\xe6\x9c\xaa\xe5\x8f\x91\xe7\x8e\xb0\xe9\x98\xbb\xe6\x96\xad\xe9\xa1\xb9')"
  else
    die "$(zh '\xe7\x8e\xaf\xe5\xa2\x83\xe6\xa3\x80\xe6\x9f\xa5\xe5\x8f\x91\xe7\x8e\xb0\xe9\x98\xbb\xe6\x96\xad\xe9\xa1\xb9\xef\xbc\x8c\xe8\xaf\xb7\xe5\x85\x88\xe5\xa4\x84\xe7\x90\x86\xe4\xb8\x8a\xe9\x9d\xa2\xe7\x9a\x84\xe9\x94\x99\xe8\xaf\xaf')"
  fi
}

cmd_stop() {
  require_project_root
  section "$(zh '\xe5\x81\x9c\xe6\xad\xa2\xe6\x9c\x8d\xe5\x8a\xa1')"
  if systemd_unit_exists; then
    if [[ "$(id -u)" -eq 0 ]]; then
      systemctl stop "$SERVICE_NAME"
    else
      sudo systemctl stop "$SERVICE_NAME"
    fi
    success "$(zh '\xe5\xb7\xb2\xe5\x81\x9c\xe6\xad\xa2')"" systemd ""$(zh '\xe6\x9c\x8d\xe5\x8a\xa1\xef\xbc\x9a')""$SERVICE_NAME"
    return
  fi

  local pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid"
      success "$(zh '\xe5\xb7\xb2\xe5\x81\x9c\xe6\xad\xa2')"" ${SERVICE_NAME}""$(zh '\xef\xbc\x8c')""pid=$pid"
    else
      warn "${SERVICE_NAME} ""$(zh '\xe8\xbf\x9b\xe7\xa8\x8b\xe6\x9c\xaa\xe8\xbf\x90\xe8\xa1\x8c')"
    fi
  else
    warn "$(zh '\xe6\x9c\xaa\xe6\x89\xbe\xe5\x88\xb0')"" pid ""$(zh '\xe6\x96\x87\xe4\xbb\xb6\xef\xbc\x9a')""$pid_file"
  fi
}

cmd_logs() {
  require_project_root
  section "$(zh '\xe5\xae\x9e\xe6\x97\xb6\xe6\x97\xa5\xe5\xbf\x97')"
  if systemd_unit_exists; then
    info "$(zh '\xe6\x8c\x89')"" Ctrl+C ""$(zh '\xe9\x80\x80\xe5\x87\xba\xe6\x97\xa5\xe5\xbf\x97')"
    journalctl -u "$SERVICE_NAME" -f
  else
    local out_log="$ROOT_DIR/backend/data/${SERVICE_NAME}.out.log"
    local err_log="$ROOT_DIR/backend/data/${SERVICE_NAME}.err.log"
    [[ -f "$out_log" || -f "$err_log" ]] || die "$(zh '\xe6\x9c\xaa\xe6\x89\xbe\xe5\x88\xb0\xe6\x97\xa5\xe5\xbf\x97\xe6\x96\x87\xe4\xbb\xb6\xef\xbc\x8c\xe8\xaf\xb7\xe5\x85\x88\xe5\x90\xaf\xe5\x8a\xa8\xe6\x9c\x8d\xe5\x8a\xa1')"
    info "$(zh '\xe6\x8c\x89')"" Ctrl+C ""$(zh '\xe9\x80\x80\xe5\x87\xba\xe6\x97\xa5\xe5\xbf\x97')"
    tail -f "$out_log" "$err_log"
  fi
}

cmd_auth() {
  while true; do
    clear || true
    cat <<EOF
涨停狙击手 - 账号密码管理

认证文件：$ROOT_DIR/backend/data/auth.json

1) 查看账号状态
2) 初始化/重建前后台账号
3) 修改后台管理员账号或密码
4) 修改前台交易终端账号或密码
5) 删除账号配置，回到网页首次初始化
0) 返回主面板

EOF
    read -r -p "$(zh '\xe8\xaf\xb7\xe9\x80\x89\xe6\x8b\xa9\xe6\x93\x8d\xe4\xbd\x9c\xef\xbc\x9a')" choice
    choice="${choice//$'\r'/}"
    case "$choice" in
      1)
        run_auth_tool status
        panel_pause
        ;;
      2)
        run_auth_tool init
        panel_pause
        ;;
      3)
        run_auth_tool set --scope admin
        panel_pause
        ;;
      4)
        run_auth_tool set --scope frontend
        panel_pause
        ;;
      5)
        run_auth_tool delete
        panel_pause
        ;;
      0|q|Q)
        return
        ;;
      *)
        warn "$(zh '\xe6\x97\xa0\xe6\x95\x88\xe9\x80\x89\xe6\x8b\xa9\xef\xbc\x9a')""$choice"
        panel_pause
        ;;
    esac
  done
}

panel_pause() {
  echo
  read -r -p "$(zh '\xe6\x8c\x89')"" Enter ""$(zh '\xe8\xbf\x94\xe5\x9b\x9e')""..." _
}

panel_run() {
  local label="$1"
  shift
  clear || true
  section "$label"
  set +e
  "$@"
  local code=$?
  set -e
  if [[ "$code" -eq 0 ]]; then
    success "$label ""$(zh '\xe5\xae\x8c\xe6\x88\x90')"
  else
    warn "$label ""$(zh '\xe9\x80\x80\xe5\x87\xba\xe7\xa0\x81\xef\xbc\x9a')""$code"
  fi
  panel_pause
}

cmd_panel() {
  require_project_root
  while true; do
    clear || true
    cat <<EOF
涨停狙击手 - 服务器运维面板

项目：$ROOT_DIR
服务：$SERVICE_NAME
端口：$PORT
版本：$(git_ref)

1) 一键更新部署
2) 重启服务
3) 停止服务
4) 查看服务状态
5) 查看实时日志
6) 备份数据
7) 恢复数据
8) 账号密码管理
9) 清理样例持仓
10) 补齐缺失日K
11) 拉取龙虎榜席位
12) 版本和模块验证
13) GitHub 上传前安全扫描
14) 服务器数据安全体检
15) 项目架构体检
16) 部署环境检查
17) 查看后台入口路径
18) 修复 Nginx 上传和超时限制
19) 查看调试通道状态
20) 生成临时调试密钥（只显示，不写入）
21) 生成并写入调试密钥
22) 关闭临时调试通道
0) 退出

EOF
    read -r -p "$(zh '\xe8\xaf\xb7\xe9\x80\x89\xe6\x8b\xa9\xe6\x93\x8d\xe4\xbd\x9c\xef\xbc\x9a')" choice
    choice="${choice//$'\r'/}"
    case "$choice" in
      1) panel_run "$(zh '\xe4\xb8\x80\xe9\x94\xae\xe6\x9b\xb4\xe6\x96\xb0\xe9\x83\xa8\xe7\xbd\xb2')" bash "$SCRIPT_DIR/qt.sh" update ;;
      2) panel_run "$(zh '\xe9\x87\x8d\xe5\x90\xaf\xe6\x9c\x8d\xe5\x8a\xa1')" bash "$SCRIPT_DIR/qt.sh" restart ;;
      3) panel_run "$(zh '\xe5\x81\x9c\xe6\xad\xa2\xe6\x9c\x8d\xe5\x8a\xa1')" bash "$SCRIPT_DIR/qt.sh" stop ;;
      4) panel_run "$(zh '\xe6\x9f\xa5\xe7\x9c\x8b\xe6\x9c\x8d\xe5\x8a\xa1\xe7\x8a\xb6\xe6\x80\x81')" bash "$SCRIPT_DIR/qt.sh" status ;;
      5)
        clear || true
        bash "$SCRIPT_DIR/qt.sh" logs
        panel_pause
        ;;
      6) panel_run "$(zh '\xe5\xa4\x87\xe4\xbb\xbd\xe6\x95\xb0\xe6\x8d\xae')" bash "$SCRIPT_DIR/qt.sh" backup ;;
      7)
        read -r -p "$(zh '\xe8\xaf\xb7\xe8\xbe\x93\xe5\x85\xa5\xe5\xa4\x87\xe4\xbb\xbd')"" tar.gz ""$(zh '\xe8\xb7\xaf\xe5\xbe\x84\xef\xbc\x9a')" backup_file
        backup_file="${backup_file//$'\r'/}"
        [[ -n "$backup_file" ]] && panel_run "$(zh '\xe6\x81\xa2\xe5\xa4\x8d\xe6\x95\xb0\xe6\x8d\xae')" bash "$SCRIPT_DIR/qt.sh" restore "$backup_file"
        ;;
      8) cmd_auth ;;
      9) panel_run "$(zh '\xe6\xb8\x85\xe7\x90\x86\xe6\xa0\xb7\xe4\xbe\x8b\xe6\x8c\x81\xe4\xbb\x93')" bash "$SCRIPT_DIR/qt.sh" clear-sample ;;
      10) panel_run "$(zh '\xe8\xa1\xa5\xe9\xbd\x90\xe7\xbc\xba\xe5\xa4\xb1\xe6\x97\xa5')""K" bash "$SCRIPT_DIR/qt.sh" fill-kline ;;
      11) panel_run "$(zh '\xe6\x8b\x89\xe5\x8f\x96\xe9\xbe\x99\xe8\x99\x8e\xe6\xa6\x9c\xe5\xb8\xad\xe4\xbd\x8d')" bash "$SCRIPT_DIR/qt.sh" sync-lhb ;;
      12) panel_run "$(zh '\xe7\x89\x88\xe6\x9c\xac\xe5\x92\x8c\xe6\xa8\xa1\xe5\x9d\x97\xe9\xaa\x8c\xe8\xaf\x81')" bash "$SCRIPT_DIR/qt.sh" version ;;
      13) panel_run "$(zh '\xe5\xae\x89\xe5\x85\xa8\xe6\x89\xab\xe6\x8f\x8f')" bash "$SCRIPT_DIR/qt.sh" scan ;;
      14) panel_run "服务器数据安全体检" bash "$SCRIPT_DIR/qt.sh" data-audit ;;
      15) panel_run "项目架构体检" bash "$SCRIPT_DIR/qt.sh" architecture ;;
      16) panel_run "$(zh '\xe9\x83\xa8\xe7\xbd\xb2\xe7\x8e\xaf\xe5\xa2\x83\xe6\xa3\x80\xe6\x9f\xa5')" bash "$SCRIPT_DIR/qt.sh" doctor ;;
      17) panel_run "查看后台入口路径" bash "$SCRIPT_DIR/qt.sh" admin-path ;;
      18) panel_run "修复 Nginx 上传和超时限制" bash "$SCRIPT_DIR/qt.sh" nginx-upload ;;
      19) panel_run "查看调试通道状态" bash "$SCRIPT_DIR/qt.sh" debug-status ;;
      20) panel_run "生成临时调试密钥" bash "$SCRIPT_DIR/qt.sh" debug-key ;;
      21) panel_run "生成并写入调试密钥" bash "$SCRIPT_DIR/qt.sh" debug-on ;;
      22) panel_run "关闭临时调试通道" bash "$SCRIPT_DIR/qt.sh" debug-off ;;
      0|q|Q)
        echo "$(zh '\xe5\xb7\xb2\xe9\x80\x80\xe5\x87\xba\xe8\xbf\x90\xe7\xbb\xb4\xe9\x9d\xa2\xe6\x9d\xbf')"
        exit 0
        ;;
      *)
        warn "$(zh '\xe6\x97\xa0\xe6\x95\x88\xe9\x80\x89\xe6\x8b\xa9\xef\xbc\x9a')""$choice"
        panel_pause
        ;;
    esac
  done
}

cmd_clear_sample_state() {
  require_project_root
  local tool="$ROOT_DIR/scripts/clear_sample_state.py"
  [[ -f "$tool" ]] || die "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe6\xa0\xb7\xe4\xbe\x8b\xe6\x8c\x81\xe4\xbb\x93\xe6\xb8\x85\xe7\x90\x86\xe5\xb7\xa5\xe5\x85\xb7\xef\xbc\x9a')""$tool"
  section "$(zh '\xe6\xb8\x85\xe7\x90\x86\xe6\xa0\xb7\xe4\xbe\x8b\xe6\x8c\x81\xe4\xbb\x93')"
  if [[ -x "$(venv_python)" ]]; then
    PYTHONPATH="$ROOT_DIR/backend" "$(venv_python)" "$tool"
  elif command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHONPATH="$ROOT_DIR/backend" "$PYTHON_BIN" "$tool"
  else
    die "$(zh '\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0')"" Python""$(zh '\xef\xbc\x8c\xe6\x97\xa0\xe6\xb3\x95\xe6\xb8\x85\xe7\x90\x86\xe6\xa0\xb7\xe4\xbe\x8b\xe6\x8c\x81\xe4\xbb\x93')"
  fi
}

cmd_fill_kline() {
  require_project_root
  run_python_tool "$(zh '\xe8\xa1\xa5\xe9\xbd\x90\xe7\xbc\xba\xe5\xa4\xb1\xe6\x97\xa5')""K" "$ROOT_DIR/scripts/fill_kline.py" "$@"
}

cmd_sync_lhb() {
  require_project_root
  run_python_tool "$(zh '\xe6\x8b\x89\xe5\x8f\x96\xe9\xbe\x99\xe8\x99\x8e\xe6\xa6\x9c\xe5\xb8\xad\xe4\xbd\x8d')" "$ROOT_DIR/scripts/sync_lhb.py" "$@"
}

cmd="${1:-panel}"
case "$cmd" in
  panel|menu)
    cmd_panel
    ;;
  install|deploy|init)
    run_script "$(zh '\xe9\xa6\x96\xe6\xac\xa1\xe9\x83\xa8\xe7\xbd\xb2')" "$(script_path install_server.sh)"
    ;;
  update|upgrade|up)
    run_script "$(zh '\xe4\xb8\x80\xe9\x94\xae\xe6\x9b\xb4\xe6\x96\xb0')" "$(script_path update_server.sh)"
    ;;
  restart|start|reload)
    run_script "$(zh '\xe9\x87\x8d\xe5\x90\xaf\xe6\x9c\x8d\xe5\x8a\xa1')" "$(script_path restart_server.sh)"
    ;;
  stop)
    cmd_stop
    ;;
  status|ps)
    cmd_status
    ;;
  admin-path|admin-entry)
    require_project_root
    section "后台入口"
    print_admin_entry_path
    ;;
  nginx-upload|fix-nginx|nginx)
    require_project_root
    ensure_nginx_upload_limit
    ;;
  version|verify)
    cmd_version
    ;;
  logs|log)
    cmd_logs
    ;;
  backup|bak)
    run_script "$(zh '\xe5\xa4\x87\xe4\xbb\xbd\xe6\x95\xb0\xe6\x8d\xae')" "$(script_path backup_data.sh)"
    ;;
  restore)
    [[ $# -ge 2 ]] || die "restore ""$(zh '\xe9\x9c\x80\xe8\xa6\x81\xe4\xbc\xa0\xe5\x85\xa5\xe5\xa4\x87\xe4\xbb\xbd')"" tar.gz ""$(zh '\xe6\x96\x87\xe4\xbb\xb6\xe8\xb7\xaf\xe5\xbe\x84')"
    run_script "$(zh '\xe6\x81\xa2\xe5\xa4\x8d\xe6\x95\xb0\xe6\x8d\xae')" "$(script_path restore_data.sh)" "$2"
    ;;
  auth|user|users|password|passwd)
    cmd_auth
    ;;
  debug-status|debug-info|diagnostic-status)
    cmd_debug_status
    ;;
  debug-key|debug-token|diagnostic-key)
    cmd_debug_key "${@:2}"
    ;;
  debug-on|debug-enable|enable-debug)
    cmd_debug_on
    ;;
  debug-off|debug-disable|disable-debug)
    cmd_debug_off
    ;;
  data-audit|audit-data|server-data-audit)
    require_project_root
    run_data_audit "${@:2}"
    ;;
  architecture|arch|architecture-report)
    require_project_root
    run_architecture_report "${@:2}"
    ;;
  clear-sample|clean-sample|sample)
    cmd_clear_sample_state
    ;;
  fill-kline|kline|daily-kline)
    cmd_fill_kline "${@:2}"
    ;;
  sync-lhb|lhb|lhb-sync)
    cmd_sync_lhb "${@:2}"
    ;;
  migrate|sqlite-migrate|migrate-sqlite)
    require_project_root
    QT_FORCE_AUTO_MIGRATE=1 auto_migrate_sqlite
    ;;
  scan|security)
    section "GitHub ""$(zh '\xe4\xb8\x8a\xe4\xbc\xa0\xe5\x89\x8d\xe5\xae\x89\xe5\x85\xa8\xe6\x89\xab\xe6\x8f\x8f')"
    run_security_scan
    ;;
  doctor|check)
    cmd_doctor
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    error "$(zh '\xe6\x9c\xaa\xe7\x9f\xa5\xe5\x91\xbd\xe4\xbb\xa4\xef\xbc\x9a')""$cmd"
    usage >&2
    exit 2
    ;;
esac
