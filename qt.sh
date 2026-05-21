#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_CMD="$ROOT_DIR/scripts/qt.sh"

usage() {
  cat <<EOF
涨停狙击手项目命令

在项目根目录执行：
  bash qt.sh install          第一次部署服务器：安装依赖并注册 systemd 服务
  bash qt.sh                  日常一键更新：等同于 bash qt.sh update
  bash qt.sh update           备份数据、拉取代码、更新依赖、重启服务
  bash qt.sh restart          重启服务
  bash qt.sh status           查看服务进程和 API 状态
  bash qt.sh logs             查看实时日志
  bash qt.sh backup           备份 backend/data
  bash qt.sh restore <tar.gz> 从备份恢复 backend/data
  bash qt.sh scan             执行 GitHub 上传前安全扫描

这个根目录脚本是给人使用的统一入口。
具体安装、更新、备份、恢复、重启逻辑仍放在 scripts/ 目录，避免根目录堆满运维脚本。
EOF
}

if [[ ! -f "$PROJECT_CMD" ]]; then
  echo "错误：找不到命令实现文件：$PROJECT_CMD" >&2
  exit 1
fi

cmd="${1:-update}"
case "$cmd" in
  help|-h|--help)
    usage
    ;;
  deploy)
    exec bash "$PROJECT_CMD" install "${@:2}"
    ;;
  *)
    exec bash "$PROJECT_CMD" "$cmd" "${@:2}"
    ;;
esac
