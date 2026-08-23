#!/bin/bash
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
  echo "Run with sudo: sudo ./install_device.sh"
  exit 1
fi

APP_DIR="/home/qrelia/qrelia"
VENV="$APP_DIR/.venv"
STATE_DIR="/home/qrelia/qreliadevice"

if [ ! -d "$APP_DIR" ]; then
  echo "Expected QRelia LCD repo at $APP_DIR"
  exit 1
fi

apt-get update
apt-get install -y python3-venv python3-full python3-dev build-essential hostapd dnsmasq iw network-manager

# QRelia launches private hostapd/dnsmasq instances only during setup.
systemctl disable --now hostapd 2>/dev/null || true
systemctl disable --now dnsmasq 2>/dev/null || true

if [ ! -x "$VENV/bin/python" ]; then
  sudo -u qrelia python3 -m venv --system-site-packages "$VENV"
fi
sudo -u qrelia "$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
sudo -u qrelia "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

mkdir -p "$STATE_DIR"
chown qrelia:qrelia "$STATE_DIR"
chmod 755 "$STATE_DIR"
chmod +x "$APP_DIR/setup/qrelia_setup_mode.py" "$APP_DIR/setup/qrelia_hard_reset_network.sh"

install -m 0644 "$APP_DIR/qrelia-tenant.service" /etc/systemd/system/qrelia-tenant.service
install -m 0644 "$APP_DIR/systemd/qrelia-setup-mode.service" /etc/systemd/system/qrelia-setup-mode.service
install -m 0644 "$APP_DIR/systemd/qrelia-network-watchdog.service" /etc/systemd/system/qrelia-network-watchdog.service
install -m 0644 "$APP_DIR/systemd/qrelia-reset-button.service" /etc/systemd/system/qrelia-reset-button.service

systemctl daemon-reload
systemctl enable qrelia-tenant.service qrelia-network-watchdog.service qrelia-reset-button.service
# Setup mode is intentionally not enabled: tenant/watchdog starts it only when needed.
systemctl disable qrelia-setup-mode.service 2>/dev/null || true
systemctl restart qrelia-tenant.service
systemctl restart qrelia-network-watchdog.service
systemctl restart qrelia-reset-button.service

echo
echo "QRelia LCD services installed."
echo "Tenant:   systemctl status qrelia-tenant.service"
echo "Setup:    systemctl status qrelia-setup-mode.service"
echo "Watchdog: systemctl status qrelia-network-watchdog.service"
