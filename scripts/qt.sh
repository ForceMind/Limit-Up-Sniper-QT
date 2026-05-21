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

用法：
  qt install              第一次部署：安装依赖并注册 systemd 服务
  qt update               一键更新：备份数据、拉取代码、更新依赖、重启服务
  qt restart | start      重启服务
  qt stop                 停止 systemd 服务或 nohup 后台进程
  qt status               查看服务进程和 API 状态
  qt logs                 查看实时服务日志
  qt backup               备份 backend/data
  qt restore <tar.gz>     从备份恢复 backend/data
  qt scan                 执行 GitHub 上传前安全扫描
  qt help                 显示帮助
EOF
}

systemd_unit_exists() {
  command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"
}

api_url() {
  echo "http://127.0.0.1:${PORT}/api/status"
}

cmd="${1:-help}"
case "$cmd" in
  install)
    bash "$SCRIPT_DIR/install_server.sh"
    ;;
  update)
    bash "$SCRIPT_DIR/update_server.sh"
    ;;
  restart|start)
    bash "$SCRIPT_DIR/restart_server.sh"
    ;;
  stop)
    if systemd_unit_exists; then
      if [[ "$(id -u)" -eq 0 ]]; then
        systemctl stop "$SERVICE_NAME"
      else
        sudo systemctl stop "$SERVICE_NAME"
      fi
    else
      pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
      if [[ -f "$pid_file" ]]; then
        pid="$(cat "$pid_file" || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
          kill "$pid"
          echo "已停止 ${SERVICE_NAME}，pid=$pid"
        else
          echo "${SERVICE_NAME} 进程未运行"
        fi
      else
        echo "未找到 pid 文件：$pid_file"
      fi
    fi
    ;;
  status)
    if systemd_unit_exists; then
      systemctl status "$SERVICE_NAME" --no-pager -l || true
    else
      pid_file="$ROOT_DIR/backend/data/${SERVICE_NAME}.pid"
      if [[ -f "$pid_file" ]]; then
        pid="$(cat "$pid_file" || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
          echo "${SERVICE_NAME} 正在运行，pid=$pid"
        else
          echo "${SERVICE_NAME} pid 文件存在，但进程未运行"
        fi
      else
        echo "未找到 ${SERVICE_NAME} pid 文件"
      fi
    fi
    if command -v curl >/dev/null 2>&1; then
      curl -fsS "$(api_url)" || true
      echo
    fi
    ;;
  logs)
    if systemd_unit_exists; then
      journalctl -u "$SERVICE_NAME" -f
    else
      tail -f "$ROOT_DIR/backend/data/${SERVICE_NAME}.out.log" "$ROOT_DIR/backend/data/${SERVICE_NAME}.err.log"
    fi
    ;;
  backup)
    bash "$SCRIPT_DIR/backup_data.sh"
    ;;
  restore)
    if [[ $# -lt 2 ]]; then
      echo "错误：qt restore 需要传入备份 tar.gz 文件路径" >&2
      exit 2
    fi
    bash "$SCRIPT_DIR/restore_data.sh" "$2"
    ;;
  scan)
    if [[ -x "$(venv_python)" ]]; then
      "$(venv_python)" "$ROOT_DIR/scripts/security_scan.py"
    else
      "$PYTHON_BIN" "$ROOT_DIR/scripts/security_scan.py"
    fi
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "未知命令：$cmd" >&2
    usage >&2
    exit 2
    ;;
esac
