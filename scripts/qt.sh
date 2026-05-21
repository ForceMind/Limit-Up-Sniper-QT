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

推荐用法：
  bash qt.sh install              第一次部署：安装依赖并注册 systemd 服务
  bash qt.sh                      日常一键更新：备份数据、拉取代码、更新依赖、重启服务
  bash qt.sh status               查看服务状态、Git 版本、API 健康检查
  bash qt.sh doctor               检查部署环境和关键文件

完整命令：
  install | deploy | init         第一次部署
  update  | upgrade | up          一键更新
  restart | start | reload        重启服务
  stop                            停止服务
  status | ps                     查看状态
  logs   | log                    查看实时日志
  backup | bak                    备份 backend/data
  restore <tar.gz>                从备份恢复 backend/data
  scan   | security               GitHub 上传前安全扫描
  doctor | check                  部署环境检查
  help                            显示帮助

说明：
  根目录 qt.sh 是给人记的入口。
  scripts/ 目录保留具体实现，避免根目录堆满安装、更新、备份、恢复脚本。
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
  echo "http://127.0.0.1:${PORT}/api/status"
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

  section "服务状态"
  if systemd_unit_exists; then
    info "检测到 systemd 服务：${SERVICE_NAME}.service"
    systemctl is-active "$SERVICE_NAME" >/dev/null 2>&1 \
      && success "systemd 服务运行中" \
      || warn "systemd 服务未运行"
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
  chmod +x "$ROOT_DIR/qt.sh" "$ROOT_DIR/scripts/"*.sh 2>/dev/null || warn "无法修改脚本执行权限；后续会用 bash 显式执行"
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

cmd="${1:-help}"
case "$cmd" in
  install|deploy|init)
    run_script "第一次部署" "$(script_path install_server.sh)"
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
