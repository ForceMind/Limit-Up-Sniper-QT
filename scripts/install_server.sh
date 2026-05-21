#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root
ensure_data_dir

if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/.env.example" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "已根据 .env.example 创建 .env；生产使用前请先填写真实配置"
fi

if [[ -f "$ROOT_DIR/.env" ]] && grep -q "zt-sniper" "$ROOT_DIR/.env"; then
  echo "警告：.env 仍引用 zt-sniper；迁移时请改为 QUANT_APP_DIR=/opt/qt 和 QUANT_SERVICE_NAME=qt"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$(venv_pip)" install --upgrade pip
"$(venv_pip)" install -r "$ROOT_DIR/backend/requirements.txt"

mkdir -p "$ROOT_DIR/backend/data" "$BACKUP_ROOT"

if command -v systemctl >/dev/null 2>&1; then
  SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
  TMP_SERVICE="$(mktemp)"
  cat > "$TMP_SERVICE" <<EOF
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

  if [[ "$(id -u)" -eq 0 ]]; then
    cp "$TMP_SERVICE" "$SERVICE_FILE"
    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"
    systemctl status "$SERVICE_NAME" --no-pager -l || true
    echo "systemd 服务已安装：$SERVICE_FILE"
  elif command -v sudo >/dev/null 2>&1; then
    sudo cp "$TMP_SERVICE" "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$SERVICE_NAME"
    sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
    echo "systemd 服务已安装：$SERVICE_FILE"
  else
    echo "检测到 systemd，但当前没有 sudo/root 权限；服务模板保留在：$TMP_SERVICE"
    echo "请手动安装到：$SERVICE_FILE"
    TMP_SERVICE=""
  fi
  if [[ -n "${TMP_SERVICE:-}" ]]; then
    rm -f "$TMP_SERVICE"
  fi
fi

chmod +x "$ROOT_DIR/qt.sh" "$ROOT_DIR/scripts/"*.sh || true
QT_BIN="/usr/local/bin/qt"
if [[ "$(id -u)" -eq 0 ]]; then
  ln -sf "$ROOT_DIR/scripts/qt.sh" "$QT_BIN"
  echo "qt 快捷命令已安装：$QT_BIN"
elif command -v sudo >/dev/null 2>&1; then
  sudo ln -sf "$ROOT_DIR/scripts/qt.sh" "$QT_BIN"
  echo "qt 快捷命令已安装：$QT_BIN"
else
  echo "当前没有 sudo/root 权限；可选的 qt 快捷命令未安装"
  echo "可手动执行：ln -sf $ROOT_DIR/scripts/qt.sh $QT_BIN"
fi

echo "安装完成"
echo "项目根目录命令：bash qt.sh restart"
echo "服务器快捷命令：qt 打开运维面板；qt update 直接一键更新"
