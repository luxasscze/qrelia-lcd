#!/usr/bin/env python3
"""QRelia LCD-generation captive setup mode.

The tenant runtime deliberately stays alive while setup mode owns wlan0. It is
therefore the single owner of the 480x320 LCD and GPIO18 WS281x strip. This
process owns networking + captive portal only.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from display_state import set_display_state
try:
    from wifi_scan import refresh_wifi_network_cache, WIFI_SCAN_CACHE_PATH
except Exception as wifi_scan_import_error:
    refresh_wifi_network_cache = None
    WIFI_SCAN_CACHE_PATH = None
    print(f"Wi-Fi scan helper unavailable: {wifi_scan_import_error}", flush=True)

SCRIPT_DIR = Path(__file__).resolve().parent
SETUP_SERVER = Path(os.environ.get("QRELIA_SETUP_SERVER", str(SCRIPT_DIR / "setup_server.py")))
PROVISIONING_FAILURE_PATH = Path(os.environ.get("QRELIA_PROVISIONING_FAILURE_PATH", "/home/qrelia/qreliadevice/provisioning_error.json"))
WIFI_SETUP_FAILURE_PATH = Path(os.environ.get("QRELIA_WIFI_SETUP_FAILURE_PATH", "/home/qrelia/qreliadevice/wifi_setup_error.json"))
WIFI_INTERFACE = os.environ.get("QRELIA_WIFI_INTERFACE", "wlan0").strip() or "wlan0"
SETUP_IP = os.environ.get("QRELIA_SETUP_IP", "192.168.4.1").strip() or "192.168.4.1"
SETUP_HOSTNAME = os.environ.get("QRELIA_SETUP_HOSTNAME", "qrelia.local").strip() or "qrelia.local"
SETUP_URL = os.environ.get("QRELIA_SETUP_URL", f"http://{SETUP_HOSTNAME}/").strip() or f"http://{SETUP_HOSTNAME}/"
SETUP_SSID = os.environ.get("QRELIA_SETUP_SSID", "QRelia-Setup").strip() or "QRelia-Setup"
SETUP_PASSWORD = os.environ.get("QRELIA_SETUP_PASSWORD", "qrelia1234").strip() or "qrelia1234"
DNSMASQ_CONFIG_PATH = Path(os.environ.get("QRELIA_SETUP_DNSMASQ_CONFIG", "/tmp/qrelia-setup-dnsmasq.conf"))
DNSMASQ_PID_PATH = Path(os.environ.get("QRELIA_SETUP_DNSMASQ_PID", "/run/qrelia-setup-dnsmasq.pid"))
HOSTAPD_CONFIG_PATH = Path(os.environ.get("QRELIA_SETUP_HOSTAPD_CONFIG", "/tmp/qrelia-setup-hostapd.conf"))
HOSTAPD_PID_PATH = Path(os.environ.get("QRELIA_SETUP_HOSTAPD_PID", "/run/qrelia-setup-hostapd.pid"))


def run(args, check=False, timeout=20):
    if isinstance(args, str):
        printable = args
        result = subprocess.run(args, shell=True, text=True, capture_output=False, timeout=timeout)
    else:
        printable = " ".join(str(x) for x in args)
        result = subprocess.run([str(x) for x in args], text=True, capture_output=False, timeout=timeout)
    print(f"> {printable}", flush=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {printable}")
    return result


def terminate_pid_file(path, name):
    try:
        if path.exists():
            pid = path.read_text(encoding="utf-8").strip()
            if pid:
                subprocess.run(["kill", pid], check=False, timeout=5)
            path.unlink(missing_ok=True)
    except Exception as exc:
        print(f"Could not stop {name}: {exc}", flush=True)


def cache_nearby_wifi_networks():
    if refresh_wifi_network_cache is None:
        return
    print("Scanning nearby venue Wi-Fi before starting QRelia-Setup AP...", flush=True)
    try:
        networks, source, error = refresh_wifi_network_cache(
            allow_iw_fallback=False,
            preserve_existing_on_empty=False,
        )
        cache_path = str(WIFI_SCAN_CACHE_PATH) if WIFI_SCAN_CACHE_PATH else "cache"
        if networks:
            print(f"Cached {len(networks)} Wi-Fi network(s) from {source} at {cache_path}.", flush=True)
        elif error:
            print(f"No Wi-Fi networks cached: {error}", flush=True)
    except Exception as exc:
        print(f"Nearby Wi-Fi pre-scan failed: {exc}", flush=True)


def write_dnsmasq_config():
    DNSMASQ_CONFIG_PATH.write_text(
        f"""# QRelia setup captive portal DNS/DHCP. Generated at runtime.
interface={WIFI_INTERFACE}
bind-interfaces
port=53
dhcp-authoritative
dhcp-range=192.168.4.10,192.168.4.80,255.255.255.0,12h
dhcp-option=option:router,{SETUP_IP}
dhcp-option=option:dns-server,{SETUP_IP}
dhcp-option=114,{SETUP_URL}
domain-needed
bogus-priv
local-ttl=60
address=/{SETUP_HOSTNAME}/{SETUP_IP}
address=/#/{SETUP_IP}
""",
        encoding="utf-8",
    )


def write_hostapd_config():
    # WPA2 is intentionally used for maximum compatibility with phones during
    # commissioning. The setup network exists only while the device is being set up.
    HOSTAPD_CONFIG_PATH.write_text(
        f"""interface={WIFI_INTERFACE}
driver=nl80211
ssid={SETUP_SSID}
hw_mode=g
channel=6
wmm_enabled=1
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase={SETUP_PASSWORD}
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
""",
        encoding="utf-8",
    )


def stop_setup_daemons():
    terminate_pid_file(HOSTAPD_PID_PATH, "QRelia hostapd")
    terminate_pid_file(DNSMASQ_PID_PATH, "QRelia dnsmasq")
    # Stop distro-level instances as a conflict guard. They remain disabled by
    # install_device.sh; these calls are harmless when the services are absent.
    subprocess.run(["systemctl", "stop", "hostapd"], check=False, timeout=10)
    subprocess.run(["systemctl", "stop", "dnsmasq"], check=False, timeout=10)


def start_setup_daemons():
    write_dnsmasq_config()
    write_hostapd_config()
    run(["dnsmasq", f"--conf-file={DNSMASQ_CONFIG_PATH}", f"--pid-file={DNSMASQ_PID_PATH}"], check=True)
    run(["hostapd", "-B", "-P", str(HOSTAPD_PID_PATH), str(HOSTAPD_CONFIG_PATH)], check=True)


def pairing_failure_message():
    try:
        if not PROVISIONING_FAILURE_PATH.exists():
            return ""
        data = json.loads(PROVISIONING_FAILURE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or bool(data.get("retryable")):
            return ""
        return str(data.get("message") or "Fix setup code/PIN").strip()
    except Exception:
        return "Fix setup code/PIN"


def wifi_setup_failure_message():
    try:
        if not WIFI_SETUP_FAILURE_PATH.exists():
            return ""
        data = json.loads(WIFI_SETUP_FAILURE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
        ssid = str(data.get("ssid") or "venue Wi-Fi").strip()
        return f"Check password: {ssid}"
    except Exception:
        return "Check Wi-Fi password"


def setup_recovery_message():
    return wifi_setup_failure_message() or pairing_failure_message()


def start_setup_network():
    recovery_message = setup_recovery_message()
    set_display_state("setup_starting", message=recovery_message or "Preparing QRelia-Setup")
    stop_setup_daemons()

    # Scan while wlan0 is still a normal managed station; single-radio Raspberry
    # Pi hardware cannot reliably scan once the AP is broadcasting.
    run(["nmcli", "dev", "set", WIFI_INTERFACE, "managed", "yes"])
    run(["nmcli", "radio", "wifi", "on"])
    cache_nearby_wifi_networks()

    run(["nmcli", "dev", "disconnect", WIFI_INTERFACE])
    run(["nmcli", "dev", "set", WIFI_INTERFACE, "managed", "no"])
    time.sleep(0.8)

    run(["ip", "link", "set", WIFI_INTERFACE, "down"])
    run(["ip", "addr", "flush", "dev", WIFI_INTERFACE])
    run(["ip", "addr", "add", f"{SETUP_IP}/24", "dev", WIFI_INTERFACE], check=True)
    run(["ip", "link", "set", WIFI_INTERFACE, "up"], check=True)
    time.sleep(1.0)

    start_setup_daemons()
    time.sleep(0.7)
    set_display_state("setup_ready", message=recovery_message or "Connect to QRelia-Setup", ip=SETUP_IP)
    print(f"QRelia setup network active: SSID={SETUP_SSID}, portal={SETUP_URL} ({SETUP_IP})", flush=True)


def stop_setup_network():
    print("Stopping QRelia setup network...", flush=True)
    stop_setup_daemons()
    subprocess.run(["ip", "addr", "flush", "dev", WIFI_INTERFACE], check=False, timeout=8)
    subprocess.run(["nmcli", "dev", "set", WIFI_INTERFACE, "managed", "yes"], check=False, timeout=8)
    subprocess.run(["nmcli", "radio", "wifi", "on"], check=False, timeout=8)


def main():
    # IMPORTANT: do not stop qrelia-tenant.service. It is the sole owner of both
    # the LCD and GPIO18 strip in the LCD-generation device.
    set_display_state("setup_starting", message=setup_recovery_message() or "Preparing setup")
    server = None

    def handle_exit(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    try:
        start_setup_network()
        print("Starting QRelia setup web server...", flush=True)
        server = subprocess.Popen(
            [sys.executable, str(SETUP_SERVER)],
            cwd=str(SCRIPT_DIR),
            stdout=sys.stdout,
            stderr=sys.stderr,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        server.wait()
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        stop_setup_network()


if __name__ == "__main__":
    main()
