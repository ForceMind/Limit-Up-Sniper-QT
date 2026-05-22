#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_CMD="$ROOT_DIR/scripts/qt.sh"

usage() {
  cat <<EOF
涨停狙击手项目命令

推荐用法：
  bash qt.sh install          第一次部署服务器
  bash qt.sh                  日常一键更新，等同于 bash qt.sh update
  bash qt.sh status           查看服务、Git 版本和 API 状态
  bash qt.sh doctor           检查部署环境和脚本权限
  qt                           服务器快捷命令，打开交互式运维面板

常用命令：
  install | deploy | init     第一次部署
  update  | upgrade | up      备份数据、拉取代码、更新依赖、重启服务
  restart | start | reload    重启服务
  stop                        停止服务
  status | ps                 查看状态
  logs   | log                查看实时日志
  backup | bak                备份 backend/data
  restore <tar.gz>            从备份恢复 backend/data
  auth                        账号密码管理
  clear-sample | sample       清理样例持仓
  scan   | security           GitHub 上传前安全扫描
  doctor | check              部署环境检查

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
