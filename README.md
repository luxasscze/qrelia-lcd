# QRelia LCD Device

Production QRelia runtime for the 480×320 LCD generation of the ambient device.

## Hardware contract

This generation deliberately has one LED output only:

- **144-pixel addressable WS281x/WS2815 strip**
- **BCM GPIO18 / physical pin 12**
- 800 kHz, DMA 10, channel 0

There is **no 74HC595 shift register**, no eight-channel status bar, no SPI LED engine, and no separate common-anode/discrete RGB LED engine in this repository. The LCD is the operational status surface; GPIO18 is the only LED-output GPIO owned by the tenant runtime.

The existing network-reset/setup hardware is a separate provisioning concern and can remain in the original QRelia setup installation.

## What is included

- QRelia provisioning/claim handling and persisted device identity
- app and admin control-plane endpoints
- SignalR negotiation, registration, reconnect and protocol keepalive
- REST order reconciliation
- Pending / Processing / Waiting / Completed / Cancelled order state handling
- stale-order detection
- heartbeat and cloud-control diagnostics
- ambient profiles, live profile updates and profile previews
- Live Service and Showroom device modes
- Wi-Fi/cloud connection-loss grace and recovery logic
- 144-pixel GPIO18 ambient animation engine and the production 100-animation catalogues
- 480×320 LCD operational renderer based on `qrelia_showroom_ultra_v2.py`

## Raspberry Pi paths

The new LCD repository is expected at:

```text
/home/qrelia/qrelia
```

The supplied `qrelia-tenant.service` runs:

```text
/home/qrelia/qrelia/qrelia_tenant.py
```

For an existing QRelia device migration, the service intentionally continues to read the already-established provisioning/device state from:

```text
/home/qrelia/qreliadevice/
```

That preserves the device claim and remains compatible with the original setup stack instead of forcing the venue to pair the device again.

## Install Python dependencies

```bash
cd ~/qrelia
sudo python3 -m pip install -r requirements.txt
```

`rpi-ws281x` on GPIO18 requires the hardware permissions normally provided by root, so the supplied systemd unit runs the production runtime as `root`.

## Install/reinstall the service

```bash
cd ~/qrelia
sudo cp qrelia-tenant.service /etc/systemd/system/qrelia-tenant.service
sudo systemctl daemon-reload
sudo systemctl enable qrelia-tenant.service
sudo systemctl restart qrelia-tenant.service
sudo systemctl status qrelia-tenant.service --no-pager -l
```

Follow logs with:

```bash
sudo journalctl -u qrelia-tenant.service -f
```

## GPIO18 ownership

The runtime takes an exclusive process lock at `/tmp/qrelia-led-strip.lock` before initializing the strip. This prevents two QRelia processes from driving GPIO18 simultaneously. The old setup/status-strip process must not independently drive GPIO18 while the LCD tenant runtime owns it.

## LCD

`qrelia_lcd.py` is the production data-driven renderer. `qrelia_showroom_ultra_v2.py` remains the visual reference/showcase script and is not the production state machine.
