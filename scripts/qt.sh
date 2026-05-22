#!/usr/bin/env bash
set -euo pipefail

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
  qt update                  一键更新：备份数据、拉取代码、更新依赖、重启服务
  qt restart                 重启服务
  qt stop                    停止服务
  qt status                  查看服务状态、Git 版本、认证状态
  qt logs                    查看实时日志
  qt backup                  备份 backend/data
  qt restore <tar.gz>        从备份恢复 backend/data
  qt auth                    账号密码管理
  qt clear-sample            清理样例持仓
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
  [[ -f "$script" ]] || die "找不到脚本：$script"
  section "$title"
  info "执行：bash ${script#$ROOT_DIR/} $*"
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
    error "找不到 Python，无法执行安全扫描。请安装 python3，或在 .env 中设置 PYTHON_BIN"
    return 1
  fi
}

run_auth_tool() {
  local auth_tool="$ROOT_DIR/scripts/manage_auth.py"
  [[ -f "$auth_tool" ]] || die "找不到账号管理工具：$auth_tool"
  if [[ -x "$(venv_python)" ]]; then
    "$(venv_python)" "$auth_tool" "$@"
  elif command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    "$PYTHON_BIN" "$auth_tool" "$@"
  else
    die "找不到 Python，无法管理账号密码"
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
      echo "未识别"
    fi
  else
    echo "未启用 Git"
  fi
}

process_status() {
  local pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      echo "nohup 进程运行中，pid=$pid"
    else
      echo "pid 文件存在，但进程未运行"
    fi
  else
    echo "未找到 nohup pid 文件"
  fi
}

cmd_status() {
  require_project_root
  section "项目状态"
  echo "项目目录：$ROOT_DIR"
  echo "服务名称：$SERVICE_NAME"
  echo "监听地址：$HOST:$PORT"
  echo "数据目录：$ROOT_DIR/backend/data"
  echo "Git 版本：$(git_ref)"
  if [[ -f "$ROOT_DIR/.env" ]]; then
    echo ".env：已存在"
  else
    echo ".env：未创建，可从 .env.example 复制"
  fi

  section "账号状态"
  run_auth_tool status || true

  section "服务状态"
  if systemd_unit_exists; then
    info "检测到 systemd 服务：${SERVICE_NAME}.service"
    systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1 \
      && success "systemd 服务运行中" \
      || warn "systemd 服务未运行"
    systemctl show "$SERVICE_NAME" -p MainPID -p ExecStart -p WorkingDirectory --no-pager || true
    systemctl status "$SERVICE_NAME" --no-pager -l || true
  else
    warn "未检测到 systemd 服务，检查 nohup 进程"
    process_status
  fi

  section "API 健康检查"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 5 "$(api_url)"; then
      echo
      success "API 可访问：$(api_url)"
      check_backend_feature_routes || true
    else
      echo
      warn "API 暂不可访问：$(api_url)"
    fi
  else
    warn "未安装 curl，跳过 API 检查"
  fi
}

cmd_doctor() {
  local failed=0
  section "部署环境检查"

  [[ -d "$ROOT_DIR/backend/app" ]] && success "后端目录存在" || { error "缺少 backend/app"; failed=1; }
  [[ -d "$ROOT_DIR/frontend" ]] && success "前端目录存在" || { error "缺少 frontend"; failed=1; }
  [[ -f "$ROOT_DIR/backend/requirements.txt" ]] && success "依赖文件存在" || { error "缺少 backend/requirements.txt"; failed=1; }
  [[ -f "$ROOT_DIR/.env" ]] && success ".env 已创建" || warn ".env 未创建，首次部署会从 .env.example 复制"
  [[ -f "$ROOT_DIR/.env.example" ]] && success ".env.example 存在" || warn "缺少 .env.example"

  if command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    success "Python 可用：$($PYTHON_BIN --version 2>&1)"
  else
    error "找不到 Python：$PYTHON_BIN"
    failed=1
  fi

  command -v git >/dev/null 2>&1 && success "Git 可用：$(git --version)" || warn "未安装 Git，update 会跳过 git pull"
  command -v curl >/dev/null 2>&1 && success "curl 可用" || warn "未安装 curl，status 无法检查 API"
  command -v systemctl >/dev/null 2>&1 && success "systemd 可用" || warn "未检测到 systemd，将使用 nohup 方式运行"

  section "脚本权限"
  chmod +x "$ROOT_DIR/qt.sh" "$ROOT_DIR/scripts/"*.sh 2>/dev/null || warn "无法修改脚本执行权限，后续会用 bash 显式执行"
  for file in "$ROOT_DIR/qt.sh" "$ROOT_DIR/scripts/"*.sh; do
    [[ -f "$file" ]] || continue
    [[ -x "$file" ]] && success "${file#$ROOT_DIR/} 可执行" || warn "${file#$ROOT_DIR/} 不可执行，但可通过 bash 运行"
  done

  section "安全扫描"
  run_security_scan || failed=1

  if [[ "$failed" -eq 0 ]]; then
    success "环境检查完成，未发现阻断项"
  else
    die "环境检查发现阻断项，请先处理上面的错误"
  fi
}

cmd_stop() {
  require_project_root
  section "停止服务"
  if systemd_unit_exists; then
    if [[ "$(id -u)" -eq 0 ]]; then
      systemctl stop "$SERVICE_NAME"
    else
      sudo systemctl stop "$SERVICE_NAME"
    fi
    success "已停止 systemd 服务：$SERVICE_NAME"
    return
  fi

  local pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid"
      success "已停止 ${SERVICE_NAME}，pid=$pid"
    else
      warn "${SERVICE_NAME} 进程未运行"
    fi
  else
    warn "未找到 pid 文件：$pid_file"
  fi
}

cmd_logs() {
  require_project_root
  section "实时日志"
  if systemd_unit_exists; then
    info "按 Ctrl+C 退出日志"
    journalctl -u "$SERVICE_NAME" -f
  else
    local out_log="$ROOT_DIR/backend/data/${SERVICE_NAME}.out.log"
    local err_log="$ROOT_DIR/backend/data/${SERVICE_NAME}.err.log"
    [[ -f "$out_log" || -f "$err_log" ]] || die "未找到日志文件，请先启动服务"
    info "按 Ctrl+C 退出日志"
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
    read -r -p "请选择操作：" choice
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
        warn "无效选择：$choice"
        panel_pause
        ;;
    esac
  done
}

panel_pause() {
  echo
  read -r -p "按 Enter 返回..." _
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
    success "$label 完成"
  else
    warn "$label 退出码：$code"
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
10) GitHub 上传前安全扫描
11) 部署环境检查
0) 退出

EOF
    read -r -p "请选择操作：" choice
    choice="${choice//$'\r'/}"
    case "$choice" in
      1) panel_run "一键更新部署" bash "$SCRIPT_DIR/qt.sh" update ;;
      2) panel_run "重启服务" bash "$SCRIPT_DIR/qt.sh" restart ;;
      3) panel_run "停止服务" bash "$SCRIPT_DIR/qt.sh" stop ;;
      4) panel_run "查看服务状态" bash "$SCRIPT_DIR/qt.sh" status ;;
      5)
        clear || true
        bash "$SCRIPT_DIR/qt.sh" logs
        panel_pause
        ;;
      6) panel_run "备份数据" bash "$SCRIPT_DIR/qt.sh" backup ;;
      7)
        read -r -p "请输入备份 tar.gz 路径：" backup_file
        backup_file="${backup_file//$'\r'/}"
        [[ -n "$backup_file" ]] && panel_run "恢复数据" bash "$SCRIPT_DIR/qt.sh" restore "$backup_file"
        ;;
      8) cmd_auth ;;
      9) panel_run "清理样例持仓" bash "$SCRIPT_DIR/qt.sh" clear-sample ;;
      10) panel_run "安全扫描" bash "$SCRIPT_DIR/qt.sh" scan ;;
      11) panel_run "部署环境检查" bash "$SCRIPT_DIR/qt.sh" doctor ;;
      0|q|Q)
        echo "已退出运维面板"
        exit 0
        ;;
      *)
        warn "无效选择：$choice"
        panel_pause
        ;;
    esac
  done
}

cmd_clear_sample_state() {
  require_project_root
  local tool="$ROOT_DIR/scripts/clear_sample_state.py"
  [[ -f "$tool" ]] || die "找不到样例持仓清理工具：$tool"
  section "清理样例持仓"
  if [[ -x "$(venv_python)" ]]; then
    PYTHONPATH="$ROOT_DIR/backend" "$(venv_python)" "$tool"
  elif command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PYTHONPATH="$ROOT_DIR/backend" "$PYTHON_BIN" "$tool"
  else
    die "找不到 Python，无法清理样例持仓"
  fi
}

cmd="${1:-panel}"
case "$cmd" in
  panel|menu)
    cmd_panel
    ;;
  install|deploy|init)
    run_script "首次部署" "$(script_path install_server.sh)"
    ;;
  update|upgrade|up)
    run_script "一键更新" "$(script_path update_server.sh)"
    ;;
  restart|start|reload)
    run_script "重启服务" "$(script_path restart_server.sh)"
    ;;
  stop)
    cmd_stop
    ;;
  status|ps)
    cmd_status
    ;;
  logs|log)
    cmd_logs
    ;;
  backup|bak)
    run_script "备份数据" "$(script_path backup_data.sh)"
    ;;
  restore)
    [[ $# -ge 2 ]] || die "restore 需要传入备份 tar.gz 文件路径"
    run_script "恢复数据" "$(script_path restore_data.sh)" "$2"
    ;;
  auth|user|users|password|passwd)
    cmd_auth
    ;;
  clear-sample|clean-sample|sample)
    cmd_clear_sample_state
    ;;
  scan|security)
    section "GitHub 上传前安全扫描"
    run_security_scan
    ;;
  doctor|check)
    cmd_doctor
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    error "未知命令：$cmd"
    usage >&2
    exit 2
    ;;
esac
