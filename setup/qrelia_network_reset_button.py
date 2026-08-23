#!/usr/bin/env python3
"""
QRelia hardware network reset button watcher.

Wiring:
- GPIO26 / physical pin 37 -> one side of momentary button
- GND / physical pin 39 -> other side of momentary button

The script uses the Raspberry Pi internal pull-up resistor, so the button
must short GPIO26 to ground when pressed.
"""

import os
import subprocess
import time
from pathlib import Path

from gpiozero import Button
from display_state import read_display_state, set_display_state


BUTTON_GPIO = int(os.environ.get("QRELIA_RESET_BUTTON_GPIO", "26"))
HOLD_SECONDS = float(os.environ.get("QRELIA_RESET_BUTTON_HOLD_SECONDS", "7"))
POLL_SECONDS = float(os.environ.get("QRELIA_RESET_BUTTON_POLL_SECONDS", "0.05"))
BOUNCE_SECONDS = float(os.environ.get("QRELIA_RESET_BUTTON_BOUNCE_SECONDS", "0.08"))

RESET_SCRIPT = Path(os.environ.get(
    "QRELIA_HARD_RESET_SCRIPT",
    "/home/qrelia/qrelia/setup/qrelia_hard_reset_network.sh"
))


def log(message):
    print(f"[QRelia Reset Button] {message}", flush=True)


def restore_display_state(previous_state):
    if previous_state:
        set_display_state(
            previous_state.get("state", "boot"),
            ssid=previous_state.get("ssid", ""),
            message=previous_state.get("message", ""),
            ip=previous_state.get("ip", ""),
        )
        return
    set_display_state("boot", message="Reset cancelled")


def run_hard_reset():
    if not RESET_SCRIPT.exists():
        log(f"Reset script not found: {RESET_SCRIPT}")
        set_display_state("error", message="Reset script missing")
        return

    log("Long press confirmed. Running hard network reset.")
    set_display_state("setup_starting", message="Reset all")

    try:
        subprocess.run(["/bin/bash", str(RESET_SCRIPT)], check=False)
    except Exception as ex:
        log(f"Hard reset failed: {ex}")
        set_display_state("error", message="Reset failed")


def wait_until_released(button):
    while button.is_pressed:
        time.sleep(POLL_SECONDS)


def monitor_button():
    button = Button(
        BUTTON_GPIO,
        pull_up=True,
        bounce_time=BOUNCE_SECONDS,
        hold_time=HOLD_SECONDS,
        hold_repeat=False,
    )

    log(
        f"Monitoring GPIO{BUTTON_GPIO}. "
        f"Hold for {HOLD_SECONDS:.1f}s to reset venue Wi-Fi and QRelia pairing."
    )

    reset_in_progress = False

    while True:
        if reset_in_progress:
            time.sleep(1)
            continue

        button.wait_for_press()
        previous_display_state = read_display_state()
        pressed_at = time.monotonic()
        last_remaining = None

        log("Button pressed. Waiting for long hold confirmation.")

        while button.is_pressed:
            elapsed = time.monotonic() - pressed_at
            remaining = max(0, int(round(HOLD_SECONDS - elapsed)))

            if remaining != last_remaining:
                last_remaining = remaining
                if remaining > 0:
                    set_display_state(
                        "reset_armed",
                        message=f"Reset all {remaining}s",
                        ip=""
                    )
                else:
                    set_display_state(
                        "setup_starting",
                        message="Reset all",
                        ip=""
                    )

            if elapsed >= HOLD_SECONDS:
                reset_in_progress = True
                run_hard_reset()
                wait_until_released(button)
                break

            time.sleep(POLL_SECONDS)

        if not reset_in_progress:
            log("Button released before hold threshold. Reset cancelled. Restoring previous display state.")
            restore_display_state(previous_display_state)
            time.sleep(0.25)


if __name__ == "__main__":
    monitor_button()
