#!/usr/bin/env python3
"""Shared setup/status state consumed by the production LCD runtime."""
import json
import os
import time
from pathlib import Path

DISPLAY_STATE_FILE = Path(os.environ.get("QRELIA_DISPLAY_STATE_FILE", "/tmp/qrelia_display_state.json"))

def set_display_state(state, ssid="", message="", ip=""):
    payload = {
        "state": str(state or ""),
        "ssid": str(ssid or ""),
        "message": str(message or ""),
        "ip": str(ip or ""),
        "updatedAtUnix": time.time(),
    }
    DISPLAY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = DISPLAY_STATE_FILE.with_suffix(DISPLAY_STATE_FILE.suffix + ".tmp")
    temp.write_text(json.dumps(payload), encoding="utf-8")
    temp.replace(DISPLAY_STATE_FILE)
    try:
        os.chmod(DISPLAY_STATE_FILE, 0o644)
    except Exception:
        pass
    return payload

def read_display_state():
    try:
        if DISPLAY_STATE_FILE.exists():
            data = json.loads(DISPLAY_STATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}
