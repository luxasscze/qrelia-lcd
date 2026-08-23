#!/usr/bin/env python3
"""Best-effort Wi-Fi scanning helpers for QRelia setup mode.

The device normally uses the same wlan0 radio for venue Wi-Fi and for the
QRelia-Setup access point. Live scanning while hostapd is broadcasting can be
unreliable on Raspberry Pi Wi-Fi hardware, so setup mode caches a scan before
switching wlan0 into AP mode. The Flask setup page can then show that cached
list and still offer a best-effort refresh.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

WIFI_INTERFACE = os.environ.get("QRELIA_WIFI_INTERFACE", "wlan0").strip() or "wlan0"
WIFI_SCAN_CACHE_PATH = Path(os.environ.get("QRELIA_WIFI_SCAN_CACHE_PATH", "/tmp/qrelia_wifi_scan.json"))
WIFI_SCAN_LAST_GOOD_PATH = Path(os.environ.get("QRELIA_WIFI_SCAN_LAST_GOOD_PATH", "/tmp/qrelia_wifi_scan_last_good.json"))
WIFI_SCAN_MAX_AGE_SECONDS = int(os.environ.get("QRELIA_WIFI_SCAN_MAX_AGE_SECONDS", "1800"))


def _now_utc_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _run(args, timeout=14):
    return subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _split_nmcli_escaped(line):
    fields = []
    current = []
    escaped = False

    for char in line.rstrip("\n"):
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == ":":
            fields.append("".join(current))
            current = []
            continue

        current.append(char)

    fields.append("".join(current))
    return fields


def _normalise_security(value):
    value = re.sub(r"\s+", " ", (value or "").strip())
    if not value or value == "--":
        return "Open"
    return value


def _signal_from_dbm(dbm):
    # Map typical Wi-Fi RSSI to a user-facing 0-100 quality number.
    # -90 dBm is practically unusable, -30 dBm is excellent/nearby.
    try:
        value = float(dbm)
    except Exception:
        return 0

    if value <= -90:
        return 0
    if value >= -30:
        return 100
    return int(round((value + 90) * 100 / 60))


def _normalise_network(ssid, signal=0, security=""):
    ssid = (ssid or "").strip()
    if not ssid:
        return None

    try:
        signal = int(float(signal or 0))
    except Exception:
        signal = 0

    signal = max(0, min(100, signal))
    security = _normalise_security(security)

    return {
        "ssid": ssid,
        "signal": signal,
        "security": security,
        "isOpen": security.lower() == "open",
    }


def _dedupe_and_sort(networks):
    by_ssid = {}

    for network in networks or []:
        item = _normalise_network(
            network.get("ssid"),
            network.get("signal"),
            network.get("security"),
        )
        if not item:
            continue

        existing = by_ssid.get(item["ssid"])
        if existing is None or item["signal"] > existing["signal"]:
            by_ssid[item["ssid"]] = item

    return sorted(
        by_ssid.values(),
        key=lambda item: (-item["signal"], item["ssid"].lower()),
    )


def scan_with_nmcli(interface=WIFI_INTERFACE, rescan=True):
    cmd = [
        "nmcli",
        "-t",
        "--escape",
        "yes",
        "-f",
        "SSID,SIGNAL,SECURITY",
        "device",
        "wifi",
        "list",
        "ifname",
        interface,
        "--rescan",
        "yes" if rescan else "no",
    ]

    result = _run(cmd, timeout=16)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "nmcli Wi-Fi scan failed").strip())

    networks = []
    for line in (result.stdout or "").splitlines():
        if not line.strip():
            continue

        fields = _split_nmcli_escaped(line)
        while len(fields) < 3:
            fields.append("")

        item = _normalise_network(fields[0], fields[1], fields[2])
        if item:
            networks.append(item)

    return _dedupe_and_sort(networks)


def scan_with_iw(interface=WIFI_INTERFACE, ap_force=True):
    cmd = ["iw", "dev", interface, "scan"]
    if ap_force:
        cmd.append("ap-force")

    result = _run(cmd, timeout=16)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "iw Wi-Fi scan failed").strip())

    networks = []
    current = None

    def finish_current():
        if current:
            item = _normalise_network(
                current.get("ssid"),
                current.get("signal"),
                current.get("security"),
            )
            if item:
                networks.append(item)

    for raw_line in (result.stdout or "").splitlines():
        line = raw_line.strip()

        if line.startswith("BSS "):
            finish_current()
            current = {"ssid": "", "signal": 0, "security": "Open", "privacy": False}
            continue

        if current is None:
            continue

        if line.startswith("SSID:"):
            current["ssid"] = line.split("SSID:", 1)[1].strip()
            continue

        if line.startswith("signal:"):
            match = re.search(r"-?\d+(?:\.\d+)?", line)
            if match:
                current["signal"] = _signal_from_dbm(match.group(0))
            continue

        if line.startswith("capability:") and "Privacy" in line:
            current["privacy"] = True
            if current.get("security") == "Open":
                current["security"] = "Secured"
            continue

        if line.startswith("RSN:"):
            current["security"] = "WPA2/WPA3"
            continue

        if line.startswith("WPA:"):
            current["security"] = "WPA/WPA2"
            continue

    finish_current()
    return _dedupe_and_sort(networks)


def _cache_payload(networks, source="unknown", error=""):
    return {
        "createdAt": _now_utc_iso(),
        "createdAtUnix": time.time(),
        "interface": WIFI_INTERFACE,
        "source": source,
        "error": error or "",
        "networks": _dedupe_and_sort(networks),
    }


def _write_cache_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o644)
    except Exception:
        pass


def _read_wifi_scan_file(path, include_stale=True):
    try:
        if not path.exists():
            return [], None

        data = json.loads(path.read_text(encoding="utf-8"))
        created_at_unix = float(data.get("createdAtUnix") or 0)
        age_seconds = max(0, int(time.time() - created_at_unix)) if created_at_unix else None
        is_stale = age_seconds is None or age_seconds > WIFI_SCAN_MAX_AGE_SECONDS

        if is_stale and not include_stale:
            return [], None

        networks = _dedupe_and_sort(data.get("networks") or [])
        meta = {
            "createdAt": data.get("createdAt") or "",
            "createdAtUnix": created_at_unix,
            "ageSeconds": age_seconds,
            "isStale": is_stale,
            "source": data.get("source") or "cache",
            "error": data.get("error") or "",
            "path": str(path),
        }
        return networks, meta
    except Exception as exc:
        return [], {"error": f"Could not read cached Wi-Fi scan from {path}: {exc}", "isStale": True}


def read_wifi_scan_cache(include_stale=True, prefer_last_good=True):
    networks, meta = _read_wifi_scan_file(WIFI_SCAN_CACHE_PATH, include_stale=include_stale)
    if networks:
        return networks, meta

    if prefer_last_good:
        last_good, last_good_meta = _read_wifi_scan_file(WIFI_SCAN_LAST_GOOD_PATH, include_stale=include_stale)
        if last_good:
            if meta and meta.get("error") and not last_good_meta.get("error"):
                last_good_meta["error"] = meta.get("error")
            last_good_meta["source"] = "last-good"
            return last_good, last_good_meta

    return networks, meta


def write_wifi_scan_cache(networks, source="unknown", error="", update_last_good=True):
    networks = _dedupe_and_sort(networks)
    payload = _cache_payload(networks, source=source, error=error)
    _write_cache_file(WIFI_SCAN_CACHE_PATH, payload)

    # Keep a separate last-known-good copy. Refresh while QRelia-Setup AP is
    # running can legitimately return no rows on a single wlan0 radio; that
    # must never destroy the useful scan captured before AP mode started.
    if networks and update_last_good:
        _write_cache_file(WIFI_SCAN_LAST_GOOD_PATH, payload)

    return networks


def refresh_wifi_network_cache(interface=WIFI_INTERFACE, allow_iw_fallback=True, preserve_existing_on_empty=True):
    errors = []
    previous_networks, _ = read_wifi_scan_cache(include_stale=True, prefer_last_good=True)

    try:
        networks = scan_with_nmcli(interface=interface, rescan=True)
        if networks:
            return write_wifi_scan_cache(networks, source="nmcli"), "nmcli", ""
        errors.append("nmcli returned no nearby Wi-Fi networks")
    except Exception as exc:
        errors.append(str(exc))

    if allow_iw_fallback:
        try:
            networks = scan_with_iw(interface=interface, ap_force=True)
            if networks:
                return write_wifi_scan_cache(networks, source="iw"), "iw", ""
            errors.append("iw returned no nearby Wi-Fi networks")
        except Exception as exc:
            errors.append(str(exc))

    error = " | ".join(error for error in errors if error) or "No Wi-Fi scan result returned."

    if preserve_existing_on_empty and previous_networks:
        return previous_networks, "last-good", error

    write_wifi_scan_cache([], source="failed", error=error, update_last_good=False)
    return [], "failed", error


def get_wifi_networks(force_refresh=False):
    if not force_refresh:
        cached, meta = read_wifi_scan_cache(include_stale=False)
        if cached:
            return cached, meta or {"source": "cache"}, ""

    networks, source, error = refresh_wifi_network_cache(allow_iw_fallback=True)
    cached, meta = read_wifi_scan_cache(include_stale=True)
    if networks:
        return networks, {"source": source, **(meta or {})}, error

    return cached, meta or {"source": source, "error": error}, error
