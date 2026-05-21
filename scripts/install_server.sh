#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

require_project_root
ensure_data_dir

if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/.env.example" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "created .env from .env.example; edit it before production use"
fi

if [[ -f "$ROOT_DIR/.env" ]] && grep -q "zt-sniper" "$ROOT_DIR/.env"; then
  echo "warning: .env still references zt-sniper; update QUANT_APP_DIR=/opt/qt and QUANT_SERVICE_NAME=qt when migrating"
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
    echo "systemd service installed: $SERVICE_FILE"
  elif command -v sudo >/dev/null 2>&1; then
    sudo cp "$TMP_SERVICE" "$SERVICE_FILE"
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$SERVICE_NAME"
    sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
    echo "systemd service installed: $SERVICE_FILE"
  else
    echo "systemd is available, but sudo/root is missing; service template left at $TMP_SERVICE"
    echo "install manually to: $SERVICE_FILE"
    TMP_SERVICE=""
  fi
  if [[ -n "${TMP_SERVICE:-}" ]]; then
    rm -f "$TMP_SERVICE"
  fi
fi

chmod +x "$ROOT_DIR/scripts/qt.sh" || true
QT_BIN="/usr/local/bin/qt"
if [[ "$(id -u)" -eq 0 ]]; then
  ln -sf "$ROOT_DIR/scripts/qt.sh" "$QT_BIN"
  echo "qt command installed: $QT_BIN"
elif command -v sudo >/dev/null 2>&1; then
  sudo ln -sf "$ROOT_DIR/scripts/qt.sh" "$QT_BIN"
  echo "qt command installed: $QT_BIN"
else
  echo "sudo/root is missing; optional qt command not installed"
  echo "manual command: ln -sf $ROOT_DIR/scripts/qt.sh $QT_BIN"
fi

echo "install complete"
echo "manual restart command: scripts/restart_server.sh"
echo "server shortcut: qt restart"
