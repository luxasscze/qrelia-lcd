#!/usr/bin/env python3

from flask import Flask, request, render_template_string, redirect, Response
import subprocess
import html
import shlex
import shutil
import os
import time
import json
import re
from pathlib import Path
from display_state import set_display_state as write_display_state
from wifi_scan import get_wifi_networks

app = Flask(__name__)

DEVICE_CONFIG_PATH = Path(os.environ.get("QRELIA_DEVICE_CONFIG_PATH", "/home/qrelia/qreliadevice/qrelia_device_config.json"))
PROVISIONING_PATH = Path(os.environ.get("QRELIA_PROVISIONING_PATH", "/home/qrelia/qreliadevice/provisioning.json"))
PROVISIONING_FAILURE_PATH = Path(os.environ.get("QRELIA_PROVISIONING_FAILURE_PATH", "/home/qrelia/qreliadevice/provisioning_error.json"))
SETUP_TRANSITION_PATH = Path(os.environ.get("QRELIA_SETUP_TRANSITION_PATH", "/tmp/qrelia-setup-transition.json"))
WIFI_SETUP_PENDING_PATH = Path(os.environ.get("QRELIA_WIFI_SETUP_PENDING_PATH", "/home/qrelia/qreliadevice/wifi_setup_pending.json"))
WIFI_SETUP_FAILURE_PATH = Path(os.environ.get("QRELIA_WIFI_SETUP_FAILURE_PATH", "/home/qrelia/qreliadevice/wifi_setup_error.json"))
WIFI_CONNECTION_NAME = os.environ.get("QRELIA_WIFI_CONNECTION_NAME", "QRelia-Venue-WiFi")
DEFAULT_ADMIN_BASE_URL = os.environ.get("QRELIA_ADMIN_BASE_URL", "https://admin.qrelia.uk").rstrip("/")
SETUP_HOSTNAME = os.environ.get("QRELIA_SETUP_HOSTNAME", "qrelia.local").strip().lower()
SETUP_IP = os.environ.get("QRELIA_SETUP_IP", "192.168.4.1").strip()
SETUP_URL = os.environ.get("QRELIA_SETUP_URL", f"http://{SETUP_HOSTNAME}/").strip()
KNOWN_SETUP_HOSTS = {SETUP_HOSTNAME, SETUP_IP, "localhost", "127.0.0.1"}
CAPTIVE_PORTAL_PATHS = {
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/connecttest.txt",
    "/ncsi.txt",
    "/canonical.html",
    "/success.txt",
    "/redirect",
}


def mark_setup_transition(ssid, state="saving_wifi"):
    SETUP_TRANSITION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": str(state or "saving_wifi"),
        "ssid": str(ssid or ""),
        "pid": os.getpid(),
        "createdAtUnix": time.time(),
    }
    SETUP_TRANSITION_PATH.write_text(json.dumps(payload), encoding="utf-8")


def clear_setup_transition():
    try:
        SETUP_TRANSITION_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def read_setup_transition():
    try:
        if not SETUP_TRANSITION_PATH.exists():
            return {}
        data = json.loads(SETUP_TRANSITION_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"Could not read setup transition marker: {exc}", flush=True)
        return {}


def reboot_handoff_transition():
    transition = read_setup_transition()
    if str(transition.get("state") or "") != "reboot_scheduled":
        return {}
    return transition


def set_display_state(state, ssid="", message="", ip="192.168.4.1"):
    """Write display state without allowing portal probes to erase reboot status.

    Mobile captive-portal clients continue issuing GET probes after /save. Those
    requests used to replace the explicit reboot screen with phone_connected or
    wifi_form before the display renderer could display it. Once reboot is scheduled,
    only the rebooting state may replace the latched display payload.
    """
    transition = reboot_handoff_transition()
    if transition and state != "rebooting":
        print(
            f"Preserving reboot display state; ignored late state '{state}'.",
            flush=True,
        )
        return
    write_display_state(state, ssid=ssid, message=message, ip=ip)


def latch_reboot_display(ssid):
    write_display_state(
        "rebooting",
        ssid=str(ssid or ""),
        message="Setup saved - restarting",
        ip="",
    )


def write_json_atomic(path, payload, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def write_wifi_setup_pending(ssid, connection_name):
    payload = {
        "attemptId": f"{int(time.time() * 1000)}-{os.getpid()}",
        "ssid": str(ssid or ""),
        "connectionName": str(connection_name or WIFI_CONNECTION_NAME),
        "createdAtUnix": time.time(),
    }
    write_json_atomic(WIFI_SETUP_PENDING_PATH, payload)


def clear_wifi_setup_pending():
    try:
        WIFI_SETUP_PENDING_PATH.unlink(missing_ok=True)
    except Exception as exc:
        print(f"Could not clear pending Wi-Fi setup marker: {exc}", flush=True)


def clear_wifi_setup_failure():
    try:
        WIFI_SETUP_FAILURE_PATH.unlink(missing_ok=True)
    except Exception as exc:
        print(f"Could not clear Wi-Fi setup failure marker: {exc}", flush=True)


def wifi_setup_failure():
    data = read_json(WIFI_SETUP_FAILURE_PATH)
    return data if isinstance(data, dict) else {}


def wifi_setup_error_message():
    data = wifi_setup_failure()
    if not data:
        return ""
    ssid = str(data.get("ssid") or "the selected network").strip()
    detail = str(data.get("message") or "The device could not connect using the saved Wi-Fi details.").strip()
    return f"Wi-Fi setup failed for {ssid}: {detail} Enter the Wi-Fi password again."


def schedule_reboot(delay_seconds=8):
    """Schedule reboot outside qrelia-setup-mode.service.

    The setup service is stopped as part of reboot. A normal background child of
    the Flask process would be killed with that service before it could reboot
    the Pi, so use an independent transient systemd unit.
    """
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"

    if not systemd_run:
        return False, "systemd-run is not available"

    unit_name = f"qrelia-setup-reboot-{os.getpid()}-{int(time.time())}"
    try:
        result = subprocess.run(
            [
                systemd_run,
                "--quiet",
                "--collect",
                f"--unit={unit_name}",
                f"--on-active={max(1, int(delay_seconds))}s",
                systemctl,
                "reboot",
            ],
            text=True,
            capture_output=True,
            timeout=12,
        )
    except Exception as exc:
        return False, f"Could not schedule reboot: {exc}"

    if result.returncode == 0:
        return True, ""

    return False, (result.stderr or result.stdout or "systemd-run failed").strip()


SETUP_SAVED_PAGE = """
<!doctype html>
<html>
<head>
    <title>QRelia Setup Saved</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --text:#fff; --muted:rgba(255,255,255,.70); --accent:#7dd3fc;
            --good:#4ade80; --panel:rgba(255,255,255,.08);
        }
        * { box-sizing:border-box; }
        body {
            margin:0; min-height:100vh; padding:20px; display:grid; place-items:center;
            font-family:Arial,sans-serif; color:var(--text);
            background:
                radial-gradient(circle at 20% 0%, rgba(125,211,252,.24), transparent 34%),
                radial-gradient(circle at 80% 20%, rgba(168,85,247,.20), transparent 31%),
                linear-gradient(135deg,#050814,#0b1020 55%,#020617);
        }
        .card {
            width:100%; max-width:640px; padding:32px; border-radius:28px;
            background:var(--panel); border:1px solid rgba(255,255,255,.16);
            box-shadow:0 24px 90px rgba(0,0,0,.52),0 0 60px rgba(74,222,128,.10);
            backdrop-filter:blur(18px); text-align:center;
        }
        .orb {
            width:92px; height:92px; margin:0 auto 22px; border-radius:999px; position:relative;
            background:radial-gradient(circle, #fff 0 8%, #86efac 9% 24%, #22c55e 25% 46%, rgba(34,197,94,.14) 47% 100%);
            box-shadow:0 0 0 1px rgba(34,197,94,.45),0 0 58px rgba(34,197,94,.40);
            animation:success-pop .55s ease-out both, breathe 2.2s ease-in-out .55s infinite;
        }
        .orb:after {
            content:""; position:absolute; inset:-10px; border-radius:inherit;
            border:2px solid rgba(74,222,128,.40); border-top-color:transparent;
            animation:spin 2.2s linear infinite;
        }
        .tick {
            position:absolute; left:29px; top:30px; width:35px; height:19px;
            border-left:6px solid white; border-bottom:6px solid white;
            transform:rotate(-45deg);
        }
        .eyebrow { color:var(--accent); font-size:12px; font-weight:900; letter-spacing:.16em; text-transform:uppercase; }
        h1 { margin:10px 0 10px; font-size:31px; }
        p { margin:0 auto 16px; max-width:530px; color:var(--muted); line-height:1.55; }
        .network {
            display:inline-block; margin:4px 0 20px; padding:9px 14px; border-radius:999px;
            background:rgba(125,211,252,.10); border:1px solid rgba(125,211,252,.24); font-weight:800;
        }
        .display-panel {
            margin-top:20px; padding:18px; border-radius:20px; text-align:left;
            background:rgba(0,0,0,.22); border:1px solid rgba(255,255,255,.10);
        }
        .display-panel > strong { display:block; margin-bottom:12px; font-size:16px; }
        .display-row { display:grid; grid-template-columns:110px 1fr; gap:12px; padding:10px 0; border-top:1px solid rgba(255,255,255,.08); }
        .display-row:first-of-type { border-top:0; padding-top:2px; }
        .display-label { color:#fff; font-weight:900; letter-spacing:.04em; }
        .display-copy { color:var(--muted); font-size:14px; line-height:1.45; }
        .recovery {
            margin-top:16px; padding:14px 16px; border-radius:16px;
            background:rgba(125,211,252,.08); border:1px solid rgba(125,211,252,.18);
            color:rgba(255,255,255,.76); font-size:13px; line-height:1.5; text-align:left;
        }
        .hint { margin-top:16px; color:rgba(255,255,255,.52); font-size:12px; line-height:1.45; }
        @keyframes spin { to { transform:rotate(360deg); } }
        @keyframes breathe { 50% { transform:scale(1.05); filter:brightness(1.10); } }
        @keyframes success-pop { 0% { transform:scale(.78); opacity:.4; } 70% { transform:scale(1.10); } 100% { transform:scale(1); opacity:1; } }
        @media (max-width:520px) {
            body { padding:12px; align-items:flex-start; }
            .card { padding:24px 18px; border-radius:22px; }
            .display-row { grid-template-columns:1fr; gap:3px; }
        }
    </style>
</head>
<body>
    <main class="card">
        <div class="orb"><span class="tick"></span></div>
        <div class="eyebrow">QRelia secure setup</div>
        <h1>Setup saved — device restarting</h1>
        <div class="network">{{ ssid }}</div>
        <p>Your Wi-Fi and QRelia pairing details have been stored. QRelia will now restart and connect in normal operating mode.</p>

        <div class="display-panel">
            <strong>Check the device LCD after restart</strong>
            <div class="display-row">
                <span class="display-label">ONLINE</span>
                <span class="display-copy">The device is connected and ready.</span>
            </div>
            <div class="display-row">
                <span class="display-label">CONNECTING</span>
                <span class="display-copy">The device is still joining the venue network. Give it a moment.</span>
            </div>
            <div class="display-row">
                <span class="display-label">WIFI ISSUE / ERROR</span>
                <span class="display-copy">Read the LCD message — it will show the connection or pairing problem.</span>
            </div>
        </div>

        <div class="recovery"><strong>Wrong Wi-Fi details?</strong> QRelia will test the saved network after restart. If it cannot connect, it automatically removes the failed profile and brings QRelia-Setup back so the password can be corrected.</div>
        <div class="hint">QRelia-Setup will disappear during restart. If the venue Wi-Fi fails, wait for QRelia-Setup to return and reconnect to it.</div>
    </main>
</body>
</html>
"""

PAGE = """
<!doctype html>
<html>
<head>
    <title>QRelia Device Setup</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        :root {
            --bg:#050814;
            --panel:rgba(255,255,255,0.08);
            --line:rgba(255,255,255,0.16);
            --line-strong:rgba(125,211,252,0.36);
            --text:#fff;
            --muted:rgba(255,255,255,0.68);
            --soft:rgba(255,255,255,0.50);
            --accent:#7dd3fc;
            --accent-2:#38bdf8;
            --good:#22c55e;
            --danger:#ef4444;
            --warn:#f59e0b;
        }
        * { box-sizing: border-box; }
        html { scroll-behavior:smooth; }
        body {
            margin:0;
            min-height:100vh;
            font-family:Arial,sans-serif;
            background:
                radial-gradient(circle at 20% 0%, rgba(125,211,252,.22), transparent 34%),
                radial-gradient(circle at 80% 20%, rgba(168,85,247,.18), transparent 30%),
                linear-gradient(135deg,#050814,#0b1020 55%,#020617);
            color:var(--text);
            display:flex;
            align-items:center;
            justify-content:center;
            padding:18px;
        }
        .card {
            width:100%;
            max-width:620px;
            padding:24px;
            border-radius:26px;
            background:var(--panel);
            border:1px solid var(--line);
            box-shadow:0 24px 90px rgba(0,0,0,.50);
            backdrop-filter:blur(18px);
        }
        .eyebrow {
            color:var(--accent);
            text-transform:uppercase;
            letter-spacing:.16em;
            font-size:12px;
            font-weight:800;
            margin-bottom:10px;
        }
        h1 { margin:0 0 8px; font-size:30px; line-height:1.08; }
        p { color:var(--muted); line-height:1.5; margin:0 0 18px; font-size:15px; }
        .steps { display:grid; gap:10px; margin:18px 0; }
        .step {
            display:grid;
            grid-template-columns:32px minmax(0,1fr);
            gap:10px;
            padding:12px;
            border-radius:16px;
            background:rgba(0,0,0,.20);
            border:1px solid rgba(255,255,255,.10);
        }
        .num {
            width:32px;
            height:32px;
            display:grid;
            place-items:center;
            border-radius:999px;
            background:linear-gradient(135deg,#7dd3fc,#38bdf8);
            color:#04111f;
            font-weight:900;
        }
        .step strong { display:block; margin-bottom:2px; }
        label {
            display:block;
            margin:0 0 8px;
            color:rgba(255,255,255,.86);
            font-size:14px;
            font-weight:800;
        }
        input {
            width:100%;
            border:1px solid var(--line);
            background:rgba(0,0,0,.24);
            color:white;
            border-radius:14px;
            padding:14px 15px;
            font-size:16px;
            outline:none;
            margin-bottom:16px;
            transition:border-color .2s ease, box-shadow .2s ease, transform .2s ease;
        }
        input:focus {
            border-color:rgba(125,211,252,.75);
            box-shadow:0 0 0 4px rgba(125,211,252,.12);
        }
        .wifi-head {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:12px;
            margin-bottom:10px;
        }
        .wifi-head label { margin-bottom:0; }
        .refresh-link {
            color:var(--accent);
            text-decoration:none;
            font-size:13px;
            font-weight:900;
            white-space:nowrap;
            padding:8px 12px;
            border-radius:999px;
            border:1px solid rgba(125,211,252,.22);
            background:rgba(125,211,252,.08);
            transition:transform .18s ease, background .18s ease, border-color .18s ease;
        }
        .refresh-link:hover {
            text-decoration:none;
            transform:translateY(-1px);
            background:rgba(125,211,252,.14);
            border-color:rgba(125,211,252,.38);
        }
        .section-caption {
            margin:-2px 0 14px;
            color:var(--soft);
            font-size:13px;
            line-height:1.45;
        }
        .wifi-picker {
            margin-bottom:14px;
            border:1px solid rgba(255,255,255,.10);
            background:rgba(0,0,0,.16);
            border-radius:20px;
            overflow:hidden;
        }
        .wifi-picker-head {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:12px;
            padding:14px 16px 10px;
            border-bottom:1px solid rgba(255,255,255,.08);
            background:linear-gradient(180deg, rgba(125,211,252,.06), rgba(125,211,252,0));
        }
        .wifi-picker-head strong { display:block; font-size:14px; }
        .wifi-picker-head span { display:block; color:var(--soft); font-size:12px; margin-top:2px; }
        .selected-chip {
            max-width:52%;
            padding:8px 12px;
            border-radius:999px;
            background:rgba(125,211,252,.12);
            border:1px solid rgba(125,211,252,.24);
            color:#d8f3ff;
            font-size:12px;
            font-weight:800;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .wifi-grid {
            display:grid;
            gap:10px;
            padding:14px;
            max-height:360px;
            overflow:auto;
        }
        .wifi-grid::-webkit-scrollbar { width:10px; }
        .wifi-grid::-webkit-scrollbar-thumb {
            background:rgba(255,255,255,.12);
            border-radius:999px;
            border:2px solid transparent;
            background-clip:padding-box;
        }
        .wifi-card {
            position:relative;
            display:block;
            width:100%;
            text-align:left;
            border:1px solid rgba(255,255,255,.10);
            background:
                linear-gradient(180deg, rgba(255,255,255,.04), rgba(255,255,255,.02)),
                rgba(10,17,32,.55);
            color:var(--text);
            border-radius:18px;
            padding:14px 16px;
            cursor:pointer;
            transition:transform .20s ease, border-color .20s ease, box-shadow .20s ease, background .20s ease;
            opacity:0;
            transform:translateY(8px);
            animation:cardIn .35s ease forwards;
        }
        .wifi-card:hover {
            transform:translateY(-2px);
            border-color:rgba(125,211,252,.28);
            box-shadow:0 14px 28px rgba(0,0,0,.24);
        }
        .wifi-card.selected {
            border-color:var(--line-strong);
            background:
                radial-gradient(circle at top right, rgba(56,189,248,.18), transparent 42%),
                linear-gradient(180deg, rgba(125,211,252,.10), rgba(255,255,255,.04)),
                rgba(10,17,32,.72);
            box-shadow:0 0 0 1px rgba(125,211,252,.18), 0 18px 35px rgba(0,0,0,.28);
            transform:translateY(-1px);
        }
        .wifi-card.selected::after {
            content:"";
            position:absolute;
            inset:-1px;
            border-radius:18px;
            padding:1px;
            background:linear-gradient(135deg, rgba(125,211,252,.95), rgba(56,189,248,.25));
            -webkit-mask:linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
            -webkit-mask-composite:xor;
                    mask-composite:exclude;
            pointer-events:none;
        }
        .wifi-card.manual {
            border-style:dashed;
            background:linear-gradient(180deg, rgba(125,211,252,.06), rgba(255,255,255,.03));
        }
        .wifi-card-top {
            display:flex;
            align-items:flex-start;
            justify-content:space-between;
            gap:12px;
        }
        .wifi-name {
            display:flex;
            align-items:center;
            gap:10px;
            min-width:0;
        }
        .wifi-icon {
            width:42px;
            height:42px;
            border-radius:14px;
            display:grid;
            place-items:center;
            background:rgba(125,211,252,.10);
            border:1px solid rgba(125,211,252,.16);
            color:#d8f3ff;
            font-size:18px;
            flex:0 0 auto;
        }
        .wifi-name-text {
            min-width:0;
        }
        .wifi-name-text strong {
            display:block;
            font-size:15px;
            line-height:1.2;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .wifi-name-text span {
            display:block;
            margin-top:3px;
            font-size:12px;
            color:var(--soft);
        }
        .wifi-badges {
            display:flex;
            flex-wrap:wrap;
            justify-content:flex-end;
            gap:6px;
        }
        .badge {
            display:inline-flex;
            align-items:center;
            gap:5px;
            padding:6px 9px;
            border-radius:999px;
            font-size:11px;
            font-weight:800;
            background:rgba(255,255,255,.06);
            border:1px solid rgba(255,255,255,.08);
            color:#e5f6ff;
        }
        .badge.recommended {
            color:#07253a;
            background:linear-gradient(135deg, #7dd3fc, #38bdf8);
            border-color:transparent;
        }
        .wifi-meta {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:10px;
            margin-top:12px;
            color:var(--soft);
            font-size:12px;
        }
        .signal-wrap {
            display:flex;
            align-items:center;
            gap:8px;
            color:#dceef9;
            font-weight:800;
        }
        .signal-bars {
            display:flex;
            align-items:flex-end;
            gap:3px;
            height:16px;
        }
        .signal-bars span {
            width:4px;
            border-radius:999px;
            background:rgba(255,255,255,.20);
            transition:background .18s ease, transform .18s ease;
        }
        .signal-bars span:nth-child(1) { height:5px; }
        .signal-bars span:nth-child(2) { height:8px; }
        .signal-bars span:nth-child(3) { height:11px; }
        .signal-bars span:nth-child(4) { height:14px; }
        .signal-bars.level-1 span:nth-child(-n+1),
        .signal-bars.level-2 span:nth-child(-n+2),
        .signal-bars.level-3 span:nth-child(-n+3),
        .signal-bars.level-4 span:nth-child(-n+4) {
            background:linear-gradient(180deg, #7dd3fc, #38bdf8);
        }
        .wifi-empty-state {
            padding:14px 16px 2px;
        }
        .manual-ssid {
            display:none;
            padding:0 0 2px;
            animation:fadeUp .22s ease;
        }
        .manual-ssid.show { display:block; }
        .pairing {
            margin-top:18px;
            padding:18px;
            border-radius:20px;
            border:1px solid rgba(125,211,252,.22);
            background:rgba(125,211,252,.08);
        }
        .pairing h2 { margin:0 0 7px; font-size:18px; }
        .advanced { margin-top:8px; }
        .advanced summary { cursor:pointer; color:var(--accent); font-weight:800; margin-bottom:10px; }
        button[type="submit"] {
            width:100%;
            border:0;
            border-radius:16px;
            padding:15px 16px;
            font-size:16px;
            font-weight:900;
            color:#02111f;
            background:linear-gradient(135deg,#7dd3fc,#38bdf8);
            cursor:pointer;
            margin-top:4px;
            transition:transform .18s ease, box-shadow .18s ease;
            box-shadow:0 16px 30px rgba(56,189,248,.22);
        }
        button[type="submit"]:hover {
            transform:translateY(-1px);
            box-shadow:0 20px 35px rgba(56,189,248,.26);
        }
        .notice {
            margin:0 0 16px;
            padding:14px;
            border-radius:14px;
            font-size:14px;
            line-height:1.45;
        }
        .notice.good { background:rgba(34,197,94,.14); border:1px solid rgba(34,197,94,.35); color:#bbf7d0; }
        .notice.bad { background:rgba(239,68,68,.14); border:1px solid rgba(239,68,68,.35); color:#fecaca; }
        .notice.warn { background:rgba(245,158,11,.14); border:1px solid rgba(245,158,11,.35); color:#fde68a; }
        .hint { margin-top:16px; color:rgba(255,255,255,.50); font-size:12px; line-height:1.45; }

        @keyframes cardIn {
            from { opacity:0; transform:translateY(8px); }
            to { opacity:1; transform:translateY(0); }
        }
        @keyframes fadeUp {
            from { opacity:0; transform:translateY(6px); }
            to { opacity:1; transform:translateY(0); }
        }

        @media (max-width: 640px) {
            body { padding:12px; align-items:flex-start; }
            .card { padding:18px; border-radius:22px; }
            h1 { font-size:28px; }
            .wifi-picker-head,
            .wifi-head {
                flex-direction:column;
                align-items:flex-start;
            }
            .selected-chip { max-width:100%; }
            .wifi-card-top,
            .wifi-meta {
                flex-direction:column;
                align-items:flex-start;
            }
            .wifi-badges { justify-content:flex-start; }
        }
    </style>
</head>

<body>
    <main class="card">
        <div class="eyebrow">QRelia Ambient Device</div>
        <h1>Setup takes 3 steps</h1>
        <p>Keep this page open until the device confirms the details were saved. After restart, check the LCD for the final connection status.</p>

        {% if pairing_error %}
            <div class="notice bad"><strong>Pairing failed:</strong> {{ pairing_error }}<br>Re-enter the setup code and PIN from the admin page below.</div>
        {% endif %}

        {% if message %}
            <div class="notice {{ message_type }}">{{ message }}</div>
        {% endif %}

        <div class="steps">
            <div class="step"><span class="num">1</span><span><strong>Venue Wi-Fi</strong> Choose the Wi-Fi this device should use every day, then enter its password.</span></div>
            <div class="step"><span class="num">2</span><span><strong>QRelia pairing</strong> Copy the setup code and PIN from the device order page.</span></div>
            <div class="step"><span class="num">3</span><span><strong>Restart and automatic recovery</strong> The LCD confirms the restart. If the saved Wi-Fi cannot connect, QRelia-Setup returns automatically so the details can be corrected.</span></div>
        </div>

        <form method="post" action="/save">
            <div class="wifi-head">
                <label for="selected_ssid">1. Choose venue Wi-Fi</label>
                <a class="refresh-link" href="/?refresh=1">Refresh list</a>
            </div>
            <div class="section-caption">Tap a nearby network card below. If the venue Wi-Fi is hidden, choose the hidden network card and type the name manually.</div>

            {% if wifi_networks %}
                <input type="hidden" id="selected_ssid" name="selected_ssid" value="{{ selected_ssid_value }}">

                <div class="wifi-picker">
                    <div class="wifi-picker-head">
                        <div>
                            <strong>Nearby Wi-Fi networks</strong>
                            <span>
                                Found {{ wifi_networks|length }} nearby network{% if wifi_networks|length != 1 %}s{% endif %}.
                                {% if wifi_scan_meta and wifi_scan_meta.isStale %}Showing the last saved scan because a live refresh was not available.{% else %}Choose the venue network from the list below.{% endif %}
                            </span>
                        </div>
                        <div id="selectedChip" class="selected-chip">No Wi-Fi selected yet</div>
                    </div>

                    <div class="wifi-grid" id="wifiGrid">
                        {% for network in wifi_networks %}
                            {% set signal_value = network.signal|int %}
                            {% set signal_level = 1 if signal_value < 26 else 2 if signal_value < 51 else 3 if signal_value < 76 else 4 %}
                            <button
                                type="button"
                                class="wifi-card{% if selected_ssid_value == network.ssid %} selected{% endif %}"
                                data-ssid="{{ network.ssid }}"
                                data-label="{{ network.ssid|e }}"
                                style="animation-delay: {{ '%.2f'|format(loop.index0 * 0.03) }}s;"
                            >
                                <div class="wifi-card-top">
                                    <div class="wifi-name">
                                        <div class="wifi-icon">📶</div>
                                        <div class="wifi-name-text">
                                            <strong>{{ network.ssid }}</strong>
                                            <span>{{ network.security }}</span>
                                        </div>
                                    </div>
                                    <div class="wifi-badges">
                                        {% if loop.first %}<span class="badge recommended">Best signal</span>{% endif %}
                                        <span class="badge">{{ network.security }}</span>
                                    </div>
                                </div>
                                <div class="wifi-meta">
                                    <div class="signal-wrap">
                                        <div class="signal-bars level-{{ signal_level }}"><span></span><span></span><span></span><span></span></div>
                                        <span>{{ network.signal }}% signal</span>
                                    </div>
                                    <span>{% if signal_value >= 70 %}Strong connection{% elif signal_value >= 45 %}Good connection{% elif signal_value >= 25 %}Okay connection{% else %}Weak connection{% endif %}</span>
                                </div>
                            </button>
                        {% endfor %}

                        <button
                            type="button"
                            class="wifi-card manual{% if selected_ssid_value == '__manual__' or (manual_ssid_value and not selected_ssid_value) %} selected{% endif %}"
                            data-ssid="__manual__"
                            data-label="Hidden or other Wi-Fi"
                            style="animation-delay: {{ '%.2f'|format(wifi_networks|length * 0.03) }}s;"
                        >
                            <div class="wifi-card-top">
                                <div class="wifi-name">
                                    <div class="wifi-icon">✏️</div>
                                    <div class="wifi-name-text">
                                        <strong>Hidden or other Wi-Fi</strong>
                                        <span>Use this if the venue network is not visible in the scan.</span>
                                    </div>
                                </div>
                                <div class="wifi-badges">
                                    <span class="badge">Manual entry</span>
                                </div>
                            </div>
                            <div class="wifi-meta">
                                <span>Type the Wi-Fi name yourself</span>
                                <span>For hidden SSIDs</span>
                            </div>
                        </button>
                    </div>
                </div>

                <div id="manualSsidWrap" class="manual-ssid{% if selected_ssid_value == '__manual__' or (manual_ssid_value and not selected_ssid_value) %} show{% endif %}">
                    <label for="manual_ssid">Hidden / other Wi-Fi name</label>
                    <input id="manual_ssid" name="manual_ssid" placeholder="Hidden or unlisted Wi-Fi name" autocomplete="off" value="{{ manual_ssid_value }}">
                </div>
            {% else %}
                <div class="wifi-picker">
                    <div class="wifi-empty-state">
                        <input id="manual_ssid" name="manual_ssid" placeholder="Venue Wi-Fi name" required autocomplete="off" value="{{ manual_ssid_value }}">
                        <div class="section-caption">No nearby Wi-Fi list is available yet. Enter the network name manually, or tap Refresh list.</div>
                    </div>
                </div>
            {% endif %}

            {% if wifi_scan_error %}
                <div class="notice warn">Could not fully refresh the Wi-Fi list, so QRelia is using the best available scan. You can still choose a network or use Hidden or other Wi-Fi.</div>
            {% endif %}

            <label for="password">Wi-Fi password</label>
            <input id="password" name="password" type="password" placeholder="Wi-Fi password" required autocomplete="off">

            <div class="pairing">
                <h2>2. QRelia pairing details</h2>
                {% if is_provisioned and not pairing_error %}
                    <p>This device is already paired. Leave code and PIN blank only when you are changing Wi-Fi for the same venue.</p>
                {% elif pairing_saved and not pairing_error %}
                    <p>Pairing details are already stored from the previous attempt. Leave these fields blank while correcting the Wi-Fi password.</p>
                {% else %}
                    <p>Required before this device can go live. Invalid values are rejected by QRelia and the device will stay in setup.</p>
                {% endif %}

                <label for="setup_code">Exact setup code</label>
                <input id="setup_code" name="setup_code" placeholder="QR-A1B2C3-000123-01" autocomplete="off" autocapitalize="characters" value="{{ setup_code_value }}" {% if pairing_required %}required{% endif %}>

                <label for="setup_pin">Exact 6-digit PIN</label>
                <input id="setup_pin" name="setup_pin" placeholder="123456" inputmode="numeric" pattern="[0-9]{6}" autocomplete="off" value="{{ setup_pin_value }}" {% if pairing_required %}required{% endif %}>

                <details class="advanced">
                    <summary>Advanced QRelia URL</summary>
                    <label for="admin_base_url">QRelia admin URL</label>
                    <input id="admin_base_url" name="admin_base_url" value="{{ admin_base_url }}" autocomplete="off">
                </details>
            </div>

            <button type="submit">Save setup and restart device</button>
        </form>

        <div class="hint">
            Setup should open automatically after joining QRelia-Setup.<br>
            Manual address: qrelia.local / 192.168.4.1<br>
            Setup network: QRelia-Setup<br>
            Pairing file: {{ provisioning_path }}
        </div>
    </main>

    <script>
        (function () {
            var hiddenSelectedInput = document.getElementById('selected_ssid');
            var manualWrap = document.getElementById('manualSsidWrap');
            var manualInput = document.getElementById('manual_ssid');
            var selectedChip = document.getElementById('selectedChip');
            var wifiCards = Array.prototype.slice.call(document.querySelectorAll('.wifi-card[data-ssid]'));

            function updateSelectedChip() {
                if (!selectedChip || !hiddenSelectedInput) return;
                if (!hiddenSelectedInput.value) {
                    selectedChip.textContent = 'No Wi-Fi selected yet';
                    return;
                }
                if (hiddenSelectedInput.value === '__manual__') {
                    selectedChip.textContent = manualInput && manualInput.value
                        ? 'Selected: ' + manualInput.value
                        : 'Selected: Hidden or other Wi-Fi';
                    return;
                }
                var selected = wifiCards.find(function (card) {
                    return card.getAttribute('data-ssid') === hiddenSelectedInput.value;
                });
                selectedChip.textContent = 'Selected: ' + (selected ? selected.getAttribute('data-label') : hiddenSelectedInput.value);
            }

            function syncManualSsid() {
                if (!hiddenSelectedInput || !manualWrap || !manualInput) {
                    updateSelectedChip();
                    return;
                }
                var showManual = hiddenSelectedInput.value === '__manual__';
                manualWrap.classList.toggle('show', showManual);
                manualInput.required = showManual;
                if (!showManual) {
                    manualInput.value = '';
                }
                updateSelectedChip();
            }

            function setSelectedCard(value) {
                if (!hiddenSelectedInput) return;
                hiddenSelectedInput.value = value;
                wifiCards.forEach(function (card) {
                    var isSelected = card.getAttribute('data-ssid') === value;
                    card.classList.toggle('selected', isSelected);
                    card.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
                });
                syncManualSsid();
                if (value === '__manual__' && manualInput) {
                    setTimeout(function () { manualInput.focus(); }, 80);
                }
            }

            wifiCards.forEach(function (card) {
                card.addEventListener('click', function () {
                    setSelectedCard(card.getAttribute('data-ssid'));
                });
            });

            if (manualInput) {
                manualInput.addEventListener('input', updateSelectedChip);
            }

            syncManualSsid();
        })();
    </script>
</body>
</html>
"""


def host_without_port():
    return (request.host or "").split(":", 1)[0].strip().lower()


def is_captive_portal_probe(path):
    path = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    lower_path = path.lower()

    if lower_path in CAPTIVE_PORTAL_PATHS:
        return True

    return lower_path.startswith((
        "/generate_204",
        "/gen_204",
        "/hotspot-detect",
        "/connecttest",
        "/ncsi",
        "/canonical",
    ))


def captive_redirect_response():
    # 302 is deliberate: Android, iOS, Windows and Linux network managers treat
    # this as a captive portal instead of a normal internet connection.
    response = redirect(SETUP_URL, code=302)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.before_request
def handle_captive_portal_detection():
    if request.method != "GET":
        return None

    path = request.path or "/"
    host = host_without_port()
    reboot_transition = reboot_handoff_transition()

    if reboot_transition:
        # Captive portal operating systems keep probing while the confirmation
        # page is visible. Re-latch the display on every late GET so neither a probe
        # nor a browser refresh can replace SETUP SAVED / RESTARTING.
        latch_reboot_display(reboot_transition.get("ssid"))

        if is_captive_portal_probe(path) or (host and host not in KNOWN_SETUP_HOSTS):
            return captive_redirect_response()
        return None

    if is_captive_portal_probe(path):
        set_display_state("phone_connected", message="Setup opened", ip=SETUP_IP)
        return captive_redirect_response()

    if host and host not in KNOWN_SETUP_HOSTS:
        # dnsmasq resolves every HTTP hostname to the setup device while the
        # owner is connected to QRelia-Setup. Redirect those requests to the
        # friendly local setup hostname so the screen always lands on QRelia.
        set_display_state("phone_connected", message="Setup opened", ip=SETUP_IP)
        return captive_redirect_response()

    return None


def normalise_base_url(value):
    value = (value or DEFAULT_ADMIN_BASE_URL).strip().rstrip("/")
    if not value:
        value = DEFAULT_ADMIN_BASE_URL
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


def read_json(path):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Could not read {path}: {exc}", flush=True)
    return {}


def is_device_provisioned():
    config = read_json(DEVICE_CONFIG_PATH)
    return bool(str(config.get("tenantId") or "").strip() and str(config.get("deviceId") or "").strip())


def has_pending_provisioning_request():
    request_data = read_json(PROVISIONING_PATH)
    return valid_setup_code(normalise_setup_code(request_data.get("setupCode"))) and valid_setup_pin(str(request_data.get("setupPin") or ""))


def pairing_error_message():
    data = read_json(PROVISIONING_FAILURE_PATH)
    if bool(data.get("retryable")):
        return ""
    return str(data.get("message") or "").strip()


def normalise_setup_code(setup_code):
    return re.sub(r"\s+", "", (setup_code or "").strip().upper())


def valid_setup_code(setup_code):
    return bool(re.fullmatch(r"QR-[A-Z0-9]{6}-[0-9]{6}-[0-9]{2}", setup_code or ""))


def valid_setup_pin(setup_pin):
    return bool(re.fullmatch(r"[0-9]{6}", setup_pin or ""))


def clear_pairing_error():
    try:
        if PROVISIONING_FAILURE_PATH.exists():
            PROVISIONING_FAILURE_PATH.unlink()
    except Exception as exc:
        print(f"Could not remove pairing error marker: {exc}", flush=True)


def write_provisioning_request(setup_code, setup_pin, admin_base_url):
    payload = {
        "setupCode": normalise_setup_code(setup_code),
        "setupPin": setup_pin.strip(),
        "adminBaseUrl": normalise_base_url(admin_base_url),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

    PROVISIONING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROVISIONING_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(PROVISIONING_PATH, 0o600)
    except Exception:
        pass


def render_setup_page(
    message=None,
    message_type=None,
    refresh_networks=False,
    selected_ssid_value="",
    manual_ssid_value="",
    setup_code_value="",
    setup_pin_value="",
    admin_base_url_value=None,
):
    is_provisioned = is_device_provisioned()
    pairing_saved = has_pending_provisioning_request()
    pairing_error = pairing_error_message()
    wifi_error = wifi_setup_error_message()
    if message is None and wifi_error:
        message = wifi_error
        message_type = "bad"
    wifi_networks, wifi_scan_meta, wifi_scan_error = get_wifi_networks(force_refresh=refresh_networks)

    return render_template_string(
        PAGE,
        message=message,
        message_type=message_type,
        is_provisioned=is_provisioned,
        pairing_saved=pairing_saved,
        pairing_required=((not is_provisioned) and (not pairing_saved)) or bool(pairing_error),
        pairing_error=pairing_error,
        admin_base_url=admin_base_url_value or DEFAULT_ADMIN_BASE_URL,
        provisioning_path=str(PROVISIONING_PATH),
        wifi_networks=wifi_networks,
        wifi_scan_meta=wifi_scan_meta,
        wifi_scan_error=wifi_scan_error,
        selected_ssid_value=selected_ssid_value,
        manual_ssid_value=manual_ssid_value,
        setup_code_value=setup_code_value,
        setup_pin_value=setup_pin_value,
    )


def run(command):
    return subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True
    )


def safe_shell(value):
    return shlex.quote(value)


@app.route("/generate_204", methods=["GET"])
@app.route("/gen_204", methods=["GET"])
@app.route("/hotspot-detect.html", methods=["GET"])
@app.route("/library/test/success.html", methods=["GET"])
@app.route("/connecttest.txt", methods=["GET"])
@app.route("/ncsi.txt", methods=["GET"])
@app.route("/canonical.html", methods=["GET"])
@app.route("/success.txt", methods=["GET"])
def captive_portal_probe():
    return captive_redirect_response()


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    return Response(status=204)


@app.route("/", methods=["GET"])
def index():
    reboot_transition = reboot_handoff_transition()
    if reboot_transition:
        ssid = str(reboot_transition.get("ssid") or "Venue Wi-Fi")
        latch_reboot_display(ssid)
        return render_template_string(SETUP_SAVED_PAGE, ssid=ssid)

    set_display_state("wifi_form")
    return render_setup_page(refresh_networks=request.args.get("refresh") == "1")

@app.route("/save", methods=["POST"])
def save():
    selected_ssid = request.form.get("selected_ssid", "").strip()
    manual_ssid = request.form.get("manual_ssid", "").strip()
    legacy_ssid = request.form.get("ssid", "").strip()
    ssid = manual_ssid if selected_ssid == "__manual__" else (selected_ssid or legacy_ssid or manual_ssid)
    password = request.form.get("password", "").strip()
    setup_code = request.form.get("setup_code", "").strip()
    setup_pin = request.form.get("setup_pin", "").strip()
    admin_base_url = request.form.get("admin_base_url", DEFAULT_ADMIN_BASE_URL).strip()
    provisioned = is_device_provisioned()
    set_display_state("saving_wifi", ssid=ssid)

    if not ssid:
        set_display_state("error", message="Missing Wi-Fi name")
        return render_setup_page(
            "Please choose a Wi-Fi network or select Hidden or other Wi-Fi.",
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    if len(password) < 8:
        set_display_state("error", message="Password too short")
        return render_setup_page(
            "Wi-Fi password must be at least 8 characters.",
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    pairing_required = ((not provisioned) and (not has_pending_provisioning_request())) or bool(pairing_error_message())
    setup_code = normalise_setup_code(setup_code)

    if (pairing_required or setup_code or setup_pin) and (not setup_code or not setup_pin):
        set_display_state("error", message="Missing setup PIN")
        return render_setup_page(
            "Setup code and PIN are required before this device can pair with QRelia.",
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    if setup_code and not valid_setup_code(setup_code):
        set_display_state("error", message="Bad setup code")
        return render_setup_page(
            "Setup code format is not valid. Use the exact code from QRelia admin, for example QR-A1B2C3-000123-01.",
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    if setup_pin and not valid_setup_pin(setup_pin):
        set_display_state("error", message="Invalid setup PIN")
        return render_setup_page(
            "Setup PIN must be exactly 6 digits.",
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    if setup_code and setup_pin:
        try:
            write_provisioning_request(setup_code, setup_pin, admin_base_url)
            clear_pairing_error()
        except Exception as exc:
            set_display_state("error", message="Pairing save failed")
            return render_setup_page(
                "Could not save QRelia pairing details: " + html.escape(str(exc)),
                "bad",
                selected_ssid_value=selected_ssid,
                manual_ssid_value=manual_ssid,
                setup_code_value=setup_code,
                setup_pin_value=setup_pin,
                admin_base_url_value=admin_base_url,
            )

    connection_name = WIFI_CONNECTION_NAME
    clear_wifi_setup_failure()

    escaped_ssid = safe_shell(ssid)
    escaped_password = safe_shell(password)
    escaped_connection_name = safe_shell(connection_name)

    # From this point onward a saved Wi-Fi profile may become visible to the
    # watchdog at any instant.  Protect the entire save/reboot transaction, not
    # just the final three-second reboot delay.
    mark_setup_transition(ssid, state="saving_wifi")

    # Remove previous QRelia Wi-Fi profile if it exists.
    run(f"nmcli connection delete {escaped_connection_name} || true")

    # Create new Wi-Fi profile.
    create_result = run(
        f"nmcli connection add "
        f"type wifi "
        f"ifname wlan0 "
        f"con-name {escaped_connection_name} "
        f"ssid {escaped_ssid}"
    )

    if create_result.returncode != 0:
        run(f"nmcli connection delete {escaped_connection_name} || true")
        clear_setup_transition()
        set_display_state("wifi_failed", ssid=ssid, message="Profile failed")
        return render_setup_page(
            "Could not create Wi-Fi profile: " + html.escape(create_result.stderr),
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    # Add WPA password.
    password_result = run(
        f"nmcli connection modify {escaped_connection_name} "
        f"wifi-sec.key-mgmt wpa-psk "
        f"wifi-sec.psk {escaped_password} "
        f"connection.autoconnect yes "
        f"connection.autoconnect-priority 100"
    )

    if password_result.returncode != 0:
        run(f"nmcli connection delete {escaped_connection_name} || true")
        clear_setup_transition()
        set_display_state("wifi_failed", ssid=ssid, message="Password failed")
        return render_setup_page(
            "Could not save Wi-Fi password: " + html.escape(password_result.stderr),
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    if selected_ssid == "__manual__":
        run(f"nmcli connection modify {escaped_connection_name} 802-11-wireless.hidden yes")

    # Keep router DNS, but add public fallback resolvers so admin.qrelia.uk can resolve
    # even on networks that hand out weak/empty DNS settings during first boot.
    run(
        f"nmcli connection modify {escaped_connection_name} "
        f"ipv4.dns '1.1.1.1 8.8.8.8' "
        f"ipv4.ignore-auto-dns no"
    )

    # Persist a first-connection marker across reboot. The watchdog clears it
    # only after this exact NetworkManager profile connects and receives an IP.
    # If that never happens, the failed profile is removed and setup mode returns.
    try:
        write_wifi_setup_pending(ssid, connection_name)
    except Exception as exc:
        run(f"nmcli connection delete {escaped_connection_name} || true")
        clear_setup_transition()
        set_display_state("error", ssid=ssid, message="Setup marker failed")
        return render_setup_page(
            "Could not prepare the first Wi-Fi connection check: " + html.escape(str(exc)),
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    # Protect the hand-off first, then schedule reboot. The explicit display state
    # is written only after systemd confirms that the reboot timer exists. This
    # gives the renderer the full delay window and prevents any portal probe from
    # replacing the screen before shutdown.
    mark_setup_transition(ssid, state="reboot_scheduled")
    reboot_scheduled, reboot_error = schedule_reboot(delay_seconds=12)

    if not reboot_scheduled:
        clear_setup_transition()
        set_display_state("error", ssid=ssid, message="Restart device")
        return render_setup_page(
            "The setup details were saved, but automatic restart could not be scheduled. "
            "Please restart the device manually. Technical detail: " + html.escape(reboot_error),
            "bad",
            selected_ssid_value=selected_ssid,
            manual_ssid_value=manual_ssid,
            setup_code_value=setup_code,
            setup_pin_value=setup_pin,
            admin_base_url_value=admin_base_url,
        )

    latch_reboot_display(ssid)
    try:
        os.sync()
    except Exception:
        pass

    # Let the 60 FPS display renderer consume at least several frames before the
    # response is returned. The state remains latched for the rest of the
    # 12-second reboot window regardless of browser/captive-portal requests.
    time.sleep(0.75)
    return render_template_string(SETUP_SAVED_PAGE, ssid=ssid)


@app.route("/<path:unused_path>", methods=["GET"])
def setup_catchall(unused_path):
    return captive_redirect_response()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
