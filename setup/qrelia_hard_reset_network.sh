#!/bin/bash
set -euo pipefail

echo "[QRelia Reset] Starting network + pairing hard reset..."

DISPLAY_STATE_FILE="${QRELIA_DISPLAY_STATE_FILE:-/tmp/qrelia_display_state.json}"
STATE_DIR="${QRELIA_STATE_DIR:-/home/qrelia/qreliadevice}"
DEVICE_CONFIG_PATH="${QRELIA_DEVICE_CONFIG_PATH:-$STATE_DIR/qrelia_device_config.json}"
PROVISIONING_PATH="${QRELIA_PROVISIONING_PATH:-$STATE_DIR/provisioning.json}"
PROVISIONING_FAILURE_PATH="${QRELIA_PROVISIONING_FAILURE_PATH:-$STATE_DIR/provisioning_error.json}"
WIFI_SETUP_PENDING_PATH="${QRELIA_WIFI_SETUP_PENDING_PATH:-$STATE_DIR/wifi_setup_pending.json}"
WIFI_SETUP_FAILURE_PATH="${QRELIA_WIFI_SETUP_FAILURE_PATH:-$STATE_DIR/wifi_setup_error.json}"
AMBIENT_PROFILE_PATH="${QRELIA_PROFILE_PATH:-$STATE_DIR/ambient_profile.json}"
CONTROL_STATUS_PATH="${QRELIA_CONTROL_PLANE_STATUS_PATH:-$STATE_DIR/qrelia_cloud_control_status.json}"

WATCHDOG_SERVICE="qrelia-network-watchdog.service"
SETUP_SERVICE="qrelia-setup-mode.service"
TENANT_SERVICE="qrelia-tenant.service"

write_state() {
  local state="$1" message="$2" ip="${3:-}"
  cat > "$DISPLAY_STATE_FILE" <<EOF
{"state":"$state","ssid":"","message":"$message","ip":"$ip"}
EOF
}

echo "[QRelia Reset] Stopping watchdog + tenant while identity is erased..."
systemctl stop "$WATCHDOG_SERVICE" || true
systemctl stop "$TENANT_SERVICE" || true
systemctl stop "$SETUP_SERVICE" || true
write_state "setup_starting" "Resetting QRelia"

mkdir -p "$STATE_DIR"
echo "[QRelia Reset] Erasing QRelia pairing/profile state..."
rm -f \
  "$DEVICE_CONFIG_PATH" \
  "$PROVISIONING_PATH" \
  "$PROVISIONING_FAILURE_PATH" \
  "$WIFI_SETUP_PENDING_PATH" \
  "$WIFI_SETUP_FAILURE_PATH" \
  "$AMBIENT_PROFILE_PATH" \
  "$CONTROL_STATUS_PATH" || true

echo "[QRelia Reset] Removing saved Wi-Fi profiles..."
while IFS= read -r connection; do
  [ -n "$connection" ] || continue
  echo "[QRelia Reset] Deleting Wi-Fi profile: $connection"
  nmcli connection delete "$connection" || true
done < <(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="802-11-wireless" || $2=="wifi" || $2=="wireless" {print $1}')

rm -f /etc/wpa_supplicant/wpa_supplicant.conf.qrelia-backup || true
rm -f /etc/netplan/99-qrelia-wifi.yaml /etc/netplan/99-qrelia-venue-wifi.yaml || true

nmcli dev set wlan0 managed yes || true
nmcli radio wifi on || true
systemctl restart NetworkManager || true
sleep 2

echo "[QRelia Reset] Starting clean LCD runtime + setup portal..."
systemctl reset-failed "$TENANT_SERVICE" || true
systemctl start "$TENANT_SERVICE"
systemctl start "$SETUP_SERVICE"
systemctl start "$WATCHDOG_SERVICE"
write_state "setup_ready" "Connect to QRelia-Setup" "192.168.4.1"

echo "[QRelia Reset] Complete. Device is unpaired and in setup mode."
