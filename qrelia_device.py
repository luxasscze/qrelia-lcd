#!/usr/bin/env python3

import asyncio
import colorsys
import fcntl
import json
import math
import random
import time
import os
import subprocess
import socket
from datetime import datetime, timezone
from urllib.parse import quote, urlparse
from pathlib import Path
import requests
import websockets
import qrelia_unique_100 as anim
from qrelia_lcd import QReliaLCDDisplay
from rpi_ws281x import PixelStrip


# =========================
# PROVISIONING
# =========================

DEVICE_CONFIG_PATH = Path(os.environ.get("QRELIA_DEVICE_CONFIG_PATH", "/home/qrelia/qreliadevice/qrelia_device_config.json"))
PROVISIONING_PATH = Path(os.environ.get("QRELIA_PROVISIONING_PATH", "/home/qrelia/qreliadevice/provisioning.json"))
PROVISIONING_FAILURE_PATH = Path(os.environ.get("QRELIA_PROVISIONING_FAILURE_PATH", "/home/qrelia/qreliadevice/provisioning_error.json"))
SETUP_DISPLAY_STATE_PATH = Path(os.environ.get("QRELIA_DISPLAY_STATE_FILE", "/tmp/qrelia_display_state.json"))
DEFAULT_APP_BASE_URL = "https://app.qrelia.uk"
DEFAULT_ADMIN_BASE_URL = "https://admin.qrelia.uk"

# qrelia_device_config.json is the source of truth after onboarding.
# Old/manual systemd Environment=QRELIA_TENANT_ID / QRELIA_DEVICE_ID values must
# not override the saved claim on cold boot; that creates the exact failure where
# the device receives tenant-level orders/profile updates but heartbeats the wrong
# device row, so admin keeps showing the real device offline.
ALLOW_IDENTITY_ENV_OVERRIDE = os.environ.get(
    "QRELIA_ALLOW_IDENTITY_ENV_OVERRIDE",
    "0"
).strip().lower() in ("1", "true", "yes", "on")


def clean_guid(value):
    return str(value or "").strip().strip("{}").lower()


def normalise_base_url(value, fallback):
    value = str(value or fallback or "").strip().rstrip("/")
    if not value:
        value = str(fallback or "").strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


def read_json_file(path):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"Could not read {path}: {exc}")
    return {}


def write_json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def remove_file_if_exists(path):
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        print(f"Could not remove {path}: {exc}")


def build_initial_device_config():
    config = read_json_file(DEVICE_CONFIG_PATH)
    return config.copy()


DEVICE_CONFIG = build_initial_device_config()


def config_identity_value(config_key, env_key):
    config_value = str(DEVICE_CONFIG.get(config_key) or "").strip()
    env_value = str(os.environ.get(env_key) or "").strip()

    if ALLOW_IDENTITY_ENV_OVERRIDE and env_value:
        return env_value

    return config_value or env_value


def identity_mismatch_diagnostic():
    return {
        "allowIdentityEnvOverride": ALLOW_IDENTITY_ENV_OVERRIDE,
        "configTenantId": clean_guid(DEVICE_CONFIG.get("tenantId")),
        "configDeviceId": clean_guid(DEVICE_CONFIG.get("deviceId")),
        "envTenantId": clean_guid(os.environ.get("QRELIA_TENANT_ID")),
        "envDeviceId": clean_guid(os.environ.get("QRELIA_DEVICE_ID")),
        "activeTenantId": TENANT_ID,
        "activeDeviceId": DEVICE_ID,
    }


# =========================
# CONFIG
# =========================

APP_BASE_URL = normalise_base_url(
    os.environ.get("QRELIA_APP_BASE_URL") or DEVICE_CONFIG.get("appBaseUrl"),
    DEFAULT_APP_BASE_URL
)
ADMIN_BASE_URL = normalise_base_url(
    os.environ.get("QRELIA_ADMIN_BASE_URL") or DEVICE_CONFIG.get("adminBaseUrl"),
    DEFAULT_ADMIN_BASE_URL
)
BASE_URL = APP_BASE_URL
HUB_PATH = "/hubs/orders"
ORDERS_API = "/api/orders"
AMBIENT_PROFILE_API = "/api/ambient-device/profile"
PROVISIONING_CLAIM_API = "/api/ambient-device/provision/claim"
HEARTBEAT_API = "/api/ambient-device/heartbeat"
PROFILE_PATH = Path(os.environ.get("QRELIA_PROFILE_PATH", "/home/qrelia/qreliadevice/ambient_profile.json"))
CONTROL_PLANE_STATUS_PATH = Path(os.environ.get(
    "QRELIA_CONTROL_PLANE_STATUS_PATH",
    "/home/qrelia/qreliadevice/qrelia_cloud_control_status.json"
))

RECONNECT_SECONDS = float(os.environ.get("QRELIA_RECONNECT_SECONDS", "5"))
HEARTBEAT_SECONDS = float(os.environ.get("QRELIA_HEARTBEAT_SECONDS", "30"))
# Keep polling the admin profile as a safety net. SignalR remains the primary
# realtime path, but after a cold power cycle the websocket/device group can be
# temporarily out of sync while REST and LCD already look healthy. Polling means
# admin ambient changes still land even if the live socket needs a reconnect.
PROFILE_POLL_SECONDS = float(os.environ.get("QRELIA_PROFILE_POLL_SECONDS", "20"))
ADMIN_CONTROL_SIGNALR_ENABLED = os.environ.get("QRELIA_ADMIN_CONTROL_SIGNALR_ENABLED", "0").lower() not in ("0", "false", "no")
ADMIN_CONTROL_SIGNALR_RECONNECT_SECONDS = float(os.environ.get("QRELIA_ADMIN_CONTROL_SIGNALR_RECONNECT_SECONDS", "5"))
FIRMWARE_VERSION = os.environ.get("QRELIA_FIRMWARE_VERSION", "lcd-python-2026.08.23")
SHOWROOM_SCENE_MIN_SECONDS = float(os.environ.get("QRELIA_SHOWROOM_SCENE_MIN_SECONDS", "11.0"))
SHOWROOM_SCENE_MAX_SECONDS = float(os.environ.get("QRELIA_SHOWROOM_SCENE_MAX_SECONDS", "18.0"))
SHOWROOM_DISSOLVE_SECONDS = float(os.environ.get("QRELIA_SHOWROOM_DISSOLVE_SECONDS", "3.2"))
SHOWROOM_ENTRY_SECONDS = float(os.environ.get("QRELIA_SHOWROOM_ENTRY_SECONDS", "1.8"))
# The generative showroom strip is substantially more CPU-heavy than the normal
# LedAnimator path because it evaluates multiple trigonometric layers per pixel
# (and two complete scenes while dissolving). On a Pi Zero 2 W, trying to run
# that workload at an unconditional 60 Hz can consume every event-loop timeslice
# and make the independent LCD appear jittery. 42 Hz remains visually fluid
# on the strip, phase-aligns with the proven status LCD cadence, and reserves
# deterministic scheduling room for the LCD renderer. Normal/live-service strip animations remain at their existing FPS.
try:
    SHOWROOM_STRIP_TARGET_FPS = float(os.environ.get("QRELIA_SHOWROOM_STRIP_FPS", "42"))
except (TypeError, ValueError):
    SHOWROOM_STRIP_TARGET_FPS = 42.0
SHOWROOM_STRIP_TARGET_FPS = max(30.0, min(60.0, SHOWROOM_STRIP_TARGET_FPS))
SHOWROOM_STRIP_FRAME_DELAY = 1.0 / SHOWROOM_STRIP_TARGET_FPS
try:
    SHOWROOM_STRIP_COOPERATIVE_PIXELS = int(os.environ.get("QRELIA_SHOWROOM_STRIP_COOPERATIVE_PIXELS", "48"))
except (TypeError, ValueError):
    SHOWROOM_STRIP_COOPERATIVE_PIXELS = 48
SHOWROOM_STRIP_COOPERATIVE_PIXELS = max(24, min(144, SHOWROOM_STRIP_COOPERATIVE_PIXELS))
SETUP_SERVICE = os.environ.get("QRELIA_SETUP_SERVICE", "qrelia-setup-mode.service")
SETUP_WIFI_SSID = os.environ.get("QRELIA_SETUP_SSID", "QRelia-Setup").strip() or "QRelia-Setup"
SETUP_WIFI_PASSWORD = os.environ.get("QRELIA_SETUP_PASSWORD", "qrelia1234").strip() or "qrelia1234"
NETWORK_WATCHDOG_SERVICE = os.environ.get("QRELIA_NETWORK_WATCHDOG_SERVICE", "qrelia-network-watchdog.service")
TENANT_SERVICE = os.environ.get("QRELIA_TENANT_SERVICE", "qrelia-tenant.service")
STALE_SECONDS = 600
SIGNALR_NEGOTIATE_TIMEOUT_SECONDS = float(os.environ.get("QRELIA_SIGNALR_NEGOTIATE_TIMEOUT_SECONDS", "4"))
# The old 6s/4s transport keepalive was too aggressive for a Pi Zero rendering
# the LCD and strip continuously. A short latency/scheduler spike could close a
# healthy socket. These defaults match a production-tolerant keepalive window.
SIGNALR_WS_PING_INTERVAL_SECONDS = float(os.environ.get("QRELIA_SIGNALR_WS_PING_INTERVAL_SECONDS", "15"))
SIGNALR_WS_PING_TIMEOUT_SECONDS = float(os.environ.get("QRELIA_SIGNALR_WS_PING_TIMEOUT_SECONDS", "20"))
SIGNALR_WS_OPEN_TIMEOUT_SECONDS = float(os.environ.get("QRELIA_SIGNALR_WS_OPEN_TIMEOUT_SECONDS", "10"))
SIGNALR_WS_CLOSE_TIMEOUT_SECONDS = float(os.environ.get("QRELIA_SIGNALR_WS_CLOSE_TIMEOUT_SECONDS", "5"))

# Raw websocket clients don't get the official SignalR client's protocol ping
# loop automatically. Send SignalR type-6 pings so ASP.NET Core doesn't close an
# otherwise healthy, mostly receive-only ambient-device connection as inactive.
SIGNALR_PROTOCOL_PING_SECONDS = float(os.environ.get("QRELIA_SIGNALR_PROTOCOL_PING_SECONDS", "15"))

# Brief socket recycling must not become a venue-facing OFFLINE alarm. Reconnect
# continues normally; LCD/strip change only when the loss persists.
SIGNALR_DISCONNECT_VISUAL_GRACE_SECONDS = float(os.environ.get(
    "QRELIA_SIGNALR_DISCONNECT_VISUAL_GRACE_SECONDS",
    "12"
))

# One iwgetid miss isn't proof of an outage. Poll less aggressively and require
# both SSID and IP to stay absent before declaring local Wi-Fi disconnected.
LOCAL_WIFI_CHECK_SECONDS = float(os.environ.get("QRELIA_LOCAL_WIFI_CHECK_SECONDS", "2"))
LOCAL_WIFI_LOSS_CONFIRM_SECONDS = float(os.environ.get("QRELIA_LOCAL_WIFI_LOSS_CONFIRM_SECONDS", "5"))
PROFILE_API_TIMEOUT_SECONDS = float(os.environ.get("QRELIA_PROFILE_API_TIMEOUT_SECONDS", "5"))
ORDERS_API_TIMEOUT_SECONDS = float(os.environ.get("QRELIA_ORDERS_API_TIMEOUT_SECONDS", "6"))
ORDER_RECONCILE_SECONDS = float(os.environ.get("QRELIA_ORDER_RECONCILE_SECONDS", "10"))
PRESERVE_ACTIVE_WHEN_ABSENT_FROM_SNAPSHOT = os.environ.get(
    "QRELIA_PRESERVE_ACTIVE_WHEN_ABSENT_FROM_SNAPSHOT",
    os.environ.get("QRELIA_PRESERVE_PROCESSING_WHEN_ABSENT_FROM_SNAPSHOT", "1")
).lower() not in ("0", "false", "no")
ACTIVE_ORDER_SNAPSHOT_GRACE_SECONDS = float(os.environ.get(
    "QRELIA_ACTIVE_ORDER_SNAPSHOT_GRACE_SECONDS",
    os.environ.get("QRELIA_PROCESSING_SNAPSHOT_GRACE_SECONDS", str(6 * 60 * 60))
))
TRANSIENT_STATUS_SECONDS = float(os.environ.get("QRELIA_TRANSIENT_STATUS_SECONDS", "2.4"))
# Keep the physical strip calmer than the LCD. The LCD may report a sustained
# outage after the normal grace period, but the room-facing strip waits longer so
# a socket that recovers around the threshold cannot flash the amber alarm. A
# negative value still disables only the connection-lost strip override.
CONNECTION_LOST_STRIP_AFTER_SECONDS = float(os.environ.get(
    "QRELIA_CONNECTION_LOST_STRIP_AFTER_SECONDS",
    str(max(SIGNALR_DISCONNECT_VISUAL_GRACE_SECONDS, 20.0))
))
LED_STRIP_LOCK_PATH = Path(os.environ.get("QRELIA_LED_STRIP_LOCK_PATH", "/tmp/qrelia-led-strip.lock"))



TENANT_ID = clean_guid(config_identity_value("tenantId", "QRELIA_TENANT_ID"))
DEVICE_ID = clean_guid(config_identity_value("deviceId", "QRELIA_DEVICE_ID"))
DEVICE_IDENTIFIER = str(DEVICE_CONFIG.get("deviceIdentifier") or "").strip()
DEVICE_NAME = str(DEVICE_CONFIG.get("deviceName") or "QRelia Ambient Hub").strip() or "QRelia Ambient Hub"

runtime_status_title = "QRelia Setup"
runtime_status_message = "Starting device"
runtime_status_mode = "setup"


def set_runtime_status(title, message="", mode="setup"):
    global runtime_status_title, runtime_status_message, runtime_status_mode, last_update_time
    runtime_status_title = str(title or "QRelia").strip()[:28]
    runtime_status_message = str(message or "").strip()[:56]
    runtime_status_mode = str(mode or "setup").strip()
    try:
        last_update_time = time.time()
    except NameError:
        pass


def set_network_runtime_status(message):
    text = str(message or "Preparing cloud claim").strip()
    lowered = text.lower()

    if "dns" in lowered or "cloud" in lowered or "online" in lowered or "connected" in lowered:
        title = "Venue WiFi OK"
    elif "ip" in lowered:
        title = "Getting WiFi IP"
    else:
        title = "Joining Venue WiFi"

    set_runtime_status(title, text, mode="network")


def apply_device_config(config):
    global APP_BASE_URL, ADMIN_BASE_URL, BASE_URL, TENANT_ID, DEVICE_ID, DEVICE_IDENTIFIER, DEVICE_NAME, DEVICE_CONFIG

    if not isinstance(config, dict):
        return

    DEVICE_CONFIG = config.copy()
    APP_BASE_URL = normalise_base_url(config.get("appBaseUrl"), DEFAULT_APP_BASE_URL)
    ADMIN_BASE_URL = normalise_base_url(config.get("adminBaseUrl"), DEFAULT_ADMIN_BASE_URL)
    BASE_URL = APP_BASE_URL
    # During normal operation the persisted claim file wins. Environment identity
    # override is only for deliberate lab/debug work with
    # QRELIA_ALLOW_IDENTITY_ENV_OVERRIDE=1.
    if ALLOW_IDENTITY_ENV_OVERRIDE:
        TENANT_ID = clean_guid(os.environ.get("QRELIA_TENANT_ID") or config.get("tenantId"))
        DEVICE_ID = clean_guid(os.environ.get("QRELIA_DEVICE_ID") or config.get("deviceId"))
    else:
        TENANT_ID = clean_guid(config.get("tenantId") or os.environ.get("QRELIA_TENANT_ID"))
        DEVICE_ID = clean_guid(config.get("deviceId") or os.environ.get("QRELIA_DEVICE_ID"))
    DEVICE_IDENTIFIER = str(config.get("deviceIdentifier") or "").strip()
    DEVICE_NAME = str(config.get("deviceName") or DEVICE_NAME or "QRelia Ambient Hub").strip() or "QRelia Ambient Hub"


def device_has_runtime_identity():
    return bool(TENANT_ID and DEVICE_ID)


def reload_device_config_from_disk():
    config = read_json_file(DEVICE_CONFIG_PATH)
    if config.get("tenantId") and config.get("deviceId"):
        before = (TENANT_ID, DEVICE_ID)
        apply_device_config(config)
        after = (TENANT_ID, DEVICE_ID)
        if before != after:
            print(
                "QRelia runtime identity reloaded from disk: "
                f"tenant={TENANT_ID}, device={DEVICE_ID}"
            )
        return True
    return False


def has_pending_provisioning_request():
    return PROVISIONING_PATH.exists()


def device_is_in_setup_flow():
    """True while the LCD should show commissioning instead of live/offline UI."""
    return (
        not device_has_runtime_identity()
        or has_pending_provisioning_request()
        or provisioning_failure_requires_setup()
    )


def setup_service_active():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SETUP_SERVICE],
            text=True,
            capture_output=True,
            timeout=3,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def ensure_setup_mode_available(message="Connect to QRelia-Setup"):
    """Start captive setup networking without surrendering LCD/GPIO18 ownership."""
    set_runtime_status("QRelia Setup", message, mode="setup")
    if setup_service_active():
        return True
    try:
        result = subprocess.run(
            ["systemctl", "start", SETUP_SERVICE],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0:
            print("QRelia-Setup captive portal started.")
            return True
        print("Could not start QRelia setup service:", (result.stderr or result.stdout).strip())
    except Exception as exc:
        print("Could not start QRelia setup service:", exc)
    return False


def write_provisioning_failure(message, setup_code="", retryable=False, category="pairing"):
    payload = {
        "message": str(message or "Pairing failed").strip(),
        "setupCode": str(setup_code or "").strip(),
        "retryable": bool(retryable),
        "category": str(category or "pairing").strip(),
        "failedAt": datetime.now(timezone.utc).isoformat(),
    }
    write_json_file(PROVISIONING_FAILURE_PATH, payload)
    print("QRelia provisioning failure:", payload["message"])


def clear_provisioning_failure():
    remove_file_if_exists(PROVISIONING_FAILURE_PATH)


def provisioning_failure_requires_setup():
    if not PROVISIONING_FAILURE_PATH.exists():
        return False

    failure = read_json_file(PROVISIONING_FAILURE_PATH)
    return not bool(failure.get("retryable"))


def current_provisioning_failure():
    return read_json_file(PROVISIONING_FAILURE_PATH) if PROVISIONING_FAILURE_PATH.exists() else {}


def claim_network_ready(admin_base_url):
    ssid = read_connected_ssid()
    ip_address = read_ip_address()

    if not ssid:
        return False, "Waiting for venue WiFi"

    if not ip_address:
        return False, f"{ssid} connected, waiting IP"

    host = urlparse(admin_base_url).hostname or "admin.qrelia.uk"
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, f"{ssid} connected, waiting DNS"
    except Exception as exc:
        return False, f"Network check failed: {exc}"

    return True, f"{ssid} online"


def request_setup_mode_after_pairing_failure(message):
    set_runtime_status("Pairing failed", message or "Open QRelia-Setup", mode="pairing")
    # LCD generation: tenant remains alive and is the sole LCD/GPIO18 owner.
    # Setup mode owns only wlan0 + the captive portal.
    if ensure_setup_mode_available(message or "Fix setup code/PIN"):
        print("Pairing failed; QRelia-Setup is available for corrected code/PIN.")


def claim_device_from_provisioning():
    pending_request = has_pending_provisioning_request()

    # Existing live identity is valid only when there is no new setup request waiting.
    # If provisioning.json exists, the user deliberately entered/re-entered a setup
    # code and PIN, so the backend must verify it before tenant runtime is allowed.
    # On a normal cold boot there is no provisioning.json, so force the identity from
    # qrelia_device_config.json before trusting any environment values.
    if not pending_request and reload_device_config_from_disk():
        clear_provisioning_failure()
        set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")
        return True

    if device_has_runtime_identity() and not pending_request:
        clear_provisioning_failure()
        set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")
        return True

    request = read_json_file(PROVISIONING_PATH)
    setup_code = str(request.get("setupCode") or "").strip().upper().replace(" ", "")
    setup_pin = str(request.get("setupPin") or "").strip()
    admin_base_url = normalise_base_url(request.get("adminBaseUrl") or ADMIN_BASE_URL, DEFAULT_ADMIN_BASE_URL)

    if not setup_code or not setup_pin:
        message = "Setup code and PIN are required. Open QRelia-Setup and copy both values from the admin device order."
        print(f"QRelia device is not provisioned. {message} Missing file/details: {PROVISIONING_PATH}")
        if pending_request:
            write_provisioning_failure(message, setup_code)
        return False

    network_ready, network_message = claim_network_ready(admin_base_url)
    if not network_ready:
        set_network_runtime_status(network_message)
        print(f"QRelia claim delayed: {network_message}")
        write_provisioning_failure(network_message, setup_code, retryable=True, category="network")
        return False

    url = f"{admin_base_url}{PROVISIONING_CLAIM_API}"
    payload = {
        "setupCode": setup_code,
        "setupPin": setup_pin,
        "firmwareVersion": FIRMWARE_VERSION,
        "deviceName": request.get("deviceName") or DEVICE_NAME or "QRelia Ambient Hub",
    }

    try:
        set_runtime_status("Verifying device", f"Checking {setup_code}", mode="pairing")
        print(f"Claiming QRelia ambient device with setup code {setup_code} via {url}")
        response = requests.post(url, json=payload, timeout=PROFILE_API_TIMEOUT_SECONDS)

        if response.status_code >= 400:
            body = response.text[:500]
            message = f"Pairing rejected by QRelia ({response.status_code}). Check the setup code and PIN."
            try:
                parsed = response.json()
                message = parsed.get("message") or parsed.get("Message") or message
            except Exception:
                pass
            print(f"Provisioning claim failed ({response.status_code}): {body}")
            write_provisioning_failure(message, setup_code, retryable=response.status_code >= 500, category="cloud" if response.status_code >= 500 else "pairing")
            return False

        result = response.json()
        if not result.get("success") and not result.get("Success"):
            message = result.get("message") or result.get("Message") or "Pairing was rejected by QRelia."
            print("Provisioning claim was rejected:", message)
            write_provisioning_failure(message, setup_code)
            return False

        config = {
            "tenantId": clean_guid(result.get("tenantId") or result.get("TenantId")),
            "deviceId": clean_guid(result.get("deviceId") or result.get("DeviceId")),
            "deviceIdentifier": str(result.get("deviceIdentifier") or result.get("DeviceIdentifier") or "").strip(),
            "setupCode": str(result.get("setupCode") or result.get("SetupCode") or setup_code).strip(),
            "deviceName": str(result.get("deviceName") or result.get("DeviceName") or payload["deviceName"]).strip(),
            "appBaseUrl": normalise_base_url(result.get("appBaseUrl") or result.get("AppBaseUrl"), DEFAULT_APP_BASE_URL),
            "adminBaseUrl": normalise_base_url(result.get("adminBaseUrl") or result.get("AdminBaseUrl") or admin_base_url, DEFAULT_ADMIN_BASE_URL),
            "firmwareVersion": str(result.get("firmwareVersion") or result.get("FirmwareVersion") or FIRMWARE_VERSION).strip(),
            "claimedAt": datetime.now(timezone.utc).isoformat(),
        }

        if not config["tenantId"] or not config["deviceId"]:
            message = "QRelia claim response did not include tenantId/deviceId, so the device refused to save an unsafe config."
            print(message)
            write_provisioning_failure(message, setup_code, retryable=True, category="cloud")
            return False

        write_json_file(DEVICE_CONFIG_PATH, config)
        remove_file_if_exists(PROVISIONING_PATH)
        clear_provisioning_failure()
        remove_file_if_exists(SETUP_DISPLAY_STATE_PATH)
        apply_device_config(config)
        set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")
        print(f"QRelia ambient device provisioned: tenant={TENANT_ID}, device={DEVICE_ID}, identifier={DEVICE_IDENTIFIER or 'n/a'}")
        return True

    except Exception as exc:
        # Network/API outages are retryable. Do not force setup mode unless QRelia
        # explicitly rejected the code/PIN; otherwise a temporary outage would trap
        # the venue owner unnecessarily.
        message = "Wi-Fi is connected, but QRelia Cloud is not reachable yet."
        detail = str(exc)[:240]
        print(f"{message} {detail}")
        write_provisioning_failure(message, setup_code, retryable=True, category="network")
        set_network_runtime_status("Checking QRelia Cloud")
        return False


# =========================
# ADDRESSABLE RGB LED STRIP
# =========================

transition_lock = asyncio.Lock()


def acquire_led_strip_lock():
    """Hold exclusive ownership of GPIO18/DMA10 for this process lifetime.

    Setup mode uses the same lock.  This is a final hardware-level guard against
    boot ordering races where systemd briefly launches setup and tenant runtimes
    together.
    """
    LED_STRIP_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = LED_STRIP_LOCK_PATH.open("a+", encoding="utf-8")
    print(f"Waiting for exclusive LED strip ownership: {LED_STRIP_LOCK_PATH}", flush=True)
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"tenant:{os.getpid()}\n")
    lock_file.flush()
    print("Exclusive LED strip ownership acquired by tenant runtime.", flush=True)
    return lock_file


led_strip_lock_file = acquire_led_strip_lock()

# The LCD generation intentionally has one LED output only: a 144-pixel
# addressable WS281x/WS2815 strip on BCM GPIO18.
LED_COUNT = 144
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_INVERT = False
LED_BRIGHTNESS = 30
LED_CHANNEL = 0

strip = PixelStrip(
    LED_COUNT,
    LED_PIN,
    LED_FREQ_HZ,
    LED_DMA,
    LED_INVERT,
    LED_BRIGHTNESS,
    LED_CHANNEL,
)
strip.begin()

anim.init_strip(strip)


def addressable_strip_off():
    """Turn off the sole LED output without touching any other GPIO."""
    try:
        for index in range(strip.numPixels()):
            strip.setPixelColor(index, 0)
        strip.show()
    except Exception as exc:
        print(f"Could not clear GPIO18 addressable strip: {exc}")


def shutdown_hardware():
    """Release the GPIO18 strip cleanly; there is no secondary GPIO hardware."""
    addressable_strip_off()
    try:
        fcntl.flock(led_strip_lock_file.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        led_strip_lock_file.close()
    except Exception:
        pass


animator = anim.LedAnimator()

DEFAULT_STATE_TO_ANIMATION = {
    # Calm fallbacks only. Scanner/strobe/meteor animations are still available
    # in the library, but they must never be automatic device fallbacks.
    "connectionLost": "amber_halo",
    "setup": "frosted_mint",
    "newOrder": "champagne_shimmer",
    "processing": "aurora_royal",
    # Waiting is a first-class SaaS profile state. Its default mirrors the admin
    # portal, while legacy profiles without AnimWaiting still inherit Processing.
    "waiting": "golden_orbit",
    "idle": "premium_velvet_breath",
    "stale": "amber_halo",
    "completed": "aurora_emerald",
    "cancelled": "rose_gold_current",
    "error": "pulse_amber",
}

STATE_TO_ANIMATION = DEFAULT_STATE_TO_ANIMATION.copy()
VALID_AMBIENT_STATES = set(DEFAULT_STATE_TO_ANIMATION.keys())
SYSTEM_AMBIENT_STATES = {"setup", "connectionLost", "error"}
current_state = "setup"
current_animation_key = None
profile_preview_until = 0.0
ambient_transient_until = 0.0
# System states are explicit device/runtime states. They must win over order-derived
# states until they are cleared, otherwise setup/error/connection-lost visuals are
# immediately overwritten by idle/pending/processing.
system_ambient_state = "setup"
app_signalr_disconnected_since = None
local_wifi_disconnected_since = None
connection_lost_visible = False
signalr_connected = False
last_signalr_connected_at = 0.0
admin_signalr_connected = False
last_admin_signalr_connected_at = 0.0
last_successful_heartbeat_at = 0.0
last_heartbeat_error = ""
last_control_plane_success_at = 0.0
last_control_plane_error = ""
active_profile_fingerprint = ""

DEVICE_MODE_LIVE = "Live service mode"
DEVICE_MODE_SHOWROOM = "Showroom preview mode"
active_device_mode = DEVICE_MODE_LIVE
showroom_scene_started_at = 0.0
showroom_scene_duration = 0.0
showroom_scene_index = 0
showroom_next_scene_index = 1
showroom_scene_seed = 0.0
showroom_next_scene_seed = 0.0
showroom_transition_style = 0
showroom_entry_started_at = 0.0
showroom_entry_frame = None

# Showroom mode has its own generative renderer. These are scene identities only;
# none maps to qrelia_unique_100 / Hospitality 100 or any normal status animation.
SHOWROOM_SCENE_NAMES = (
    "liquid-silk",
    "champagne-caustics",
    "deep-tide",
    "prism-architecture",
    "comet-ballet",
    "velvet-nebula",
    "pearl-chorus",
    "midnight-horizon",
)


def normalise_device_mode(value):
    text = str(value or DEVICE_MODE_LIVE).strip()
    token = text.replace("_", "").replace("-", "").replace(" ", "").lower()
    if token in {"showroom", "showroompreview", "showroompreviewmode"}:
        return DEVICE_MODE_SHOWROOM
    return text or DEVICE_MODE_LIVE


def showroom_mode_enabled():
    return normalise_device_mode(active_device_mode) == DEVICE_MODE_SHOWROOM


def connection_loss_started_at():
    # During commissioning wlan0 is deliberately switched into AP mode, so a
    # missing venue SSID/SignalR socket is expected and must not replace SETUP
    # visuals with a false OFFLINE alarm.
    if device_is_in_setup_flow():
        return None

    values = [
        value for value in (
            app_signalr_disconnected_since,
            local_wifi_disconnected_since,
        )
        if value is not None
    ]
    return min(values) if values else None


def connection_loss_is_visually_confirmed(now=None):
    started_at = connection_loss_started_at()
    if started_at is None:
        return False

    now = time.time() if now is None else now
    return now - started_at >= max(0.0, SIGNALR_DISCONNECT_VISUAL_GRACE_SECONDS)


def connection_loss_is_strip_confirmed(now=None):
    if CONNECTION_LOST_STRIP_AFTER_SECONDS < 0:
        return False

    started_at = connection_loss_started_at()
    if started_at is None:
        return False

    now = time.time() if now is None else now
    required_delay = max(
        0.0,
        SIGNALR_DISCONNECT_VISUAL_GRACE_SECONDS,
        CONNECTION_LOST_STRIP_AFTER_SECONDS,
    )
    return now - started_at >= required_delay


def safe_animation_name(name, fallback="premium_velvet_breath"):
    if name in anim.ANIMATIONS:
        return name
    print(f"Unknown ambient animation '{name}', using '{fallback}'")
    return fallback if fallback in anim.ANIMATIONS else "premium_velvet_breath"

def normalise_state_name(state):
    state = str(state or "idle").strip()
    for valid_state in VALID_AMBIENT_STATES:
        if state.lower() == valid_state.lower():
            return valid_state
    return "idle"

def animation_for_state(state):
    state = normalise_state_name(state)
    fallback = DEFAULT_STATE_TO_ANIMATION.get(state) or DEFAULT_STATE_TO_ANIMATION["idle"]
    return safe_animation_name(STATE_TO_ANIMATION.get(state) or fallback, fallback=fallback)

current_animation_key = animation_for_state(current_state)
animator.set_animation(current_animation_key)

def get_value(data, camel, pascal=None, default=None):
    if not isinstance(data, dict):
        return default
    if camel in data and data[camel] is not None:
        return data[camel]
    if pascal and pascal in data and data[pascal] is not None:
        return data[pascal]
    return default


def _ambient_state_token(value):
    return str(value or "").strip().replace("_", "").replace("-", "").replace(" ", "").lower()


def normalise_animation_map(data):
    if not isinstance(data, dict):
        return {}

    state_lookup = {_ambient_state_token(state): state for state in VALID_AMBIENT_STATES}
    result = {}

    for raw_state, raw_animation in data.items():
        state = state_lookup.get(_ambient_state_token(raw_state))
        animation_name = str(raw_animation or "").strip()
        if state and animation_name:
            result[state] = animation_name

    return result


def normalise_profile(data):
    profile = data if isinstance(data, dict) else {}
    animations = get_value(profile, "animations", "Animations", {}) or {}
    animation_overrides = normalise_animation_map(animations)

    # Backend sends both an Anim* field set and an animations dictionary. Treat
    # Waiting exactly like every other state, while accepting camelCase, PascalCase
    # and differently-cased dictionary keys from current and older deployments.
    merged = DEFAULT_STATE_TO_ANIMATION.copy()
    merged.update(animation_overrides)

    field_map = {
        "connectionLost": ("animConnectionLost", "AnimConnectionLost"),
        "setup": ("animSetup", "AnimSetup"),
        "processing": ("animProcessing", "AnimProcessing"),
        "waiting": ("animWaiting", "AnimWaiting"),
        "idle": ("animIdle", "AnimIdle"),
        "cancelled": ("animCancelled", "AnimCancelled"),
        "completed": ("animCompleted", "AnimCompleted"),
        "error": ("animError", "AnimError"),
        "newOrder": ("animNewOrder", "AnimNewOrder"),
        "stale": ("animStale", "AnimStale"),
    }

    explicit_fields = {}
    for state, (camel, pascal) in field_map.items():
        value = str(get_value(profile, camel, pascal, "") or "").strip()
        if value:
            explicit_fields[state] = value
            merged[state] = value

    # Backward compatibility for profiles created before AnimWaiting existed. A
    # genuinely supplied waiting value always wins; only absent legacy values fall
    # back to the effective Processing animation.
    if "waiting" not in animation_overrides and "waiting" not in explicit_fields:
        merged["waiting"] = merged.get("processing") or DEFAULT_STATE_TO_ANIMATION["waiting"]

    brightness = int(get_value(profile, "brightness", "Brightness", 84) or 84)
    stale_seconds = int(get_value(profile, "staleAfterSeconds", "StaleAfterSeconds", 600) or 600)

    return {
        "id": get_value(profile, "id", "Id"),
        "tenantId": get_value(profile, "tenantId", "TenantId", TENANT_ID),
        "deviceId": get_value(profile, "deviceId", "DeviceId", DEVICE_ID),
        "deviceName": get_value(profile, "deviceName", "DeviceName", DEVICE_NAME),
        "deviceMode": normalise_device_mode(get_value(profile, "deviceMode", "DeviceMode", DEVICE_MODE_LIVE)),
        "selectedState": normalise_state_name(get_value(profile, "selectedState", "SelectedState", "idle")),
        "brightness": max(1, min(100, brightness)),
        "staleAfterSeconds": max(10, stale_seconds),
        "animations": merged,
    }


def profile_fingerprint(profile):
    try:
        return json.dumps(normalise_profile(profile), sort_keys=True, separators=(",", ":"))
    except Exception:
        return ""


def save_profile_to_disk(profile):
    try:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        print(f"Ambient profile saved to {PROFILE_PATH}")
    except Exception as e:
        print("Failed saving ambient profile to disk:", e)


def load_profile_from_disk():
    try:
        if PROFILE_PATH.exists():
            return normalise_profile(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        print("Failed loading local ambient profile:", e)
    return normalise_profile({})


def unique_non_empty(values):
    seen = set()
    result = []
    for value in values:
        value = str(value or "").strip().rstrip("/")
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def with_host_prefix(base_url, required_prefix):
    base_url = normalise_base_url(base_url, "")
    if not base_url:
        return ""

    try:
        parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
        scheme = parsed.scheme or "https"
        host = parsed.hostname or ""
        if not host:
            return ""

        if host.startswith("admin.") and required_prefix == "app.":
            host = "app." + host[len("admin."):]
        elif host.startswith("app.") and required_prefix == "admin.":
            host = "admin." + host[len("app."):]
        elif not host.startswith(required_prefix):
            host = required_prefix + host

        port = f":{parsed.port}" if parsed.port else ""
        return f"{scheme}://{host}{port}".rstrip("/")
    except Exception:
        return ""


def control_plane_base_urls():
    # Keep the control plane deterministic and fast. The Pi should touch the app
    # endpoint first because that is the same host used for orders/SignalR; admin
    # remains the fallback. Do not try generated host guesses on every heartbeat.
    return unique_non_empty([
        APP_BASE_URL,
        DEFAULT_APP_BASE_URL,
        ADMIN_BASE_URL,
        DEFAULT_ADMIN_BASE_URL,
    ])


def control_signalr_base_urls():
    return unique_non_empty([
        APP_BASE_URL,
        DEFAULT_APP_BASE_URL,
        ADMIN_BASE_URL,
        DEFAULT_ADMIN_BASE_URL,
    ])


def control_plane_headers():
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": f"QRelia-Ambient-Device/{FIRMWARE_VERSION}",
    }

    # Keep both naming conventions. ASP.NET Core model/header binding is usually
    # tolerant, but proxies/custom code often are not.
    if TENANT_ID:
        headers.update({
            "X-Tenant-Id": TENANT_ID,
            "X-TenantId": TENANT_ID,
            "X-Tenant-ID": TENANT_ID,
            "X-QRelia-Tenant-Id": TENANT_ID,
        })
    if DEVICE_ID:
        headers.update({
            "X-Device-Id": DEVICE_ID,
            "X-DeviceId": DEVICE_ID,
            "X-Device-ID": DEVICE_ID,
            "X-QRelia-Device-Id": DEVICE_ID,
        })
    return headers


def write_control_plane_status(component, ok, url="", detail="", status_code=None):
    global last_control_plane_success_at, last_control_plane_error

    now = time.time()
    if ok:
        last_control_plane_success_at = now
        last_control_plane_error = ""
    else:
        last_control_plane_error = str(detail or "unknown error")[:320]

    payload = {
        "component": str(component or "control-plane"),
        "ok": bool(ok),
        "url": str(url or ""),
        "statusCode": status_code,
        "detail": str(detail or "")[:500],
        "tenantId": TENANT_ID,
        "deviceId": DEVICE_ID,
        "appBaseUrl": APP_BASE_URL,
        "adminBaseUrl": ADMIN_BASE_URL,
        "appSignalRConnected": bool(signalr_connected),
        "adminSignalRConnected": bool(admin_signalr_connected),
        "lastAppSignalRConnectedAtUtc": utc_iso_from_epoch(last_signalr_connected_at),
        "lastAdminSignalRConnectedAtUtc": utc_iso_from_epoch(last_admin_signalr_connected_at),
        "lastSuccessfulHeartbeatAtUtc": utc_iso_from_epoch(last_successful_heartbeat_at),
        "recordedAtUtc": datetime.now(timezone.utc).isoformat(),
        "identity": identity_mismatch_diagnostic(),
    }

    try:
        CONTROL_PLANE_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONTROL_PLANE_STATUS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(CONTROL_PLANE_STATUS_PATH, 0o600)
    except Exception as exc:
        print("Failed writing QRelia control-plane status:", exc)


def profile_urls_for_base(base_url):
    tenant_q = quote(TENANT_ID or "")
    device_q = quote(DEVICE_ID or "")
    return unique_non_empty([
        f"{base_url}{AMBIENT_PROFILE_API}/{device_q}?tenantId={tenant_q}&deviceId={device_q}",
        f"{base_url}{AMBIENT_PROFILE_API}/{device_q}",
        f"{base_url}{AMBIENT_PROFILE_API}?tenantId={tenant_q}&deviceId={device_q}",
    ])


def load_profile_from_api():
    if not DEVICE_ID:
        print("QRelia device is not provisioned yet; using local/default ambient profile.")
        return None

    errors = []
    timeout = max(1.5, min(PROFILE_API_TIMEOUT_SECONDS, 3.5))

    for base_url in control_plane_base_urls():
        for url in profile_urls_for_base(base_url):
            try:
                r = requests.get(url, timeout=timeout, headers=control_plane_headers())

                # A 404/405 usually means that specific host/path shape is not
                # where the admin API lives. Try the next candidate instead of
                # giving up and leaving the device locally-online/cloud-offline.
                if r.status_code in (404, 405):
                    errors.append(f"{url} -> HTTP {r.status_code}")
                    continue

                r.raise_for_status()
                write_control_plane_status("ambient-profile", True, url=url, status_code=r.status_code)
                return normalise_profile(r.json())
            except Exception as e:
                errors.append(f"{url} -> {str(e)[:180]}")

    detail = " | ".join(errors[-6:]) if errors else "No profile endpoints attempted"
    write_control_plane_status("ambient-profile", False, detail=detail)
    print("Failed loading ambient profile from any QRelia control-plane endpoint:", detail)
    return None


def _post_signalr_negotiate(base_url):
    params = {"negotiateVersion": "1"}
    if TENANT_ID:
        params["tenantId"] = TENANT_ID
    if DEVICE_ID:
        params["deviceId"] = DEVICE_ID

    response = requests.post(
        f"{base_url}{HUB_PATH}/negotiate",
        params=params,
        timeout=SIGNALR_NEGOTIATE_TIMEOUT_SECONDS,
        headers=control_plane_headers(),
    )
    response.raise_for_status()
    return response.json()


async def signalr_negotiate(base_url):
    # requests is synchronous; running it directly in asyncio causes visible LED frame stalls
    # whenever a network check/reconnect is slow or unavailable.
    return await asyncio.to_thread(_post_signalr_negotiate, base_url)


async def apply_ambient_profile(profile, persist=True, reason="profile"):
    global STATE_TO_ANIMATION, STALE_SECONDS, current_state, profile_preview_until
    global active_profile_fingerprint, active_device_mode, ambient_transient_until

    profile = normalise_profile(profile)
    new_fingerprint = profile_fingerprint(profile)
    profile_changed = bool(new_fingerprint and new_fingerprint != active_profile_fingerprint)
    previous_mode = active_device_mode
    active_device_mode = normalise_device_mode(profile.get("deviceMode"))
    mode_changed = normalise_device_mode(previous_mode) != active_device_mode

    STATE_TO_ANIMATION = profile["animations"].copy()
    STALE_SECONDS = int(profile["staleAfterSeconds"])

    try:
        strip.setBrightness(int(profile["brightness"] * 255 / 100))
        strip.show()
    except Exception as e:
        print("Failed applying strip brightness:", e)

    if persist:
        save_profile_to_disk(profile)

    if new_fingerprint:
        active_profile_fingerprint = new_fingerprint

    # Showroom is a device-level visual runtime, not an order-state preview. The
    # live SignalR profile push therefore becomes the mode switch itself. Orders
    # continue to be tracked underneath so returning to service mode is immediate.
    if showroom_mode_enabled():
        profile_preview_until = 0.0
        ambient_transient_until = 0.0
        print(
            f"Applying ambient profile ({reason}); mode={active_device_mode}; "
            f"showroom={'entered' if mode_changed else 'active'}"
        )
        await enter_showroom_visuals(force=mode_changed)
        return

    if mode_changed and normalise_device_mode(previous_mode) == DEVICE_MODE_SHOWROOM:
        print(f"Applying ambient profile ({reason}); leaving showroom for mode={active_device_mode}")
        await exit_showroom_visuals()
        return

    # Admin profile saves can preview any selected state briefly. Boot/API profile loads
    # must not replace real runtime states such as setup, connectionLost or error.
    # Polling uses the same preview behaviour only when the API profile actually changed,
    # so a safety poll cannot keep hijacking the order-derived idle/new/processing state.
    if reason == "signalr" or (reason == "api-poll" and profile_changed):
        current_state = normalise_state_name(profile.get("selectedState") or current_state or "idle")
        profile_preview_until = time.time() + 20.0

    next_animation = animation_for_state(current_state)
    print(
        f"Applying ambient profile ({reason}); mode={active_device_mode}, state={current_state}, "
        f"animation={next_animation}, processing={STATE_TO_ANIMATION.get('processing')}, "
        f"waiting={STATE_TO_ANIMATION.get('waiting')}"
    )
    await fade_transition(next_animation, duration=0.28)


async def set_ambient_state(state, duration=0.6, force=False, validity_check=None):
    global current_state
    state = normalise_state_name(state)
    animation_name = animation_for_state(state)

    if validity_check is not None and not validity_check():
        return False

    if not force and state == current_state and animation_name == current_animation_key:
        return True

    applied = await fade_transition(
        animation_name,
        duration=duration,
        validity_check=validity_check,
    )
    if not applied:
        return False

    current_state = state
    return True


async def play_transient_ambient_state(state, hold_seconds=None):
    global ambient_transient_until

    if showroom_mode_enabled():
        return

    hold_seconds = TRANSIENT_STATUS_SECONDS if hold_seconds is None else hold_seconds
    ambient_transient_until = time.time() + hold_seconds

    await set_ambient_state(state, duration=0.25, force=True)
    await asyncio.sleep(hold_seconds)

    # Completed/cancelled are moments, not long-running operational states.
    # After the acknowledgement pulse, return to the real order-derived state.
    ambient_transient_until = 0.0
    await set_ambient_state(get_led_strip_state(), duration=0.35, force=True)


async def load_initial_ambient_profile():
    local_profile = load_profile_from_disk()
    await apply_ambient_profile(local_profile, persist=False, reason="local/default")

    api_profile = load_profile_from_api()
    if api_profile:
        await apply_ambient_profile(api_profile, persist=True, reason="api")


async def refresh_ambient_profile_from_api(reason="manual", preview_on_change=True):
    profile = await asyncio.to_thread(load_profile_from_api)
    if not profile:
        return False

    changed = profile_fingerprint(profile) != active_profile_fingerprint
    if not changed:
        return True

    print(f"Ambient profile changed via API ({reason})")
    await apply_ambient_profile(profile, persist=True, reason="api-poll" if preview_on_change else "api")
    return True


async def ambient_profile_poll_loop():
    if PROFILE_POLL_SECONDS <= 0:
        return

    # Do not hammer the admin API on process start; startup already loads the profile.
    await asyncio.sleep(max(5.0, min(PROFILE_POLL_SECONDS, 10.0)))

    while True:
        try:
            await refresh_ambient_profile_from_api("periodic safety poll", preview_on_change=True)
        except Exception as exc:
            print("Ambient profile safety poll failed:", exc)

        await asyncio.sleep(PROFILE_POLL_SECONDS)



def _read_strip_frame():
    frame = []
    for i in range(strip.numPixels()):
        packed = strip.getPixelColor(i)
        frame.append(((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF))
    return frame


def _write_strip_frame(frame):
    for i, (r, g, b) in enumerate(frame):
        strip.setPixelColor(i, anim.rgb(r, g, b))


def _showroom_clamp01(value):
    return max(0.0, min(1.0, float(value)))


def _showroom_smoothstep(edge0, edge1, value):
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    value = _showroom_clamp01((value - edge0) / (edge1 - edge0))
    return value * value * (3.0 - 2.0 * value)


def _showroom_smootherstep(edge0, edge1, value):
    if edge0 == edge1:
        return 1.0 if value >= edge1 else 0.0
    value = _showroom_clamp01((value - edge0) / (edge1 - edge0))
    return value * value * value * (value * (value * 6.0 - 15.0) + 10.0)


def _showroom_gaussian(distance, width):
    width = max(0.0001, float(width))
    return math.exp(-0.5 * (float(distance) / width) ** 2)


def _showroom_circular_distance(a, b):
    distance = abs(float(a) - float(b)) % 1.0
    return min(distance, 1.0 - distance)


def _showroom_hash01(value):
    return (math.sin(float(value) * 12.9898 + 78.233) * 43758.5453) % 1.0


def _showroom_rgb_limit(colour):
    # Proportional limiting preserves hue at bright overlaps rather than clipping
    # cyan/violet/champagne highlights into flat white blocks.
    peak = max(255.0, *(float(channel) for channel in colour))
    scale = 255.0 / peak
    return tuple(max(0, min(255, int(channel * scale))) for channel in colour)


def _showroom_add(*colours):
    return tuple(sum(colour[channel] for colour in colours) for channel in range(3))


def _showroom_scale(colour, amount):
    return tuple(channel * amount for channel in colour)


def _showroom_blend(a, b, amount):
    amount = _showroom_clamp01(amount)
    return tuple(a[channel] + (b[channel] - a[channel]) * amount for channel in range(3))


def _showroom_hsv(hue, saturation, value):
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, _showroom_clamp01(saturation), _showroom_clamp01(value))
    return r * 255.0, g * 255.0, b * 255.0


def _showroom_liquid_silk(i, n, t, seed):
    """Layered QRelia cyan/violet silk with pearlescent moving folds."""
    x = i / max(1, n - 1)
    phase = seed * math.tau
    fold_a = 0.5 + 0.5 * math.sin(x * 10.8 - t * 0.78 + math.sin(t * 0.23 + phase) * 1.25)
    fold_b = 0.5 + 0.5 * math.sin(x * 17.6 + t * 0.58 + math.sin(x * 5.1 - t * 0.31) * 1.05 + phase * 0.7)
    depth = 0.5 + 0.5 * math.sin(x * 3.9 - t * 0.19 + phase)
    base = _showroom_blend((1, 4, 15), (4, 18, 42), depth)
    cyan = _showroom_scale((12, 228, 255), (fold_a ** 3.6) * 0.90)
    violet = _showroom_scale((118, 45, 255), (fold_b ** 4.2) * 0.68)
    pearl = 0.5 + 0.5 * math.sin(x * 37.0 - t * 2.65 + math.sin(t * 0.61 + phase))
    return _showroom_add(base, cyan, violet, _showroom_scale((218, 255, 255), pearl ** 15 * 0.58))


def _showroom_champagne_caustics(i, n, t, seed):
    """Warm champagne light refracting over black glass; luxury, never orange alarm."""
    x = i / max(1, n - 1)
    phase = seed * math.tau
    wave_a = math.sin(x * 15.0 - t * 0.69 + phase)
    wave_b = math.sin(x * 27.0 + t * 0.41 + math.sin(x * 4.0 + t * 0.25) * 1.5)
    caustic = _showroom_clamp01((wave_a * wave_b + 1.0) * 0.5) ** 3.0
    glow = (0.5 + 0.5 * math.sin(x * 5.5 - t * 0.22 + phase * 0.5)) ** 2
    colour = _showroom_add(
        _showroom_blend((5, 3, 2), (29, 15, 4), glow),
        _showroom_scale((255, 168, 38), caustic * 0.76),
        _showroom_scale((255, 229, 168), caustic ** 2 * 0.62),
    )
    glint = 0.5 + 0.5 * math.sin(i * 1.73 - t * 4.8 + phase)
    return _showroom_add(colour, _showroom_scale((255, 248, 220), glint ** 24 * 0.38))


def _showroom_deep_tide(i, n, t, seed):
    """Deep ocean currents with turquoise light shafts travelling at different depths."""
    x = i / max(1, n - 1)
    phase = seed * math.tau
    swell = 0.5 + 0.5 * math.sin(x * 7.2 - t * 0.36 + phase)
    undercurrent = 0.5 + 0.5 * math.sin(x * 20.5 + t * 0.52 + math.sin(t * 0.17) * 2.0)
    shaft = 0.5 + 0.5 * math.sin(x * 31.0 - t * 1.18 + phase * 0.4)
    base = _showroom_blend((0, 5, 16), (0, 26, 38), swell)
    return _showroom_add(
        base,
        _showroom_scale((0, 195, 210), undercurrent ** 4 * 0.72),
        _showroom_scale((60, 220, 255), shaft ** 10 * 0.52),
        _showroom_scale((44, 36, 155), (1.0 - swell) ** 3 * 0.35),
    )


def _showroom_prism_architecture(i, n, t, seed):
    """Mirrored spectral architecture expanding from the centre like light through crystal."""
    x = i / max(1, n - 1)
    centre = abs(x - 0.5) * 2.0
    phase = (t * (0.075 + seed * 0.018) + seed) % 1.0
    ring_a = _showroom_gaussian(abs(centre - phase * 1.23), 0.052)
    ring_b = _showroom_gaussian(abs(centre - ((phase + 0.43) % 1.0) * 1.16), 0.085)
    ring_c = _showroom_gaussian(abs(centre - ((phase + 0.72) % 1.0) * 1.08), 0.11)
    hue = 0.47 + x * 0.34 + math.sin(t * 0.19 + seed * 7.0) * 0.045
    prism = _showroom_hsv(hue, 0.82, 0.055 + ring_a * 0.90 + ring_b * 0.48 + ring_c * 0.24)
    centre_pearl = _showroom_gaussian(centre, 0.072) * (0.34 + 0.32 * (1 + math.sin(t * 0.68)) / 2)
    return _showroom_add(prism, _showroom_scale((235, 255, 255), centre_pearl))


def _showroom_comet_ballet(i, n, t, seed):
    """Four soft comets orbit with long photographic tails and crossing colour temperatures."""
    x = i / max(1, n)
    phase = seed * math.tau
    colour = _showroom_blend((1, 3, 12), (5, 11, 25), 0.5 + 0.5 * math.sin(x * 5.0 - t * 0.16))
    specs = (
        ((t * 0.052 + seed) % 1.0, 0.050, (12, 220, 255), 1.00),
        ((1.0 - t * 0.041 + 0.28 + seed * 0.3) % 1.0, 0.066, (120, 48, 255), 0.82),
        ((t * 0.033 + 0.58 + seed * 0.6) % 1.0, 0.043, (255, 178, 55), 0.67),
        ((1.0 - t * 0.026 + 0.79 + seed * 0.2) % 1.0, 0.085, (40, 235, 188), 0.54),
    )
    for position, width, comet_colour, strength in specs:
        distance = _showroom_circular_distance(x, position)
        halo = _showroom_gaussian(distance, width)
        core = _showroom_gaussian(distance, width * 0.23)
        colour = _showroom_add(
            colour,
            _showroom_scale(comet_colour, halo * 0.47 * strength),
            _showroom_scale((235, 255, 255), core * 0.86 * strength),
        )
    bead = 0.5 + 0.5 * math.sin(i * 1.17 - t * 3.1 + phase)
    return _showroom_add(colour, _showroom_scale((105, 215, 255), bead ** 19 * 0.20))


def _showroom_velvet_nebula(i, n, t, seed):
    """Slow velvet nebula with stable stars and changing blue-violet depth."""
    x = i / max(1, n - 1)
    phase = seed * math.tau
    cloud_a = 0.5 + 0.5 * math.sin(x * 8.3 + t * 0.22 + phase)
    cloud_b = 0.5 + 0.5 * math.sin(x * 13.7 - t * 0.31 + math.sin(x * 2.6 + t * 0.14) * 1.4)
    colour = _showroom_add(
        _showroom_blend((2, 2, 12), (9, 5, 27), cloud_a),
        _showroom_scale((72, 35, 190), cloud_a ** 3 * 0.52),
        _showroom_scale((0, 150, 220), cloud_b ** 4 * 0.44),
    )
    star_seed = _showroom_hash01(i * 1.93 + seed * 91.0)
    if star_seed > 0.80:
        speed = 0.45 + _showroom_hash01(i + seed * 31.0) * 1.25
        star_phase = _showroom_hash01(i * 2.7 + seed * 53.0) * math.tau
        twinkle = (0.5 + 0.5 * math.sin(t * speed + star_phase)) ** 7
        colour = _showroom_add(colour, _showroom_scale((205, 245, 255), (0.08 + twinkle * 0.75)))
    return colour


def _showroom_pearl_chorus(i, n, t, seed):
    """Interlocking pearl/cyan/gold voices that meet and separate like a visual score."""
    x = i / max(1, n - 1)
    phase = seed * math.tau
    colour = _showroom_blend((1, 5, 13), (4, 12, 24), 0.5 + 0.5 * math.sin(x * 4.0 + t * 0.13))
    voices = (
        (0.50 + 0.34 * math.sin(t * 0.38 + phase), 0.055, (225, 255, 255), 0.84),
        (0.50 + 0.40 * math.sin(t * 0.29 + phase + 2.1), 0.073, (20, 218, 255), 0.72),
        (0.50 + 0.37 * math.sin(t * 0.25 + phase + 4.0), 0.090, (255, 182, 70), 0.48),
        (0.50 + 0.43 * math.sin(t * 0.21 + phase + 5.2), 0.105, (112, 55, 255), 0.42),
    )
    for position, width, voice_colour, strength in voices:
        intensity = _showroom_gaussian(x - position, width)
        colour = _showroom_add(colour, _showroom_scale(voice_colour, intensity * strength))
    return colour


def _showroom_midnight_horizon(i, n, t, seed):
    """Broad cinematic horizons sweep the strip with slow colour-temperature changes."""
    x = i / max(1, n - 1)
    phase = seed * math.tau
    horizon_a = 0.5 + 0.5 * math.sin(x * 5.8 - t * 0.29 + phase)
    horizon_b = 0.5 + 0.5 * math.sin(x * 9.6 + t * 0.21 + phase * 0.37)
    cool = _showroom_blend((1, 6, 18), (3, 32, 48), horizon_a)
    warm_gate = horizon_b ** 5 * (0.35 + 0.65 * horizon_a)
    violet_gate = (1.0 - horizon_a) ** 4 * (0.3 + 0.7 * horizon_b)
    pearl_line = (0.5 + 0.5 * math.sin(x * 22.0 - t * 0.68 + phase)) ** 12
    return _showroom_add(
        cool,
        _showroom_scale((255, 151, 42), warm_gate * 0.43),
        _showroom_scale((91, 45, 220), violet_gate * 0.52),
        _showroom_scale((205, 248, 255), pearl_line * 0.36),
    )


SHOWROOM_SCENE_RENDERERS = (
    _showroom_liquid_silk,
    _showroom_champagne_caustics,
    _showroom_deep_tide,
    _showroom_prism_architecture,
    _showroom_comet_ballet,
    _showroom_velvet_nebula,
    _showroom_pearl_chorus,
    _showroom_midnight_horizon,
)


def _pick_next_showroom_scene(excluding):
    candidates = [index for index in range(len(SHOWROOM_SCENE_RENDERERS)) if index != excluding]
    return random.choice(candidates) if candidates else 0


def _showroom_transition_mask(style, x, progress, pixel_index, seed):
    if progress <= 0.0:
        return 0.0
    if progress >= 1.0:
        return 1.0
    progress = _showroom_smootherstep(0.0, 1.0, progress)
    if style % 5 == 0:
        moving_edge = progress * 1.28 - 0.14
        distortion = math.sin(x * 17.0 + progress * math.tau + seed * 9.0) * 0.045
        return _showroom_smoothstep(x - 0.09, x + 0.09, moving_edge + distortion)
    if style % 5 == 1:
        centre_distance = abs(x - 0.5) * 2.0
        return _showroom_smoothstep(centre_distance - 0.13, centre_distance + 0.08, progress * 1.16)
    if style % 5 == 2:
        edge_distance = min(x, 1.0 - x) * 2.0
        return _showroom_smoothstep(edge_distance - 0.12, edge_distance + 0.08, progress * 1.14)
    if style % 5 == 3:
        threshold = _showroom_hash01(pixel_index * 1.71 + seed * 113.0)
        return _showroom_smoothstep(threshold - 0.16, threshold + 0.16, progress)
    # Interlaced curtain: alternate LED groups arrive just a little out of phase.
    lane_delay = (pixel_index % 7) / 28.0
    return _showroom_smoothstep(x - 0.10, x + 0.10, progress * 1.25 - lane_delay)


def _showroom_transition_pearl(style, x, progress):
    progress = _showroom_smootherstep(0.0, 1.0, progress)
    if style % 5 == 0:
        distance = abs(x - (progress * 1.28 - 0.14))
    elif style % 5 == 1:
        distance = abs(abs(x - 0.5) * 2.0 - progress)
    elif style % 5 == 2:
        distance = abs(min(x, 1.0 - x) * 2.0 - progress)
    else:
        return 0.0
    return _showroom_gaussian(distance, 0.032) * math.sin(progress * math.pi)


def _initialise_showroom_sequence(capture_entry=True):
    global showroom_scene_started_at, showroom_scene_duration, showroom_scene_index
    global showroom_next_scene_index, showroom_scene_seed, showroom_next_scene_seed
    global showroom_transition_style, showroom_entry_started_at, showroom_entry_frame

    showroom_scene_index = random.randrange(len(SHOWROOM_SCENE_RENDERERS))
    showroom_next_scene_index = _pick_next_showroom_scene(showroom_scene_index)
    showroom_scene_seed = random.random()
    showroom_next_scene_seed = random.random()
    showroom_transition_style = random.randrange(5)
    showroom_scene_started_at = time.monotonic()
    hold_min = max(4.0, min(SHOWROOM_SCENE_MIN_SECONDS, SHOWROOM_SCENE_MAX_SECONDS))
    hold_max = max(hold_min, SHOWROOM_SCENE_MAX_SECONDS)
    showroom_scene_duration = random.uniform(hold_min, hold_max)
    showroom_entry_started_at = showroom_scene_started_at
    showroom_entry_frame = _read_strip_frame() if capture_entry else None
    print(
        "Showroom generative cinema started: "
        f"{SHOWROOM_SCENE_NAMES[showroom_scene_index]} -> {SHOWROOM_SCENE_NAMES[showroom_next_scene_index]}"
    )


def _advance_showroom_scene(now):
    global showroom_scene_started_at, showroom_scene_duration, showroom_scene_index
    global showroom_next_scene_index, showroom_scene_seed, showroom_next_scene_seed
    global showroom_transition_style

    showroom_scene_index = showroom_next_scene_index
    showroom_scene_seed = showroom_next_scene_seed
    showroom_next_scene_index = _pick_next_showroom_scene(showroom_scene_index)
    showroom_next_scene_seed = random.random()
    showroom_transition_style = random.randrange(5)
    showroom_scene_started_at = now
    hold_min = max(4.0, min(SHOWROOM_SCENE_MIN_SECONDS, SHOWROOM_SCENE_MAX_SECONDS))
    hold_max = max(hold_min, SHOWROOM_SCENE_MAX_SECONDS)
    showroom_scene_duration = random.uniform(hold_min, hold_max)
    print(
        "Showroom scene: "
        f"{SHOWROOM_SCENE_NAMES[showroom_scene_index]} -> {SHOWROOM_SCENE_NAMES[showroom_next_scene_index]}"
    )


async def _render_showroom_strip_frame(now=None):
    """Render one showroom strip frame without monopolising the asyncio thread.

    The generative strip is the single heaviest Python render path in the service.
    Yielding between small pixel batches keeps the LCD deadline runnable while
    preserving one atomic strip.show() at the end of the completed frame.
    """
    global showroom_entry_frame

    now = time.monotonic() if now is None else now
    if showroom_scene_duration <= 0.0:
        _initialise_showroom_sequence(capture_entry=True)
        now = time.monotonic()

    local_time = now - showroom_scene_started_at
    if local_time >= showroom_scene_duration:
        _advance_showroom_scene(now)
        local_time = 0.0

    n = strip.numPixels()
    current_renderer = SHOWROOM_SCENE_RENDERERS[showroom_scene_index]
    next_renderer = SHOWROOM_SCENE_RENDERERS[showroom_next_scene_index]
    transition_seconds = min(max(0.6, SHOWROOM_DISSOLVE_SECONDS), max(0.7, showroom_scene_duration * 0.42))
    transition_start = max(0.0, showroom_scene_duration - transition_seconds)
    transition_progress = 0.0
    if local_time >= transition_start:
        transition_progress = (local_time - transition_start) / max(0.01, transition_seconds)

    global_t = now
    entry_progress = _showroom_smootherstep(
        0.0,
        1.0,
        (now - showroom_entry_started_at) / max(0.1, SHOWROOM_ENTRY_SECONDS),
    )

    frame = []
    for i in range(n):
        x = i / max(1, n - 1)
        current_colour = current_renderer(i, n, global_t, showroom_scene_seed)
        colour = current_colour

        if transition_progress > 0.0:
            next_colour = next_renderer(i, n, global_t, showroom_next_scene_seed)
            mask = _showroom_transition_mask(
                showroom_transition_style,
                x,
                transition_progress,
                i,
                showroom_next_scene_seed,
            )
            colour = _showroom_blend(current_colour, next_colour, mask)
            pearl = _showroom_transition_pearl(showroom_transition_style, x, transition_progress)
            colour = _showroom_add(colour, _showroom_scale((232, 255, 255), pearl * 0.62))

        colour = _showroom_rgb_limit(colour)
        if showroom_entry_frame is not None and entry_progress < 1.0 and i < len(showroom_entry_frame):
            colour = _showroom_rgb_limit(_showroom_blend(showroom_entry_frame[i], colour, entry_progress))
        frame.append(colour)

        # Bound the longest uninterrupted CPU burst. On the Pi Zero 2 W this is
        # more important than raw strip FPS: the LCD loop can now hit its
        # next deadline even during the expensive two-scene dissolve window.
        if (i + 1) < n and (i + 1) % SHOWROOM_STRIP_COOPERATIVE_PIXELS == 0:
            await asyncio.sleep(0)

    if entry_progress >= 1.0:
        showroom_entry_frame = None

    _write_strip_frame(frame)


async def _dissolve_showroom_to_operational(new_animation, duration=0.72):
    """Leave generative cinema cleanly even when the operational animation name did not change."""
    global animator, current_animation_key

    new_animation = safe_animation_name(new_animation, fallback=DEFAULT_STATE_TO_ANIMATION["idle"])
    duration = max(0.25, float(duration))
    from_frame = _read_strip_frame()
    next_animator = anim.LedAnimator()
    next_animator.set_animation(new_animation)
    next_animator.started_at = time.time()
    steps = max(18, int(duration / max(1.0 / 60.0, anim.FRAME_DELAY)))
    delay = duration / steps

    async with transition_lock:
        for step in range(steps):
            next_animator.step()
            target_frame = _read_strip_frame()
            p = _showroom_smootherstep(0.0, 1.0, (step + 1) / steps)
            blended = [
                (
                    int(old[0] + (new[0] - old[0]) * p),
                    int(old[1] + (new[1] - old[1]) * p),
                    int(old[2] + (new[2] - old[2]) * p),
                )
                for old, new in zip(from_frame, target_frame)
            ]
            _write_strip_frame(blended)
            anim.show()
            await asyncio.sleep(delay)

        animator = next_animator
        current_animation_key = new_animation
        animator.step()
        anim.show()


async def enter_showroom_visuals(force=False):
    global system_ambient_state, _lcd_event

    # Orders/connectivity keep updating underneath; showroom owns visuals only.
    # Profile edits made while already in showroom must not restart the film.
    system_ambient_state = None
    _lcd_event = None
    if force or showroom_scene_duration <= 0.0:
        _initialise_showroom_sequence(capture_entry=True)
    lcd_update()


async def exit_showroom_visuals():
    global current_state, showroom_scene_duration, showroom_entry_frame

    showroom_scene_duration = 0.0
    showroom_entry_frame = None
    # Hand visual ownership back to the live LCD scene immediately.
    lcd_update()

    target_state = "connectionLost" if connection_loss_is_strip_confirmed() else get_order_ambient_state()
    current_state = target_state
    await _dissolve_showroom_to_operational(animation_for_state(target_state), duration=0.78)


async def led_strip_loop():
    next_frame = time.perf_counter()
    previous_showroom_mode = showroom_mode_enabled()

    while True:
        is_showroom = showroom_mode_enabled()
        if is_showroom != previous_showroom_mode:
            # Never carry cadence debt across a device-mode switch. In
            # particular, leaving showroom must not trigger an immediate burst
            # of catch-up strip work while the LCD is changing scene
            # back to the operational renderer.
            next_frame = time.perf_counter()
            previous_showroom_mode = is_showroom

        if not transition_lock.locked():
            if is_showroom:
                # Give an LCD frame that became runnable on this same cadence
                # first refusal on the event loop before starting strip maths.
                await asyncio.sleep(0)
                await _render_showroom_strip_frame(time.monotonic())
                # Mode can change while the cooperative renderer is yielding.
                # Never commit one final stale showroom strip frame after live
                # mode has already taken ownership back.
                if showroom_mode_enabled():
                    anim.show()
            else:
                animator.step()
                anim.show()

        frame_delay = SHOWROOM_STRIP_FRAME_DELAY if is_showroom else anim.FRAME_DELAY
        next_frame += frame_delay

        # Precise timing compensation. The normal animation path is untouched;
        # showroom has a small CPU reserve because its per-frame renderer is much
        # more expensive and otherwise starves the independent LCD cadence.
        now = time.perf_counter()
        sleep_time = next_frame - now

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            # Drop schedule debt rather than bursting stale frames. A tiny
            # showroom-only rest is important on Pi Zero: sleep(0) can resume
            # the display renderer gets a fair scheduling slice under heavy strip scenes.
            next_frame = now
            await asyncio.sleep(0.0015 if is_showroom else 0)


def get_order_ambient_state():
    if has_stale_pending():
        return "stale"
    if pending_count > 0:
        return "newOrder"
    if waiting_count > 0:
        return "waiting"
    if processing_count > 0:
        return "processing"
    return "idle"


def get_led_strip_state():
    if system_ambient_state in SYSTEM_AMBIENT_STATES:
        return system_ambient_state
    return get_order_ambient_state()


async def set_system_ambient_state(state, duration=0.28, force=False):
    global system_ambient_state

    if showroom_mode_enabled():
        system_ambient_state = None
        return False

    state = normalise_state_name(state)
    if state not in SYSTEM_AMBIENT_STATES:
        await clear_system_ambient_state(duration=duration)
        return True

    if state == "connectionLost":
        # Do not commit the system override before the guarded fade succeeds.
        # Otherwise a reconnect during fade-out queues recovery behind an amber
        # fade-in and produces a visible one-second connection warning flash.
        if not connection_loss_is_strip_confirmed():
            return False

        previous_system_state = system_ambient_state
        applied = await set_ambient_state(
            state,
            duration=duration,
            force=force,
            validity_check=connection_loss_is_strip_confirmed,
        )
        if not applied:
            return False

        # Close the tiny gap between the final guarded frame and committing the
        # override. If recovery landed there, immediately restore the real state.
        if not connection_loss_is_strip_confirmed():
            fallback_state = (
                previous_system_state
                if previous_system_state in SYSTEM_AMBIENT_STATES
                else get_order_ambient_state()
            )
            await set_ambient_state(fallback_state, duration=0.18, force=True)
            return False

        system_ambient_state = state
        return True

    system_ambient_state = state
    await set_ambient_state(state, duration=duration, force=force)
    return True


async def clear_system_ambient_state(duration=0.35):
    global system_ambient_state

    if system_ambient_state is not None:
        print(f"Clearing system ambient state: {system_ambient_state}")

    system_ambient_state = None
    if showroom_mode_enabled():
        return

    await set_ambient_state(get_order_ambient_state(), duration=duration, force=True)

async def led_strip_state_loop():
    current = None
    last_state = None
    stable_since = time.time()

    while True:

        if showroom_mode_enabled():
            await asyncio.sleep(0.1)
            continue

        if transition_lock.locked():
            await asyncio.sleep(0.1)
            continue

        now = time.time()

        if now < profile_preview_until or now < ambient_transient_until:
            await asyncio.sleep(0.15)
            continue

        state = get_led_strip_state()

        if state != last_state:
            last_state = state
            stable_since = time.time()

        # only accept state if stable for 0.3s
        if state != current and (time.time() - stable_since) > 0.3:
            current = state
            await set_ambient_state(state)

        await asyncio.sleep(0.1)

async def fade_to_black(duration=0.4):
    steps = 25
    delay = duration / steps

    async with transition_lock:
        for i in range(steps):
            factor = 1.0 - (i / steps)
            anim.fade_all(factor)
            anim.show()
            await asyncio.sleep(delay)

async def fade_transition(new_animation, duration=0.6, validity_check=None):
    global animator, current_animation_key

    new_animation = safe_animation_name(new_animation)

    def transition_is_valid():
        if validity_check is None:
            return True
        try:
            return bool(validity_check())
        except Exception as exc:
            print("Ambient transition validity check failed:", exc)
            return False

    async def fade_current_back_in():
        # The guarded target became invalid during fade-out. Restore the current
        # animation without ever switching to the now-stale target animation.
        for i in range(steps):
            factor = (i + 1) / steps
            animator.step()
            anim.fade_all(factor)
            anim.show()
            await asyncio.sleep(delay)

    if not transition_is_valid():
        return False

    # Critical fix: do not fade the same animation repeatedly.
    # Reconnect loops/profile refreshes were causing a visible fade every few seconds.
    if new_animation == current_animation_key:
        return True

    steps = max(8, int(24 * max(0.18, duration)))
    delay = max(0.003, duration / (steps * 2))

    async with transition_lock:
        if not transition_is_valid():
            return False
        if new_animation == current_animation_key:
            return True

        previous_animation_key = current_animation_key

        # ---------- FADE OUT ----------
        for i in range(steps):
            if not transition_is_valid():
                await fade_current_back_in()
                return False

            factor = 1.0 - (i / steps)
            animator.step()
            anim.fade_all(factor)
            anim.show()
            await asyncio.sleep(delay)

        if not transition_is_valid():
            await fade_current_back_in()
            return False

        # Use a fresh animator per animation to clear meteor/scanner/rain memory.
        animator = anim.LedAnimator()
        animator.set_animation(new_animation)
        animator.started_at = time.time()
        current_animation_key = new_animation

        # ---------- FADE IN ----------
        for i in range(steps):
            if not transition_is_valid():
                # Recovery arrived after the target animator was selected but
                # before its fade completed. Restore the prior animation inside
                # the same lock so no queued state loop can expose the stale one.
                restored_animation = safe_animation_name(
                    previous_animation_key,
                    fallback=DEFAULT_STATE_TO_ANIMATION["idle"],
                )
                animator = anim.LedAnimator()
                animator.set_animation(restored_animation)
                animator.started_at = time.time()
                current_animation_key = restored_animation
                await fade_current_back_in()
                return False

            factor = i / steps
            animator.step()
            anim.fade_all(factor)
            anim.show()
            await asyncio.sleep(delay)

    return True


# =========================
# LCD DISPLAY
# =========================
# The LCD is a first-class operational surface. Network/SignalR/order logic never
# waits for rendering; the renderer consumes snapshots of the runtime state.

LCD_TARGET_FPS = max(15.0, min(60.0, float(os.environ.get("QRELIA_LCD_FPS", "30"))))
LCD_NEW_ORDER_EVENT_SECONDS = float(os.environ.get("QRELIA_LCD_NEW_ORDER_EVENT_SECONDS", "6.5"))
LCD_STATUS_EVENT_SECONDS = float(os.environ.get("QRELIA_LCD_STATUS_EVENT_SECONDS", "4.2"))
LCD_NETWORK_CACHE_SECONDS = float(os.environ.get("QRELIA_LCD_NETWORK_CACHE_SECONDS", "1.5"))

_lcd_event = None
_lcd_network_cache = {"updated": 0.0, "ssid": "", "ip": ""}
_lcd_display = None


def _short_order_id(order_id):
    value = str(order_id or "").strip()
    if not value:
        return "--"
    if value.isdigit():
        return value[-6:]
    return value.split("-")[0][:8].upper()


def _cached_network_info():
    return _lcd_network_cache["ssid"], _lcd_network_cache["ip"]


async def lcd_network_info_loop():
    while True:
        try:
            ssid = await asyncio.to_thread(read_connected_ssid)
            ip_address = await asyncio.to_thread(read_ip_address)
            _lcd_network_cache["updated"] = time.monotonic()
            _lcd_network_cache["ssid"] = ssid
            _lcd_network_cache["ip"] = ip_address
        except Exception as exc:
            print("LCD network info refresh failed:", exc)
        await asyncio.sleep(max(0.75, LCD_NETWORK_CACHE_SECONDS))


def show_lcd_event(kind, order_id="", location="", wait_minutes=None, item_count=None, notes="", duration=None):
    global _lcd_event
    kind = str(kind or "update").strip().lower()
    if duration is None:
        duration = LCD_NEW_ORDER_EVENT_SECONDS if kind == "new" else LCD_STATUS_EVENT_SECONDS
    now = time.monotonic()
    _lcd_event = {
        "kind": kind,
        "order_id": str(order_id or ""),
        "location": str(location or ""),
        "wait_minutes": wait_minutes,
        "item_count": item_count,
        "notes": str(notes or ""),
        "started": now,
        "until": now + max(0.25, float(duration)),
    }
    lcd_update()


def _active_lcd_event():
    global _lcd_event
    if _lcd_event and time.monotonic() >= float(_lcd_event.get("until", 0.0)):
        _lcd_event = None
    return dict(_lcd_event) if _lcd_event else None


def _active_orders_for_lcd():
    rows = []
    now = time.time()
    for oid, status in order_status.items():
        phase = order_phase(status)
        if phase not in ("pending", "processing", "waiting"):
            continue
        rows.append({
            "id": str(oid),
            "shortId": _short_order_id(oid),
            "status": str(status or ""),
            "phase": phase,
            "location": str(order_location_label.get(oid) or ""),
            "notes": str(order_notes.get(oid) or ""),
            "itemCount": order_item_count.get(oid),
            "waitMinutes": order_wait_minutes.get(oid),
            "ageSeconds": max(0.0, now - float(order_created_time.get(oid) or now)),
            "pendingAgeSeconds": max(0.0, now - float(order_pending_time.get(oid) or now)) if phase == "pending" else 0.0,
        })
    phase_rank = {"pending": 0, "waiting": 1, "processing": 2}
    rows.sort(key=lambda row: (phase_rank.get(row["phase"], 9), -row["ageSeconds"]))
    return rows


def _setup_display_state():
    state = read_json_file(SETUP_DISPLAY_STATE_PATH)
    if not state:
        return {}
    try:
        updated = float(state.get("updatedAtUnix") or 0)
    except (TypeError, ValueError):
        updated = 0
    # Setup status is transient coordination state. Ignore ancient leftovers from
    # a previous boot once the device is otherwise live.
    if updated and time.time() - updated > 3600:
        return {}
    return state


def _setup_runtime_copy(title, message, mode, display_state):
    state = str(display_state.get("state") or "").strip().lower()
    detail = str(display_state.get("message") or "").strip()
    titles = {
        "setup_starting": "Preparing setup",
        "setup_ready": "QRelia Setup",
        "phone_connected": "Setup connected",
        "wifi_form": "Venue WiFi",
        "saving_wifi": "Saving WiFi",
        "wifi_failed": "WiFi needs attention",
        "wifi_verifying": "Checking WiFi",
        "wifi_verified": "Venue WiFi OK",
        "rebooting": "Restarting QRelia",
        "reset_armed": "Reset QRelia",
        "error": "Setup problem",
    }
    if state in titles:
        title = titles[state]
    if detail:
        message = detail
    if state in ("pairing", "pairing_failed"):
        mode = "pairing"
    elif state:
        mode = "setup"
    return title, message, mode


def _lcd_runtime_snapshot():
    ssid, ip_address = _cached_network_info()
    failure = current_provisioning_failure()
    setup_state = _setup_display_state() if device_is_in_setup_flow() else {}
    title, message, mode = _setup_runtime_copy(
        runtime_status_title, runtime_status_message, runtime_status_mode, setup_state
    )
    effective_provisioned = bool(device_has_runtime_identity() and not device_is_in_setup_flow())
    return {
        "deviceName": DEVICE_NAME or DEVICE_IDENTIFIER or "QRelia Device",
        "deviceIdentifier": DEVICE_IDENTIFIER,
        "tenantId": TENANT_ID,
        "deviceId": DEVICE_ID,
        "firmwareVersion": FIRMWARE_VERSION,
        "runtimeTitle": title,
        "runtimeMessage": message,
        "runtimeMode": mode,
        "deviceMode": active_device_mode,
        "showroom": showroom_mode_enabled(),
        "signalRConnected": bool(signalr_connected),
        "adminSignalRConnected": bool(admin_signalr_connected),
        "connectionLost": bool(connection_loss_is_visually_confirmed()),
        "systemAmbientState": system_ambient_state,
        "ambientState": get_led_strip_state(),
        "selectedState": current_state,
        "profilePreview": time.time() < profile_preview_until,
        "stale": bool(has_stale_pending()),
        "staleAfterSeconds": int(STALE_SECONDS),
        "pendingCount": int(pending_count),
        "processingCount": int(processing_count),
        "waitingCount": int(waiting_count),
        "activeOrders": _active_orders_for_lcd(),
        "event": _active_lcd_event(),
        "ssid": str(setup_state.get("ssid") or ssid),
        "ipAddress": str(setup_state.get("ip") or ip_address),
        "provisioned": effective_provisioned,
        "setupState": str(setup_state.get("state") or ""),
        "setupSsid": SETUP_WIFI_SSID,
        "setupPassword": SETUP_WIFI_PASSWORD,
        "provisioningFailure": failure,
        "lastUpdateTime": last_update_time,
        "lastSignalRConnectedAt": last_signalr_connected_at,
    }


def lcd_update():
    if _lcd_display is not None:
        _lcd_display.update(_lcd_runtime_snapshot())


async def lcd_refresh_loop():
    global _lcd_display
    try:
        _lcd_display = QReliaLCDDisplay(target_fps=LCD_TARGET_FPS)
    except Exception as exc:
        print("LCD initialisation failed; runtime will continue headless:", exc)
        _lcd_display = None
        return

    print(f"QRelia LCD renderer started at target {LCD_TARGET_FPS:.1f} FPS")
    frame_delay = 1.0 / LCD_TARGET_FPS
    try:
        while True:
            started = time.perf_counter()
            lcd_update()
            if not _lcd_display.render():
                print("LCD requested runtime exit")
                return
            elapsed = time.perf_counter() - started
            await asyncio.sleep(max(0.001, frame_delay - elapsed))
    finally:
        try:
            _lcd_display.close()
        except Exception:
            pass

def norm(s):
    return (s or "").lower()


def order_phase(status):
    s = norm(status).strip().replace(" ", "").replace("-", "").replace("_", "")

    if s in ("pending", "new", "neworder", "awaitingacceptance"):
        return "pending"

    if s in ("waiting", "queued", "onhold"):
        return "waiting"

    if s in ("processing", "inprogress", "accepted", "preparing", "beingprepared"):
        return "processing"

    if s in ("completed", "complete", "delivered", "done", "fulfilled", "served"):
        return "completed"

    if s in ("cancelled", "canceled", "declined", "rejected", "void"):
        return "cancelled"

    return s


def normalise_wait_minutes(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None

    return minutes if 1 <= minutes <= 240 else None


def has_stale_pending():
    now = time.time()
    for oid, st in order_status.items():
        if order_phase(st) == "pending":
            ts = order_pending_time.get(oid)
            if ts and (now - ts) >= STALE_SECONDS:
                return True
    return False


def count_order_phases():
    p = 0
    pr = 0
    w = 0
    for st in order_status.values():
        phase = order_phase(st)
        if phase == "pending":
            p += 1
        elif phase == "processing":
            pr += 1
        elif phase == "waiting":
            w += 1
    return p, pr, w

# =========================
# LOAD EXISTING ORDERS
# =========================


def parse_order_timestamp(order, keys=None):
    keys = keys or (
        "pendingSince", "PendingSince",
        "createdAt", "CreatedAt",
        "createdOn", "CreatedOn",
        "orderDate", "OrderDate",
        "createdUtc", "CreatedUtc",
    )

    for key in keys:
        value = order.get(key) if isinstance(order, dict) else None
        if not value:
            continue

        try:
            if isinstance(value, (int, float)):
                # Accept normal Unix seconds and millisecond timestamps.
                return float(value) / 1000.0 if value > 10_000_000_000 else float(value)

            text = str(value).strip()
            if not text:
                continue
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"

            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except Exception:
            continue

    return None


def fetch_existing_orders_snapshot():
    if not TENANT_ID:
        raise RuntimeError("QRelia tenant is not provisioned yet.")

    params = {"tenantId": TENANT_ID}
    if DEVICE_ID:
        params["deviceId"] = DEVICE_ID

    headers = {
        "X-Tenant-Id": TENANT_ID,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    if DEVICE_ID:
        headers["X-Device-Id"] = DEVICE_ID

    r = requests.get(
        BASE_URL + ORDERS_API,
        params=params,
        timeout=ORDERS_API_TIMEOUT_SECONDS,
        headers=headers,
    )

    if r.status_code == 204 or not r.text.strip():
        return []

    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict):
        data = data.get("orders") or data.get("Orders") or data.get("items") or data.get("Items") or []

    if not isinstance(data, list):
        raise ValueError(f"Unexpected orders API payload: {type(data).__name__}")

    return data


def apply_order_snapshot(data):
    global pending_count, processing_count, waiting_count, last_update_time

    previous_status = order_status.copy()
    previous_pending_time = order_pending_time.copy()
    previous_status_updated_time = order_status_updated_time.copy()
    previous_wait_minutes = order_wait_minutes.copy()
    previous_location_label = order_location_label.copy()
    previous_item_count = order_item_count.copy()
    previous_notes = order_notes.copy()
    previous_created_time = order_created_time.copy()

    order_status.clear()
    order_pending_time.clear()
    order_status_updated_time.clear()
    order_wait_minutes.clear()
    order_location_label.clear()
    order_item_count.clear()
    order_notes.clear()
    order_created_time.clear()

    now = time.time()

    for order in data:

        if not isinstance(order, dict):
            continue

        oid = str(
            order.get("id") or order.get("Id") or
            order.get("orderId") or order.get("OrderId") or
            order.get("orderID") or order.get("OrderID") or ""
        )
        st = str(
            order.get("status") or order.get("Status") or
            order.get("orderStatus") or order.get("OrderStatus") or
            order.get("state") or order.get("State") or ""
        )

        if not oid:
            continue

        phase = order_phase(st)
        order_status[oid] = st
        order_created_time[oid] = (
            parse_order_timestamp(order)
            or previous_created_time.get(oid)
            or now
        )
        order_status_updated_time[oid] = (
            parse_order_timestamp(order, (
                "updatedAt", "UpdatedAt",
                "statusUpdatedAt", "StatusUpdatedAt",
                "modifiedAt", "ModifiedAt",
                "createdAt", "CreatedAt",
                "orderDate", "OrderDate",
            ))
            or previous_status_updated_time.get(oid)
            or now
        )

        location_label = str(
            order.get("locationLabel") or order.get("LocationLabel") or
            previous_location_label.get(oid) or ""
        ).strip()
        if location_label:
            order_location_label[oid] = location_label

        # The active-order snapshot intentionally remains compact and currently
        # does not include notes/items. Retain richer live SignalR context when
        # the same order is present in both sources.
        if oid in previous_item_count:
            order_item_count[oid] = previous_item_count[oid]
        if oid in previous_notes:
            order_notes[oid] = previous_notes[oid]

        if phase == "pending":
            order_pending_time[oid] = previous_pending_time.get(oid) or parse_order_timestamp(order) or now
        elif phase == "waiting":
            wait_minutes = normalise_wait_minutes(
                order.get("estimatedWaitMinutes") or order.get("EstimatedWaitMinutes")
            )
            if wait_minutes is not None:
                order_wait_minutes[oid] = wait_minutes
            elif oid in previous_wait_minutes:
                order_wait_minutes[oid] = previous_wait_minutes[oid]

    # Snapshot data must never convert a live order into idle just because the
    # REST endpoint returned a partial active-order list. SignalR is the live
    # source of truth for orders seen by this device. A local pending,
    # processing or waiting
    # order remains active until an explicit completed/cancelled/delivered/declined
    # status arrives, or until the safety grace window expires.
    if PRESERVE_ACTIVE_WHEN_ABSENT_FROM_SNAPSHOT:
        restored_active = 0

        for oid, old_status in previous_status.items():
            old_phase = order_phase(old_status)

            if old_phase not in ("pending", "processing", "waiting"):
                continue

            if oid in order_status:
                # Snapshot explicitly knows this order. Do not override it.
                continue

            status_since = previous_status_updated_time.get(oid) or previous_pending_time.get(oid) or now
            if now - status_since > ACTIVE_ORDER_SNAPSHOT_GRACE_SECONDS:
                print(f"Dropping stale preserved active order after grace window: id={oid}, status={old_status}")
                continue

            order_status[oid] = old_status
            order_status_updated_time[oid] = status_since
            order_created_time[oid] = previous_created_time.get(oid) or status_since

            if oid in previous_location_label:
                order_location_label[oid] = previous_location_label[oid]
            if oid in previous_item_count:
                order_item_count[oid] = previous_item_count[oid]
            if oid in previous_notes:
                order_notes[oid] = previous_notes[oid]

            if old_phase == "pending":
                order_pending_time[oid] = previous_pending_time.get(oid) or status_since
            elif old_phase == "waiting" and oid in previous_wait_minutes:
                order_wait_minutes[oid] = previous_wait_minutes[oid]

            restored_active += 1

        if restored_active:
            print(f"Preserved {restored_active} active order(s) absent from snapshot")

    pending_count, processing_count, waiting_count = count_order_phases()
    last_update_time = time.time()

    print(
        "Order snapshot loaded: "
        f"pending={pending_count}, processing={processing_count}, "
        f"waiting={waiting_count}, total={len(order_status)}"
    )


def load_existing_orders():
    try:
        apply_order_snapshot(fetch_existing_orders_snapshot())
        return True
    except Exception as e:
        print("Failed loading existing orders:", e)
        return False


async def refresh_orders_from_api(reason="manual"):
    print(f"Refreshing order snapshot ({reason})")
    try:
        snapshot = await asyncio.to_thread(fetch_existing_orders_snapshot)
        apply_order_snapshot(snapshot)
        return True
    except Exception as e:
        print("Failed loading existing orders:", e)
        return False



def read_connected_ssid():
    try:
        result = subprocess.run(
            ["iwgetid", "-r"],
            text=True,
            capture_output=True,
            timeout=0.7,
        )
        return result.stdout.strip()
    except Exception:
        return ""


async def local_wifi_watch_loop():
    global local_wifi_disconnected_since

    if LOCAL_WIFI_CHECK_SECONDS <= 0:
        return

    missing_since = None

    while True:
        await asyncio.sleep(LOCAL_WIFI_CHECK_SECONDS)

        # iwgetid can briefly return empty under load. Confirm with the current IP
        # and require a sustained miss before treating this as a local outage.
        ssid = await asyncio.to_thread(read_connected_ssid)
        if ssid:
            missing_since = None
            local_wifi_disconnected_since = None
            continue

        ip_address = await asyncio.to_thread(read_ip_address)
        if ip_address:
            missing_since = None
            local_wifi_disconnected_since = None
            continue

        now = time.time()
        if missing_since is None:
            missing_since = now
            continue

        if now - missing_since < max(0.0, LOCAL_WIFI_LOSS_CONFIRM_SECONDS):
            continue

        if local_wifi_disconnected_since is None:
            local_wifi_disconnected_since = missing_since
            print(
                "Local Wi-Fi loss confirmed after "
                f"{now - missing_since:.1f}s without SSID or IP"
            )


async def connection_loss_visual_loop():
    global connection_lost_visible

    while True:
        await asyncio.sleep(0.25)

        if showroom_mode_enabled():
            connection_lost_visible = False
            continue

        started_at = connection_loss_started_at()
        if started_at is None:
            if connection_lost_visible or system_ambient_state == "connectionLost":
                connection_lost_visible = False
                if system_ambient_state == "connectionLost":
                    await clear_system_ambient_state(duration=0.24)
            continue

        now = time.time()
        if now < profile_preview_until or now < ambient_transient_until:
            continue

        if not connection_lost_visible and connection_loss_is_strip_confirmed(now):
            connection_lost_visible = await set_system_ambient_state(
                "connectionLost",
                duration=0.28,
                force=True,
            )


async def order_snapshot_reconciliation_loop():
    if ORDER_RECONCILE_SECONDS <= 0:
        return

    while True:
        await asyncio.sleep(ORDER_RECONCILE_SECONDS)

        # The REST snapshot is only a safety net for a healthy live SignalR session.
        # Do not run it before the first hub connection or while reconnecting. Apart
        # from avoiding unnecessary HTTP work, this prevents a transient REST timeout
        # from being mistaken for a device/cloud outage.
        if not signalr_connected or connection_loss_started_at() is not None:
            continue

        if await refresh_orders_from_api("periodic reconciliation"):
            lcd_update()
            if system_ambient_state == "error":
                await clear_system_ambient_state(duration=0.35)
        else:
            # SignalR is the primary order path and is still connected here. A single
            # failed reconciliation request is therefore degraded redundancy, not a
            # venue-facing device error. Previously this switched the strip to
            # pulse_amber (255,110,20), causing the unexplained red/orange flash while
            # the LCD correctly remained READY. Keep the current ambient state and
            # let the next periodic snapshot retry silently. Sustained socket/Wi-Fi
            # loss is handled separately by connection_loss_visual_loop().
            print(
                "Periodic order reconciliation failed while SignalR is healthy; "
                "retaining current ambient visuals"
            )

# =========================
# CLOUD HEARTBEAT
# =========================


def read_ip_address():
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            text=True,
            capture_output=True,
            timeout=0.7,
        )
        return (result.stdout.strip().split() or [""])[0]
    except Exception:
        return ""


def utc_iso_from_epoch(epoch):
    if not epoch:
        return None

    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()
    except Exception:
        return None


def heartbeat_base_urls():
    # Presence must reach the admin plane even when the app plane is already working.
    # Setup claim posts to admin, which is why the device appears online directly
    # after onboarding. On a later cold boot the runtime previously posted to app
    # first and returned on the first 200 OK, leaving admin/super-admin LastSeen
    # stale. Touch admin first, then app, and require only one successful host.
    return unique_non_empty([
        ADMIN_BASE_URL,
        DEFAULT_ADMIN_BASE_URL,
        APP_BASE_URL,
        DEFAULT_APP_BASE_URL,
    ])


def heartbeat_urls_for_base(base_url):
    device_q = quote(DEVICE_ID or "")
    tenant_q = quote(TENANT_ID or "")
    return unique_non_empty([
        f"{base_url}/api/ambient-device/touch/{device_q}?tenantId={tenant_q}",
        f"{base_url}{HEARTBEAT_API}/{device_q}?tenantId={tenant_q}",
        f"{base_url}{HEARTBEAT_API}?tenantId={tenant_q}&deviceId={device_q}",
    ])


def post_heartbeat():
    global last_successful_heartbeat_at, last_heartbeat_error

    # Before each heartbeat, re-read the persisted claim identity. This is the
    # cold-boot path that differs from first setup: setup writes the correct identity
    # in memory, but a later power cycle starts from disk + systemd environment.
    reload_device_config_from_disk()

    if not device_has_runtime_identity():
        return False

    wifi_ssid = read_connected_ssid()
    ip_address = read_ip_address()
    now_iso = datetime.now(timezone.utc).isoformat()

    payload = {
        "tenantId": TENANT_ID,
        "TenantId": TENANT_ID,
        "deviceId": DEVICE_ID,
        "DeviceId": DEVICE_ID,
        "id": DEVICE_ID,
        "deviceIdentifier": DEVICE_IDENTIFIER,
        "DeviceIdentifier": DEVICE_IDENTIFIER,
        "deviceName": DEVICE_NAME,
        "DeviceName": DEVICE_NAME,
        "setupCode": str(DEVICE_CONFIG.get("setupCode") or "").strip(),
        "SetupCode": str(DEVICE_CONFIG.get("setupCode") or "").strip(),
        "firmwareVersion": FIRMWARE_VERSION,
        "FirmwareVersion": FIRMWARE_VERSION,
        # Admin online/offline must be based on the heartbeat HTTP request
        # reaching the admin database. SignalR/profile traffic can be healthy
        # while admin LastSeen remains stale if the Pi only touches app.qrelia.uk.
        "runtimeStatus": "Online",
        "RuntimeStatus": "Online",
        "status": "Online",
        "isOnline": True,
        "IsOnline": True,
        "signalRConnected": bool(signalr_connected),
        "SignalRConnected": bool(signalr_connected),
        "signalRStatus": "Connected" if signalr_connected else "Reconnecting",
        "adminSignalRConnected": bool(admin_signalr_connected),
        "AdminSignalRConnected": bool(admin_signalr_connected),
        "lastSignalRConnectedAtUtc": utc_iso_from_epoch(last_signalr_connected_at),
        "lastAdminSignalRConnectedAtUtc": utc_iso_from_epoch(last_admin_signalr_connected_at),
        "lastHeartbeatError": last_heartbeat_error,
        "lastSeenUtc": now_iso,
        "LastSeenUtc": now_iso,
        "heartbeatAtUtc": now_iso,
        "HeartbeatAtUtc": now_iso,
        "timestampUtc": now_iso,
        "TimestampUtc": now_iso,
        "wifiSsid": wifi_ssid,
        "WifiSsid": wifi_ssid,
        "ipAddress": ip_address,
        "IpAddress": ip_address,
    }

    errors = []
    successes = []
    timeout = max(1.5, min(PROFILE_API_TIMEOUT_SECONDS, 3.5))

    for base_url in heartbeat_base_urls():
        base_success = False

        for url in heartbeat_urls_for_base(base_url):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=timeout,
                    headers=control_plane_headers(),
                    allow_redirects=False,
                )

                if response.status_code in (301, 302, 303, 307, 308):
                    errors.append(f"{url} -> redirect {response.status_code} to {response.headers.get('Location', '')[:120]}")
                    continue

                if response.status_code in (404, 405):
                    errors.append(f"{url} -> HTTP {response.status_code}")
                    continue

                response.raise_for_status()
                successes.append(f"{url} -> HTTP {response.status_code}")
                base_success = True
                break
            except Exception as exc:
                errors.append(f"{url} -> {str(exc)[:180]}")

        if not base_success:
            print(f"QRelia heartbeat did not reach {base_url}")

    if successes:
        last_successful_heartbeat_at = time.time()
        last_heartbeat_error = ""
        write_control_plane_status(
            "heartbeat",
            True,
            url=" | ".join(successes[-4:]),
            status_code=200
        )
        return True

    last_heartbeat_error = " | ".join(errors[-6:]) if errors else "No heartbeat endpoints attempted"
    write_control_plane_status("heartbeat", False, detail=last_heartbeat_error)
    raise RuntimeError(last_heartbeat_error)

async def heartbeat_loop():
    if HEARTBEAT_SECONDS <= 0:
        return

    # First heartbeat should be immediate after boot, not after the first interval.
    # This prevents the admin from keeping a cold-booted device offline while the
    # LCD already shows Wi-Fi/IP and the tenant runtime is loading orders.
    while True:
        if device_has_runtime_identity():
            try:
                await asyncio.to_thread(post_heartbeat)
                if runtime_status_mode != "live" and app_signalr_disconnected_since is None:
                    set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")
            except Exception as exc:
                print("QRelia heartbeat failed:", exc)

        await asyncio.sleep(HEARTBEAT_SECONDS)

# =========================
# SIGNALR HELPERS
# =========================


SIGNALR_NEW_ORDER_TARGETS = {
    "newordercreated",
    "ordercreated",
    "receiveorder",
    "receiveordercreated",
}

SIGNALR_ORDER_UPDATED_TARGETS = {
    "orderupdated",
    "orderstatusupdated",
    "orderchanged",
    "receiveorderupdate",
    "receiveorderupdated",
}

SIGNALR_AMBIENT_PROFILE_TARGETS = {
    "ambientprofileupdated",
    "receiveambientprofileupdated",
    "ambientdeviceprofileupdated",
    "receiveambientdeviceprofileupdated",
    "ambientsettingsupdated",
    "receiveambientsettingsupdated",
    "devicesettingsupdated",
    "receiveqreliaambientprofileupdated",
    "profileupdated",
}

KNOWN_ORDER_PHASES = {"pending", "processing", "waiting", "completed", "cancelled"}


def signalr_host_from_base_url(base_url):
    parsed = urlparse(base_url if "://" in str(base_url or "") else f"https://{base_url}")
    host = parsed.netloc or parsed.path
    return host.strip("/")


def signalr_target_matches(target, allowed_targets):
    return str(target or "").strip().lower() in allowed_targets


def extract_signalr_args(msg):
    args = msg.get("arguments")
    if args is None:
        args = msg.get("Arguments")
    return args if isinstance(args, list) else []


def extract_first_value(obj, keys):
    if not isinstance(obj, dict):
        return None

    for key in keys:
        value = obj.get(key)
        if value is not None and str(value).strip():
            return value

    for nested_key in ("order", "Order", "payload", "Payload", "data", "Data", "model", "Model"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            value = extract_first_value(nested, keys)
            if value is not None and str(value).strip():
                return value

    return None


def extract_first_value_from_args(args, keys):
    for arg in args or []:
        value = extract_first_value(arg, keys)
        if value is not None and str(value).strip():
            return value
    return None


def looks_like_guid(value):
    text = str(value or "").strip()
    return len(text) == 36 and text.count("-") == 4


def extract_id(args):
    if not args:
        return "?"

    value = extract_first_value_from_args(args, (
        "Id", "id",
        "OrderId", "orderId", "OrderID", "orderID",
        "OrderGuid", "orderGuid",
        "Guid", "guid",
    ))

    if value is not None and str(value).strip():
        return str(value).strip()

    for arg in args:
        if isinstance(arg, (str, int)) and str(arg).strip():
            return str(arg).strip()

    return "?"


def extract_status(args):
    if not args:
        return ""

    value = extract_first_value_from_args(args, (
        "Status", "status",
        "OrderStatus", "orderStatus",
        "State", "state",
    ))

    if value is not None and str(value).strip():
        return str(value).strip()

    for arg in args:
        if isinstance(arg, str):
            phase = order_phase(arg)
            if phase in KNOWN_ORDER_PHASES and not looks_like_guid(arg):
                return arg.strip()

    return ""


def extract_wait_minutes(args):
    value = extract_first_value_from_args(args, (
        "EstimatedWaitMinutes", "estimatedWaitMinutes",
        "WaitMinutes", "waitMinutes",
    ))
    return normalise_wait_minutes(value)


def extract_location_label(args):
    value = extract_first_value_from_args(args, (
        "LocationLabel", "locationLabel",
        "LocationName", "locationName",
        "TableLabel", "tableLabel",
        "RoomLabel", "roomLabel",
    ))
    return str(value or "").strip()


def extract_notes(args):
    value = extract_first_value_from_args(args, (
        "Notes", "notes",
        "OrderNotes", "orderNotes",
        "CustomerNotes", "customerNotes",
    ))
    return str(value or "").strip()


def _count_items_payload(value):
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return _count_items_payload(json.loads(text))
        except Exception:
            return None

    if isinstance(value, (int, float)):
        count = int(value)
        return count if count > 0 else None

    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict):
                quantity = item.get("Quantity")
                if quantity is None:
                    quantity = item.get("quantity")
                try:
                    total += max(1, int(quantity)) if quantity is not None else 1
                except (TypeError, ValueError):
                    total += 1
            else:
                total += 1
        return total or None

    if isinstance(value, dict):
        for key in ("Items", "items", "OrderItems", "orderItems", "ItemsArray", "itemsArray"):
            if key in value:
                return _count_items_payload(value.get(key))

        quantity = value.get("Quantity")
        if quantity is None:
            quantity = value.get("quantity")
        if quantity is not None:
            try:
                count = int(quantity)
                return count if count > 0 else None
            except (TypeError, ValueError):
                return None

    return None


def extract_item_count(args):
    value = extract_first_value_from_args(args, (
        "ItemsArray", "itemsArray",
        "Items", "items",
        "OrderItems", "orderItems",
        "ItemCount", "itemCount",
    ))
    return _count_items_payload(value)


def extract_ambient_profile_payload(args):
    for arg in args or []:
        if not isinstance(arg, dict):
            continue

        # The current hub sends the profile directly. Accept common wrapper shapes
        # as well so a server-side envelope change cannot silently discard AnimWaiting.
        for key in ("profile", "Profile", "ambientProfile", "AmbientProfile", "data", "Data"):
            nested = arg.get(key)
            if isinstance(nested, dict):
                return nested

        return arg

    return args[0] if args else None


# =========================
# EVENTS
# =========================


async def new_order(args):

    global pending_count, processing_count, waiting_count, last_update_time

    oid = extract_id(args)
    new_status = extract_status(args) or "Pending"
    new_phase = order_phase(new_status)
    wait_minutes = extract_wait_minutes(args)
    location_label = extract_location_label(args)
    item_count = extract_item_count(args)
    notes = extract_notes(args)
    now = time.time()

    order_status[oid] = new_status
    order_status_updated_time[oid] = now
    order_created_time[oid] = now

    if location_label:
        order_location_label[oid] = location_label
    if item_count:
        order_item_count[oid] = item_count
    if notes:
        order_notes[oid] = notes

    if new_phase == "pending":
        order_pending_time[oid] = now
    else:
        order_pending_time.pop(oid, None)

    if new_phase == "waiting" and wait_minutes is not None:
        order_wait_minutes[oid] = wait_minutes
    else:
        order_wait_minutes.pop(oid, None)

    pending_count, processing_count, waiting_count = count_order_phases()

    last_update_time = time.time()

    print(
        f"New order tracked: id={oid}, status={new_status}, pending={pending_count}, "
        f"processing={processing_count}, waiting={waiting_count}"
    )

    if new_phase == "waiting":
        show_lcd_event(
            "waiting",
            order_id=oid,
            location=location_label,
            wait_minutes=wait_minutes,
            item_count=item_count,
            notes=notes,
        )
    elif new_phase == "processing":
        show_lcd_event(
            "processing",
            order_id=oid,
            location=location_label,
            item_count=item_count,
            notes=notes,
        )
    else:
        show_lcd_event(
            "new",
            order_id=oid,
            location=location_label,
            item_count=item_count,
            notes=notes,
        )

    lcd_update()



async def order_updated(args):

    global pending_count, processing_count, waiting_count, last_update_time

    oid = extract_id(args)

    new_status = extract_status(args)

    if not new_status:
        print(f"Order update ignored because no status was supplied: id={oid}, args={args}")
        await refresh_orders_from_api("SignalR update missing status")
        lcd_update()
        return

    old_status = order_status.get(oid, "")
    old_phase = order_phase(old_status)
    new_phase = order_phase(new_status)
    wait_minutes = extract_wait_minutes(args)
    old_wait_minutes = order_wait_minutes.get(oid)
    location_label = extract_location_label(args)
    notes = extract_notes(args)
    item_count = extract_item_count(args)
    now = time.time()

    order_status[oid] = new_status
    order_status_updated_time[oid] = now
    order_created_time.setdefault(oid, now)

    if location_label:
        order_location_label[oid] = location_label
    if notes:
        order_notes[oid] = notes
    if item_count:
        order_item_count[oid] = item_count

    if new_phase == "pending":
        order_pending_time.setdefault(oid, time.time())
    else:
        order_pending_time.pop(oid, None)

    if new_phase == "waiting":
        if wait_minutes is not None:
            order_wait_minutes[oid] = wait_minutes
    else:
        order_wait_minutes.pop(oid, None)

    pending_count, processing_count, waiting_count = count_order_phases()

    last_update_time = time.time()

    wait_detail = f", estimatedWaitMinutes={order_wait_minutes.get(oid)}" if new_phase == "waiting" else ""
    print(
        f"Order updated: id={oid}, status={new_status}, pending={pending_count}, "
        f"processing={processing_count}, waiting={waiting_count}{wait_detail}"
    )

    display_location = order_location_label.get(oid, "")
    display_notes = order_notes.get(oid, "")
    display_item_count = order_item_count.get(oid)

    if new_phase == "waiting" and (old_phase != new_phase or old_wait_minutes != order_wait_minutes.get(oid)):
        show_lcd_event(
            "waiting",
            order_id=oid,
            location=display_location,
            wait_minutes=order_wait_minutes.get(oid),
            item_count=display_item_count,
            notes=display_notes,
        )
    elif old_phase != new_phase and new_phase == "processing":
        show_lcd_event(
            "processing",
            order_id=oid,
            location=display_location,
            item_count=display_item_count,
            notes=display_notes,
        )
    elif new_phase == "completed":
        show_lcd_event(
            "completed",
            order_id=oid,
            location=display_location,
            item_count=display_item_count,
            notes=display_notes,
        )
    elif new_phase == "cancelled":
        show_lcd_event(
            "cancelled",
            order_id=oid,
            location=display_location,
            item_count=display_item_count,
            notes=display_notes,
        )

    lcd_update()

    if new_phase == "completed":
        await play_transient_ambient_state("completed")

    elif new_phase == "cancelled":
        await play_transient_ambient_state("cancelled")

# =========================
# SIGNALR CONNECT
# =========================


async def ambient_profile_updated(args):
    payload = extract_ambient_profile_payload(args)
    if payload is None:
        return
    print("Ambient profile update received from QRelia Admin")
    await apply_ambient_profile(payload, persist=True, reason="signalr")


async def send_signalr_invocation(ws, target, args=None, invocation_id=None):
    await ws.send(json.dumps({
        "type": 1,
        "invocationId": invocation_id or f"qrelia-{target}-{int(time.time() * 1000)}",
        "target": target,
        "arguments": [arg for arg in (args or []) if arg],
    }) + "\x1e")


async def register_signalr_device(ws, label="SignalR"):
    if not DEVICE_ID:
        return

    # Register only method signatures the hub deliberately supports. Previous
    # broad guessing included a two-argument RegisterAmbientDevice call, which
    # produced hub binding errors and made real failures harder to see.
    registrations = [
        ("JoinTenant", [TENANT_ID]),
        ("RegisterAmbientDevice", [DEVICE_ID]),
        ("JoinAmbientDevice", [DEVICE_ID]),
        ("JoinDeviceGroup", [DEVICE_ID]),
    ]

    for index, (target, args) in enumerate(registrations):
        try:
            await send_signalr_invocation(
                ws,
                target,
                args,
                invocation_id=f"{label.lower().replace(' ', '-')}-register-{index}"
            )
        except Exception as exc:
            print(f"{label} registration send failed for {target}:", exc)


async def send_signalr_presence(ws, label="SignalR"):
    # SignalR protocol ping. This isn't an admin presence heartbeat or hub method
    # invocation; it only keeps the receive-mostly hub connection active.
    await ws.send(json.dumps({"type": 6}) + "\x1e")
    return True


async def signalr_presence_loop(ws, label="SignalR"):
    if SIGNALR_PROTOCOL_PING_SECONDS <= 0:
        return

    try:
        while True:
            await asyncio.sleep(SIGNALR_PROTOCOL_PING_SECONDS)
            await send_signalr_presence(ws, label=label)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # The receive loop owns reconnects. Once its socket closes this task will
        # naturally stop on the next scheduled ping without leaking indefinitely.
        print(f"{label} protocol ping loop stopped:", exc)


def consume_task_exception(task):
    try:
        task.exception()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print("Background SignalR task failed:", exc)


async def connect_control_signalr_once(base_url):
    global admin_signalr_connected, last_admin_signalr_connected_at

    if not TENANT_ID or not DEVICE_ID:
        raise RuntimeError("QRelia device is not provisioned yet; admin control SignalR cannot register.")

    neg = await signalr_negotiate(base_url)
    token = neg.get("connectionToken") or neg.get("connectionId")
    if not token:
        raise RuntimeError(f"Admin control SignalR negotiate did not return a connection token: {neg}")

    host = signalr_host_from_base_url(base_url)
    query = f"id={quote(str(token))}&tenantId={quote(TENANT_ID)}&deviceId={quote(DEVICE_ID)}&role=ambient-device"
    ws_url = f"wss://{host}{HUB_PATH}?{query}"

    print(f"Admin control SignalR connecting to {base_url}{HUB_PATH} tenant={TENANT_ID} device={DEVICE_ID}")

    async with websockets.connect(
        ws_url,
        origin=base_url,
        ping_interval=SIGNALR_WS_PING_INTERVAL_SECONDS,
        ping_timeout=SIGNALR_WS_PING_TIMEOUT_SECONDS,
        open_timeout=SIGNALR_WS_OPEN_TIMEOUT_SECONDS,
        close_timeout=SIGNALR_WS_CLOSE_TIMEOUT_SECONDS,
    ) as ws:
        await ws.send(json.dumps({"protocol": "json", "version": 1}) + "\x1e")
        await ws.recv()
        await register_signalr_device(ws, label="Admin control SignalR")
        asyncio.create_task(signalr_presence_loop(ws, label="Admin control SignalR"))

        admin_signalr_connected = True
        last_admin_signalr_connected_at = time.time()
        write_control_plane_status("admin-signalr", True, url=ws_url)

        try:
            await asyncio.to_thread(post_heartbeat)
        except Exception as heartbeat_error:
            print("Admin control SignalR connected, but heartbeat still failed:", heartbeat_error)

        await refresh_ambient_profile_from_api("admin control SignalR connected", preview_on_change=True)
        print("Admin control SignalR connected; listening for ambient profile changes")

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(10.0, HEARTBEAT_SECONDS))
            except asyncio.TimeoutError:
                continue

            for frame in raw.split("\x1e"):
                if not frame.strip():
                    continue

                try:
                    msg = json.loads(frame)
                except Exception:
                    continue

                message_type = msg.get("type")

                if message_type == 6:
                    continue

                if message_type == 3:
                    error = msg.get("error") or msg.get("Error")
                    if error:
                        print(f"Admin control SignalR completion/error: {error}")
                    continue

                if message_type == 7:
                    print(f"Admin control SignalR close frame received: {msg}")
                    return

                if message_type != 1:
                    continue

                target = msg.get("target") or msg.get("Target")
                args = extract_signalr_args(msg)

                if signalr_target_matches(target, SIGNALR_AMBIENT_PROFILE_TARGETS):
                    print(f"Admin control SignalR event received: {target}")
                    await ambient_profile_updated(args)
                elif target:
                    print(f"Admin control SignalR event ignored by device: {target}")


async def admin_control_signalr_loop():
    global admin_signalr_connected

    if not ADMIN_CONTROL_SIGNALR_ENABLED:
        return

    while True:
        if not device_has_runtime_identity():
            await asyncio.sleep(max(2.0, ADMIN_CONTROL_SIGNALR_RECONNECT_SECONDS))
            continue

        for base_url in control_signalr_base_urls():
            try:
                await connect_control_signalr_once(base_url)
            except Exception as exc:
                admin_signalr_connected = False
                write_control_plane_status("admin-signalr", False, url=f"{base_url}{HUB_PATH}", detail=str(exc))
                print(f"Admin control SignalR error via {base_url}:", exc)

            await asyncio.sleep(max(1.0, ADMIN_CONTROL_SIGNALR_RECONNECT_SECONDS))


async def connect_once():
    global app_signalr_disconnected_since, local_wifi_disconnected_since
    global connection_lost_visible, signalr_connected, last_signalr_connected_at

    if not TENANT_ID or not DEVICE_ID:
        raise RuntimeError("QRelia device is not provisioned yet. Complete setup code/PIN pairing first.")

    neg = await signalr_negotiate(APP_BASE_URL)

    token = neg.get("connectionToken") or neg.get("connectionId")
    if not token:
        raise RuntimeError(f"SignalR negotiate did not return a connection token: {neg}")

    # Join both groups on the known-good app hub:
    #   tenant-{TENANT_ID} receives normal order events
    #   ambient-device-{DEVICE_ID} receives direct ambient profile pushes
    # The device group is critical because profile saves are device-specific; relying only
    # on tenantId makes debugging impossible if the local env/default tenant is wrong.
    host = signalr_host_from_base_url(APP_BASE_URL)
    query = f"id={quote(str(token))}&tenantId={quote(TENANT_ID)}"
    if DEVICE_ID:
        query += f"&deviceId={quote(DEVICE_ID)}"
    ws_url = f"wss://{host}{HUB_PATH}?{query}"

    print(f"SignalR connecting to {APP_BASE_URL}{HUB_PATH} tenant={TENANT_ID} device={DEVICE_ID or 'not-set'}")

    async with websockets.connect(
        ws_url,
        origin=APP_BASE_URL,
        ping_interval=SIGNALR_WS_PING_INTERVAL_SECONDS,
        ping_timeout=SIGNALR_WS_PING_TIMEOUT_SECONDS,
        open_timeout=SIGNALR_WS_OPEN_TIMEOUT_SECONDS,
        close_timeout=SIGNALR_WS_CLOSE_TIMEOUT_SECONDS,
    ) as ws:

        await ws.send(json.dumps({"protocol": "json", "version": 1}) + "\x1e")

        await ws.recv()

        # Explicitly re-register after every socket handshake. Cold boots can
        # negotiate successfully but still miss the device-specific admin group
        # unless the runtime invokes the hub registration again.
        await register_signalr_device(ws, label="App SignalR")
        asyncio.create_task(signalr_presence_loop(ws, label="App SignalR"))

        signalr_connected = True
        last_signalr_connected_at = time.time()
        app_signalr_disconnected_since = None
        local_wifi_disconnected_since = None
        recovering_from_visible_loss = connection_lost_visible or system_ambient_state == "connectionLost"
        recovering_from_transient_error = system_ambient_state == "error"
        connection_lost_visible = False
        set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")
        if recovering_from_visible_loss:
            await clear_system_ambient_state(duration=0.24)
        elif recovering_from_transient_error:
            # A completed hub handshake proves the primary realtime path is healthy.
            # Do not leave an earlier REST/startup error latched on the strip.
            await clear_system_ambient_state(duration=0.24)
        lcd_update()

        try:
            await asyncio.to_thread(post_heartbeat)
        except Exception as heartbeat_error:
            print("SignalR connected, but immediate heartbeat failed:", heartbeat_error)

        try:
            await refresh_ambient_profile_from_api("SignalR connected", preview_on_change=True)
        except Exception as profile_error:
            print("SignalR connected, but immediate ambient profile refresh failed:", profile_error)

        # A reconnect can miss every order event that happened while Wi-Fi/SignalR was down.
        # Always try to pull a fresh authoritative snapshot, but never block the live SignalR
        # listener just because the REST snapshot endpoint was briefly unavailable.
        snapshot_loaded = await refresh_orders_from_api("SignalR connected")
        if snapshot_loaded:
            lcd_update()
            await clear_system_ambient_state(duration=0.35)
        else:
            print("SignalR connected; startup order snapshot failed, continuing live event listener")

        print("SignalR connected to app hub; listening for orders, updates and ambient profile events")

        while True:

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(10.0, HEARTBEAT_SECONDS))
            except asyncio.TimeoutError:
                continue

            for frame in raw.split("\x1e"):

                if not frame.strip():
                    continue

                try:
                    msg = json.loads(frame)
                except:
                    continue

                message_type = msg.get("type")

                if message_type == 6:
                    # SignalR ping frame. Keep the socket alive; no visual update needed.
                    continue

                if message_type == 3:
                    error = msg.get("error") or msg.get("Error")
                    if error:
                        print(f"SignalR server completion/error: {error}")
                    continue

                if message_type == 7:
                    print(f"SignalR close frame received: {msg}")
                    return

                if message_type == 1:

                    target = msg.get("target") or msg.get("Target")
                    args = extract_signalr_args(msg)

                    if target:
                        print(f"SignalR event received: {target}")

                    if signalr_target_matches(target, SIGNALR_NEW_ORDER_TARGETS):
                        await new_order(args)

                    elif signalr_target_matches(target, SIGNALR_ORDER_UPDATED_TARGETS):
                        await order_updated(args)

                    elif signalr_target_matches(target, SIGNALR_AMBIENT_PROFILE_TARGETS):
                        await ambient_profile_updated(args)

                    elif target:
                        print(f"SignalR event ignored by device: {target}")

# =========================
# MAIN
# =========================


async def main():

    global signalr_connected, app_signalr_disconnected_since, connection_lost_visible

    addressable_strip_off()

    await load_initial_ambient_profile()

    if device_has_runtime_identity():
        set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")

    # Start visual loops before any network/API work. If Wi-Fi is unavailable,
    # the strip now moves immediately instead of waiting behind HTTP timeouts.
    asyncio.create_task(lcd_refresh_loop())
    asyncio.create_task(lcd_network_info_loop())
    asyncio.create_task(led_strip_loop())
    asyncio.create_task(led_strip_state_loop())
    asyncio.create_task(local_wifi_watch_loop())
    asyncio.create_task(connection_loss_visual_loop())
    asyncio.create_task(order_snapshot_reconciliation_loop())
    asyncio.create_task(ambient_profile_poll_loop())
    asyncio.create_task(heartbeat_loop())
    asyncio.create_task(admin_control_signalr_loop())

    while True:
        if await asyncio.to_thread(claim_device_from_provisioning):
            await load_initial_ambient_profile()
            set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")
            break

        failure = current_provisioning_failure()
        message = str(failure.get("message") or "Enter setup code/PIN")
        retryable = bool(failure.get("retryable"))

        await set_system_ambient_state("connectionLost" if retryable else "setup", duration=0.28, force=True)

        if failure:
            print(f"Provisioning not complete: {message}")
            if not retryable:
                request_setup_mode_after_pairing_failure(message)
            else:
                set_network_runtime_status(message)
        else:
            set_runtime_status("QRelia Setup", "Connect to QRelia-Setup", mode="setup")
            await asyncio.to_thread(ensure_setup_mode_available, "Connect to QRelia-Setup")
            print("Waiting for QRelia provisioning. QRelia-Setup is available for Wi-Fi + setup code/PIN.")

        lcd_update()
        await asyncio.sleep(RECONNECT_SECONDS)

    try:
        await asyncio.to_thread(post_heartbeat)
    except Exception as heartbeat_error:
        print("Initial QRelia heartbeat failed:", heartbeat_error)

    if await refresh_orders_from_api("startup"):
        set_runtime_status("QRelia Online", DEVICE_IDENTIFIER or DEVICE_NAME, mode="live")
        await clear_system_ambient_state(duration=0.35)
    else:
        await set_system_ambient_state("error", duration=0.28, force=True)

    lcd_update()

    while True:

        try:
            await connect_once()
            # A clean return means SignalR sent a close frame. Treat it as disconnected
            # and reconnect after the normal delay without flashing a brief outage.
            signalr_connected = False
            if app_signalr_disconnected_since is None:
                app_signalr_disconnected_since = time.time()
            print("SignalR connection closed; reconnecting")
            await asyncio.sleep(RECONNECT_SECONDS)

        except Exception as e:

            print("SignalR error:", e)

            signalr_connected = False
            now = time.time()
            if app_signalr_disconnected_since is None:
                app_signalr_disconnected_since = now

            await asyncio.sleep(RECONNECT_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutdown_hardware()
