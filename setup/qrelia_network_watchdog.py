#!/usr/bin/env python3

import subprocess
import time
import json
import os
from pathlib import Path

from display_state import set_display_state

PROVISIONING_FAILURE_PATH = Path("/home/qrelia/qreliadevice/provisioning_error.json")
SETUP_TRANSITION_PATH = Path(os.environ.get("QRELIA_SETUP_TRANSITION_PATH", "/tmp/qrelia-setup-transition.json"))
WIFI_SETUP_PENDING_PATH = Path(os.environ.get("QRELIA_WIFI_SETUP_PENDING_PATH", "/home/qrelia/qreliadevice/wifi_setup_pending.json"))
WIFI_SETUP_FAILURE_PATH = Path(os.environ.get("QRELIA_WIFI_SETUP_FAILURE_PATH", "/home/qrelia/qreliadevice/wifi_setup_error.json"))

SETUP_SERVICE = "qrelia-setup-mode.service"
TENANT_SERVICE = "qrelia-tenant.service"

CHECK_INTERVAL_SECONDS = 5
WARNING_AFTER_SECONDS = 10
SETUP_TRANSITION_MAX_AGE_SECONDS = 180
FRESH_SETUP_WIFI_TIMEOUT_SECONDS = int(os.environ.get("QRELIA_FRESH_SETUP_WIFI_TIMEOUT_SECONDS", "45"))

WIFI_INTERFACE = "wlan0"


def log(message):
    print(f"[QRelia Network Watchdog] {message}", flush=True)


def run(command, timeout=8):
    try:
        return subprocess.run(
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        log(f"Command timed out: {command}")
        return subprocess.CompletedProcess(command, 124, "", "Timeout")
    except Exception as ex:
        log(f"Command failed: {command} | {ex}")
        return subprocess.CompletedProcess(command, 1, "", str(ex))


def get_ip_address():
    result = run(f"ip -4 addr show {WIFI_INTERFACE}", timeout=3)

    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("inet "):
            return line.split()[1].split("/")[0]

    return ""


def get_connected_ssid():
    result = run(f"iwgetid {WIFI_INTERFACE} -r", timeout=3)
    ssid = result.stdout.strip()

    if ssid:
        return ssid

    result = run("nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev", timeout=5)

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 4:
            device = parts[0]
            dev_type = parts[1]
            state = parts[2]
            connection = parts[3]

            if device == WIFI_INTERFACE and dev_type == "wifi" and state == "connected":
                return connection

    return ""


def get_active_connection_name():
    result = run(f"nmcli -g GENERAL.CONNECTION device show {WIFI_INTERFACE}", timeout=5)
    if result.returncode != 0:
        return ""
    value = result.stdout.strip()
    return "" if value in {"", "--"} else value


def read_json_file(path):
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"Could not read {path}: {exc}")
        return {}


def write_json_atomic(path, payload, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def read_wifi_setup_pending():
    return read_json_file(WIFI_SETUP_PENDING_PATH)


def clear_wifi_setup_pending():
    try:
        WIFI_SETUP_PENDING_PATH.unlink(missing_ok=True)
    except Exception as exc:
        log(f"Could not clear pending Wi-Fi setup marker: {exc}")


def clear_wifi_setup_failure():
    try:
        WIFI_SETUP_FAILURE_PATH.unlink(missing_ok=True)
    except Exception as exc:
        log(f"Could not clear Wi-Fi setup failure marker: {exc}")


def write_wifi_setup_failure(pending, message):
    payload = {
        "attemptId": str(pending.get("attemptId") or ""),
        "ssid": str(pending.get("ssid") or "venue Wi-Fi"),
        "connectionName": str(pending.get("connectionName") or "QRelia-Venue-WiFi"),
        "message": str(message or "The saved Wi-Fi details could not connect."),
        "createdAtUnix": time.time(),
    }
    try:
        write_json_atomic(WIFI_SETUP_FAILURE_PATH, payload)
    except Exception as exc:
        log(f"Could not write Wi-Fi setup failure marker: {exc}")


def pending_profile_connected(pending, ssid, ip, active_connection):
    expected_connection = str(pending.get("connectionName") or "QRelia-Venue-WiFi")
    return bool(ssid and ip and active_connection == expected_connection)


def recover_failed_fresh_setup(pending):
    expected_connection = str(pending.get("connectionName") or "QRelia-Venue-WiFi")
    expected_ssid = str(pending.get("ssid") or "venue Wi-Fi")
    log(f"Fresh setup could not connect to '{expected_ssid}'. Returning to setup mode.")

    # Remove only the profile created by the captive portal. This makes the
    # no-saved-profile rule and setup service agree instead of fighting each other.
    run(f"nmcli connection delete {shell_quote(expected_connection)} || true", timeout=12)
    write_wifi_setup_failure(
        pending,
        "The network could not be joined. Check the Wi-Fi name and password.",
    )
    clear_wifi_setup_pending()
    set_display_state("setup_starting", ssid=expected_ssid, message="Wrong Wi-Fi - setup returning")

    if not setup_mode_active():
        start_setup_mode("Check Wi-Fi password")


def shell_quote(value):
    import shlex
    return shlex.quote(str(value or ""))


def get_saved_wifi_connections():
    result = run("nmcli -t -f NAME,TYPE connection show", timeout=5)

    saved = []

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 2:
            name = parts[0]
            conn_type = parts[1]

            if conn_type in ("802-11-wireless", "wifi", "wireless"):
                saved.append(name)

    return saved


def has_saved_wifi():
    return len(get_saved_wifi_connections()) > 0


def setup_mode_active():
    result = run(f"systemctl is-active {SETUP_SERVICE}", timeout=5)
    return result.stdout.strip() == "active"


def read_pairing_failure():
    try:
        if not PROVISIONING_FAILURE_PATH.exists():
            return {}

        data = json.loads(PROVISIONING_FAILURE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log(f"Could not read pairing failure marker: {exc}")
        return {}


def pairing_recovery_required():
    data = read_pairing_failure()
    return bool(data) and not bool(data.get("retryable"))


def pairing_recovery_message():
    data = read_pairing_failure()
    message = str(data.get("message") or "").strip()
    return message or "Fix setup code/PIN"


def setup_transition_active():
    """Protect the credential-save and scheduled-reboot hand-off."""
    try:
        if not SETUP_TRANSITION_PATH.exists():
            return False

        data = json.loads(SETUP_TRANSITION_PATH.read_text(encoding="utf-8"))
        state = str(data.get("state") or "") if isinstance(data, dict) else ""
        age = time.time() - SETUP_TRANSITION_PATH.stat().st_mtime
        if age <= SETUP_TRANSITION_MAX_AGE_SECONDS:
            return True

        # If reboot scheduling ever fails unexpectedly, release the watchdog
        # after the bounded grace period. Keep the saved profile: normal runtime
        # will attempt it and the display will report any connection problem.
        log(f"Removing stale setup transition marker in state '{state}' ({int(age)}s old).")
        SETUP_TRANSITION_PATH.unlink(missing_ok=True)
    except Exception as exc:
        log(f"Could not inspect setup transition marker: {exc}")

    return False

def start_setup_mode(reason="Starting setup"):
    log(f"Starting setup mode: {reason}")
    set_display_state("setup_starting", message=reason[:24])

    # LCD generation rule: qrelia-tenant is the ONLY owner of GPIO18 and the LCD.
    # Setup mode changes wlan0 and serves the captive portal beside it; it never
    # starts a second LED/display renderer and therefore must not stop tenant.
    result = run(f"systemctl start {SETUP_SERVICE}", timeout=15)

    if result.returncode != 0:
        log(f"Failed to start setup mode: {result.stderr}")
        set_display_state("error", message="Setup failed")


def stop_setup_mode():
    run(f"systemctl stop {SETUP_SERVICE} || true", timeout=15)


def tenant_mode_active():
    result = run(f"systemctl is-active {TENANT_SERVICE}", timeout=5)
    return result.stdout.strip() == "active"


def start_tenant_mode(reason="Starting tenant runtime"):
    log(f"Starting tenant mode: {reason}")
    run(f"systemctl reset-failed {TENANT_SERVICE} || true", timeout=5)
    result = run(f"systemctl start {TENANT_SERVICE}", timeout=15)

    if result.returncode != 0:
        log(f"Failed to start tenant mode: {result.stderr}")
        set_display_state("error", message="Tenant failed")


def stop_tenant_mode():
    run(f"systemctl stop {TENANT_SERVICE} || true", timeout=15)


def nudge_wifi_reconnect():
    # This must never block forever.
    # It simply asks NetworkManager to try wlan0 again.
    run(f"nmcli radio wifi on", timeout=5)
    run(f"nmcli device connect {WIFI_INTERFACE}", timeout=10)


def main():
    log("Network watchdog started.")

    disconnected_since = None
    fresh_setup_disconnected_since = None
    last_connected_ssid = ""
    last_state = ""

    while True:
        ssid = get_connected_ssid()
        ip = get_ip_address()
        active_connection = get_active_connection_name()
        pending_wifi_setup = read_wifi_setup_pending()
        now = time.time()

        # setup_server.py has saved the profile and scheduled reboot in an
        # independent systemd unit. Do not stop setup mode before the browser has
        # received its confirmation page and the reboot unit has fired.
        if setup_transition_active():
            if last_state != "setup_transition":
                log("Setup save/reboot hand-off is active.")
                last_state = "setup_transition"
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # A profile created by the captive portal is provisional until that
        # exact NetworkManager connection associates and receives an IPv4 address.
        # This applies only to a fresh setup attempt; established devices still
        # tolerate normal router outages without dropping into setup mode.
        if pending_wifi_setup:
            expected_ssid = str(pending_wifi_setup.get("ssid") or "venue Wi-Fi")

            if pending_profile_connected(pending_wifi_setup, ssid, ip, active_connection):
                log(f"Fresh Wi-Fi setup succeeded on '{expected_ssid}' ({ip}).")
                clear_wifi_setup_pending()
                clear_wifi_setup_failure()
                fresh_setup_disconnected_since = None
            else:
                if fresh_setup_disconnected_since is None:
                    fresh_setup_disconnected_since = now
                    log(f"Testing fresh Wi-Fi setup for '{expected_ssid}'.")

                pending_for = int(now - fresh_setup_disconnected_since)

                # If the boot decider already started setup mode, finish recovery
                # immediately so the watchdog cannot stop it because a bad saved
                # profile still exists.
                if setup_mode_active() or pending_for >= FRESH_SETUP_WIFI_TIMEOUT_SECONDS:
                    recover_failed_fresh_setup(pending_wifi_setup)
                    fresh_setup_disconnected_since = None
                    disconnected_since = None
                    last_state = "fresh_setup_recovery"
                    time.sleep(CHECK_INTERVAL_SECONDS)
                    continue

                remaining = max(0, FRESH_SETUP_WIFI_TIMEOUT_SECONDS - pending_for)
                state_key = f"fresh_setup_testing:{pending_for // CHECK_INTERVAL_SECONDS}:{expected_ssid}"
                if state_key != last_state:
                    set_display_state(
                        "wifi_failed",
                        ssid=expected_ssid,
                        message=f"Checking WiFi {remaining}s",
                        ip="",
                    )
                    last_state = state_key

                if pending_for % 15 < CHECK_INTERVAL_SECONDS:
                    expected_connection = str(pending_wifi_setup.get("connectionName") or "QRelia-Venue-WiFi")
                    run(f"nmcli connection up {shell_quote(expected_connection)} ifname {WIFI_INTERFACE} || true", timeout=15)

                time.sleep(CHECK_INTERVAL_SECONDS)
                continue
        else:
            fresh_setup_disconnected_since = None

        if ssid:
            if disconnected_since is not None:
                log(f"Wi-Fi recovered: {ssid}")

            disconnected_since = None
            last_connected_ssid = ssid

            if pairing_recovery_required():
                if not setup_mode_active():
                    start_setup_mode("Fix pairing")
                set_display_state("setup_ready", message=pairing_recovery_message(), ip="192.168.4.1")
                last_state = "pairing_recovery_connected"
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue

            if setup_mode_active():
                log("Wi-Fi connected while setup mode is active. Stopping setup mode.")
                stop_setup_mode()

            # Cold boot/power-cycle recovery: Wi-Fi being connected is not enough.
            # The tenant runtime owns heartbeat + SignalR + order/profile handling,
            # so make sure it is running whenever venue Wi-Fi is available.
            if not tenant_mode_active():
                start_tenant_mode("venue Wi-Fi connected")

            state_key = f"normal:{ssid}:{ip}"

            if state_key != last_state:
                set_display_state("normal_mode", ssid=ssid, ip=ip)
                last_state = state_key

            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # No Wi-Fi currently connected.
        if disconnected_since is None:
            disconnected_since = now
            log("Wi-Fi connection lost or unavailable at boot.")

        offline_for = int(now - disconnected_since)
        saved_wifi_profiles = get_saved_wifi_connections()

        # Only confirmed code/PIN failures should return to setup mode.
        # Retryable cloud/DNS failures must stay in normal Wi-Fi mode and keep retrying.
        if pairing_recovery_required():
            log("Confirmed pairing failure marker exists; keeping setup mode available.")

            if not setup_mode_active():
                start_setup_mode("Fix setup code")

            set_display_state("setup_ready", message=pairing_recovery_message(), ip="192.168.4.1")
            last_state = "setup_pairing_recovery"
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # If there are no saved Wi-Fi profiles, setup mode is correct.
        if not saved_wifi_profiles:
            log("No saved Wi-Fi profiles found.")

            if not setup_mode_active():
                start_setup_mode("Starting setup")

            last_state = "setup_no_saved_wifi"
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue

        # If saved Wi-Fi exists, do NOT enter setup mode.
        # Keep retrying forever, because the hotspot/router may come back.
        if setup_mode_active():
            log("Saved Wi-Fi exists but setup mode is active. Stopping setup mode.")
            stop_setup_mode()

        if offline_for >= WARNING_AFTER_SECONDS:
            profile_text = saved_wifi_profiles[0]
            message = f"Offline {offline_for}s"

            state_key = f"wifi_failed:{offline_for // CHECK_INTERVAL_SECONDS}:{profile_text}"

            log(
                f"Wi-Fi unavailable for {offline_for}s. "
                f"Saved profiles exist: {saved_wifi_profiles}. Retrying."
            )

            set_display_state(
                "wifi_failed",
                ssid=last_connected_ssid or profile_text,
                message=message,
                ip=""
            )

            # Every few loops, nudge NetworkManager to try again.
            if offline_for % 20 < CHECK_INTERVAL_SECONDS:
                nudge_wifi_reconnect()

            last_state = state_key

        else:
            # Early boot grace period.
            message = "Checking WiFi"
            state_key = f"checking:{offline_for}"

            if state_key != last_state:
                set_display_state(
                    "wifi_failed",
                    ssid=last_connected_ssid,
                    message=message,
                    ip=""
                )
                last_state = state_key

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
