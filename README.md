# QRelia LCD Device

Production runtime for the 480×320 LCD generation of QRelia.

## Hardware contract

The tenant runtime has a single LED output: a **144-pixel WS281x/WS2815 strip on BCM GPIO18 (physical pin 12)**. The LCD is the operational display. The hard-reset button remains an input on BCM GPIO26.

The LCD and GPIO18 are always owned by `qrelia-tenant.service`. Setup mode owns only wlan0, DHCP/DNS and the captive portal, so commissioning cannot create a second LED/display renderer.

## Runtime layout

Application code:

```text
/home/qrelia/qrelia
```

Persistent device identity/profile state remains compatible with the original QRelia image:

```text
/home/qrelia/qreliadevice
```

Important services:

```text
qrelia-tenant.service
qrelia-setup-mode.service
qrelia-network-watchdog.service
qrelia-reset-button.service
```

`qrelia-setup-mode.service` is intentionally not enabled at boot. The tenant/watchdog starts it automatically when the device has no saved Wi-Fi/claim or pairing needs correction.

## Installation

From the repository:

```bash
cd ~/qrelia
sudo ./install_device.sh
```

The installer uses `/home/qrelia/qrelia/.venv`, installs the captive-portal networking dependencies, installs the systemd units, and enables the tenant/watchdog/reset-button services.

## First boot

An unpaired device automatically starts the `QRelia-Setup` access point. The LCD displays the setup state and portal address. Connect a phone to `QRelia-Setup`, open `http://qrelia.local/` (or `http://192.168.4.1/`), select venue Wi-Fi and enter the QRelia setup code/PIN.

The captive portal writes the same provisioning contract used by the original device. After restart the tenant claims the device, persists tenant/device identity, loads its ambient profile, starts heartbeat and SignalR, and reconciles active orders.

## Diagnostics

```bash
sudo systemctl status qrelia-tenant.service --no-pager -l
sudo systemctl status qrelia-setup-mode.service --no-pager -l
sudo systemctl status qrelia-network-watchdog.service --no-pager -l
sudo journalctl -u qrelia-tenant.service -f
sudo journalctl -u qrelia-setup-mode.service -f
```

Persistent claim files can be inspected with:

```bash
sudo ls -la /home/qrelia/qreliadevice
```
