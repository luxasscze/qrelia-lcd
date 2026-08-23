#!/usr/bin/env python3
"""QRelia Hospitality 100 production animation collection.

The existing QRelia 100 library remains untouched. This module contributes
25 reusable, non-blocking, per-pixel engines with four curated variants each.
"""
from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List

try:
    from rpi_ws281x import Color
except Exception:
    def Color(r: int, g: int, b: int) -> int:
        return ((int(r) & 255) << 16) | ((int(g) & 255) << 8) | (int(b) & 255)

strip = None


def init_strip(value: Any) -> None:
    global strip
    strip = value


def clamp(value: float, low: int = 0, high: int = 255) -> int:
    return max(low, min(high, int(value)))


def rgb(r: float, g: float, b: float) -> int:
    return Color(clamp(r), clamp(g), clamp(b))


def set_pixel(index: int, r: float, g: float, b: float) -> None:
    if strip is not None and 0 <= index < strip.numPixels():
        strip.setPixelColor(index, rgb(r, g, b))


def get_rgb_tuple(index: int) -> tuple[int, int, int]:
    colour = strip.getPixelColor(index)
    return (colour >> 16) & 0xFF, (colour >> 8) & 0xFF, colour & 0xFF


def clear(show_now: bool = True) -> None:
    for index in range(strip.numPixels()):
        strip.setPixelColor(index, 0)
    if show_now:
        strip.show()


def fade_all(factor: float) -> None:
    for index in range(strip.numPixels()):
        r, g, b = get_rgb_tuple(index)
        set_pixel(index, r * factor, g * factor, b * factor)


def add_pixel(index: int, r: float, g: float, b: float) -> None:
    current_r, current_g, current_b = get_rgb_tuple(index)
    set_pixel(index, current_r + r, current_g + g, current_b + b)


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    h %= 1.0
    s = max(0.0, min(1.0, s))
    v = max(0.0, min(1.0, v))
    sector = int(h * 6.0)
    fraction = h * 6.0 - sector
    p = v * (1.0 - s)
    q = v * (1.0 - fraction * s)
    t = v * (1.0 - (1.0 - fraction) * s)
    sector %= 6
    if sector == 0: r, g, b = v, t, p
    elif sector == 1: r, g, b = q, v, p
    elif sector == 2: r, g, b = p, v, t
    elif sector == 3: r, g, b = p, q, v
    elif sector == 4: r, g, b = t, p, v
    else: r, g, b = v, p, q
    return clamp(r * 255), clamp(g * 255), clamp(b * 255)


def smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def frac(value: float) -> float:
    return value - math.floor(value)


def hash11(value: float) -> float:
    return frac(math.sin(value * 127.1) * 43758.5453123)


def hash21(x: float, y: float) -> float:
    return frac(math.sin(x * 127.1 + y * 311.7) * 43758.5453123)


def wave01(value: float) -> float:
    return 0.5 + 0.5 * math.sin(value)


def dist_ring(index: int, position: float, count: int) -> float:
    return min((index - position) % count, (position - index) % count)


def hue_mix(h1: float, h2: float, amount: float) -> float:
    delta = ((h2 - h1 + 0.5) % 1.0) - 0.5
    return (h1 + delta * amount) % 1.0

def engine_lux_aurora(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    h1, h2 = p["h1"], p["h2"]
    speed = p.get("speed", 0.65)
    scale = p.get("scale", 0.035)
    for i in range(n):
        x = i * scale
        a = wave01(x * 3.1 + t * speed)
        b = wave01(x * 7.2 - t * speed * 0.57 + math.sin(t * 0.21))
        m = smoothstep((a + b) * 0.5)
        h = hue_mix(h1, h2, m + 0.08 * math.sin(i * 0.01 + t * 0.09))
        v = 0.16 + 0.84 * m
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.72), v)
        set_pixel(i, r, g, b)

def engine_champagne_twinkle(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    mem = anim.state["twinkle"]
    fade = p.get("fade", 0.91)
    density = p.get("density", 0.045)
    for i in range(n):
        mem[i] *= fade
        if random.random() < density * (0.3 + hash21(i, int(t * 12))):
            mem[i] = 1.0
        base = p.get("base", 0.035) + 0.03 * wave01(i * 0.07 + t * 0.2)
        v = min(1.0, base + mem[i])
        h = p["hue"] + p.get("spread", 0.04) * hash11(i)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.32), v)
        set_pixel(i, r, g, b)

def engine_comet_tail(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    pos = (t * p.get("speed", 18.0)) % n
    tail = p.get("tail", 18.0)
    for i in range(n):
        d = dist_ring(i, pos, n)
        v = max(0.0, 1.0 - (d / tail) ** p.get("softness", 1.55))
        h = p["hue"] + p.get("hue_drift", 0.02) * (i / max(1, n))
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.95), v)
        set_pixel(i, r, g, b)

def engine_meteor_shower(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    mem = anim.state["memory_a"]
    for i in range(n):
        mem[i] *= p.get("decay", 0.82)
    if random.random() < p.get("spawn", 0.18):
        mem[random.randrange(n)] = 1.0
    shift = int(t * p.get("speed", 20.0)) % n
    for i in range(n):
        v = mem[(i - shift) % n]
        h = p["hue"] + p.get("spread", 0.02) * math.sin(i * 0.1 + t)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.95), v)
        set_pixel(i, r, g, b)

def engine_lava_lamp(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    for i in range(n):
        x = i / max(1, n - 1)
        a = wave01(x * p.get("scale1", 8.0) + t * p.get("speed1", 0.7))
        b = wave01(x * p.get("scale2", 17.0) - t * p.get("speed2", 0.45))
        c = wave01(x * 31.0 + math.sin(t * 0.4) * 2)
        v = smoothstep((a + b + c) / 3.0)
        r, g, b = hsv_to_rgb(p["hue"] + 0.06 * v, p.get("sat", 0.92), 0.15 + 0.85 * v)
        set_pixel(i, r, g, b)

def engine_ocean_swell(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    for i in range(n):
        wave = wave01(i * p.get("scale", 0.07) + t * p.get("speed", 1.2) + 0.8 * math.sin(t * 0.3 + i * 0.02))
        foam = wave01(i * 0.31 - t * 3.2) ** 8
        h = p["hue"] + 0.04 * wave
        v = 0.18 + 0.65 * wave + p.get("foam", 0.15) * foam
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.82), min(1.0, v))
        set_pixel(i, r, g, b)

def engine_ripple_center(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    center = (0.5 + 0.5 * math.sin(t * p.get("center_speed", 0.5))) * (n - 1)
    for i in range(n):
        d = abs(i - center)
        w = wave01(d * p.get("freq", 0.65) - t * p.get("speed", 4.5))
        fade = max(0.0, 1.0 - d / (n * p.get("decay", 0.75)))
        r, g, b = hsv_to_rgb(p["hue"] + d * p.get("hue_step", 0.0009), p.get("sat", 0.9), w * fade)
        set_pixel(i, r, g, b)

def engine_nebula_cloud(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    h1, h2, h3 = p["h1"], p["h2"], p["h3"]
    for i in range(n):
        x = i / max(1, n)
        a = wave01(x * 7 + t * p.get("speed", 0.55))
        b = wave01(x * 13 - t * 0.41)
        c = wave01(x * 23 + math.sin(t * 0.31))
        m = (a + b + c) / 3
        h = hue_mix(hue_mix(h1, h2, a), h3, b * 0.55)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.72), 0.12 + 0.88 * smoothstep(m))
        set_pixel(i, r, g, b)

def engine_starfield(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    hue = p["hue"]
    for i in range(n):
        base = p.get("base", 0.025) + 0.025 * wave01(i * 0.17 + t * 0.12)
        sparkle = 1.0 if random.random() < p.get("sparkle", 0.012) else 0.0
        v = min(1.0, base + sparkle)
        r, g, b = hsv_to_rgb(hue + 0.08 * hash11(i), p.get("sat", 0.2), v)
        set_pixel(i, r, g, b)

def engine_candle_flicker(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    hue = p["hue"]
    for i in range(n):
        noise = hash21(i, int(t * p.get("rate", 18))) - 0.5
        slow = wave01(i * 0.09 + t * 0.7)
        v = max(0.02, min(1.0, p.get("base", 0.42) + 0.22 * slow + p.get("noise", 0.25) * noise))
        r, g, b = hsv_to_rgb(hue + 0.015 * slow, p.get("sat", 0.82), v)
        set_pixel(i, r, g, b)

def engine_gradient_flow(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    for i in range(n):
        x = (i / max(1, n - 1) + t * p.get("speed", 0.04)) % 1.0
        h = hue_mix(p["h1"], p["h2"], x)
        pulse = 0.55 + 0.45 * wave01(i * p.get("wave", 0.05) + t * 1.0)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.75), pulse)
        set_pixel(i, r, g, b)

def engine_theatre_chase(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    phase = int(t * p.get("speed", 12)) % p.get("period", 5)
    for i in range(n):
        on = ((i + phase) % p.get("period", 5)) == 0
        v = 1.0 if on else p.get("back", 0.02)
        h = p["hue"] + p.get("hue_step", 0.0) * i
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.95), v)
        set_pixel(i, r, g, b)

def engine_equalizer(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    bands = max(4, int(p.get("bands", 16)))
    per = max(1, n // bands)
    for i in range(n):
        band = min(bands - 1, i // per)
        level = wave01(t * p.get("speed", 3.0) + band * 0.9 + math.sin(t * 0.4 + band))
        local = (i % per) / max(1, per - 1)
        v = 1.0 if local <= level else 0.04
        h = p["hue"] + band * p.get("hue_step", 0.018)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.95), v)
        set_pixel(i, r, g, b)

def engine_bouncing_orbs(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    clear(False)
    count = int(p.get("count", 4))
    width = p.get("width", 11)
    for k in range(count):
        speed = p.get("speed", 0.8) * (0.7 + 0.17 * k)
        pos = (0.5 + 0.5 * math.sin(t * speed + k * 1.7)) * (n - 1)
        hue = p["hue"] + k * p.get("hue_step", 0.08)
        for i in range(n):
            d = abs(i - pos)
            v = max(0.0, 1.0 - d / width) ** 1.8
            if v > 0:
                r, g, b = hsv_to_rgb(hue, p.get("sat", 0.86), v)
                add_pixel(i, r, g, b)

def engine_confetti(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    fade_all(p.get("fade", 0.72))
    drops = int(p.get("drops", 3))
    for _ in range(drops):
        if random.random() < p.get("chance", 0.8):
            i = random.randrange(n)
            h = (p["hue"] + random.random() * p.get("spread", 0.35)) % 1.0
            r, g, b = hsv_to_rgb(h, p.get("sat", 0.9), 1.0)
            set_pixel(i, r, g, b)

def engine_sparkle_wave(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    for i in range(n):
        wave = wave01(i * p.get("scale", 0.08) + t * p.get("speed", 1.3))
        sparkle = 1.0 if random.random() < p.get("sparkle", 0.012) * wave else 0.0
        v = 0.08 + 0.55 * wave + 0.65 * sparkle
        r, g, b = hsv_to_rgb(p["hue"] + 0.04 * wave, p.get("sat", 0.68), min(1.0, v))
        set_pixel(i, r, g, b)

def engine_fireflies(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    mem = anim.state["spark"]
    for i in range(n):
        mem[i] *= p.get("fade", 0.94)
        if random.random() < p.get("spawn", 0.012):
            mem[i] = 1.0
        v = mem[i] * (0.75 + 0.25 * wave01(t * 1.5 + i * 0.2))
        r, g, b = hsv_to_rgb(p["hue"] + 0.05 * hash11(i), p.get("sat", 0.65), v)
        set_pixel(i, r, g, b)

def engine_mirror_wave(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    center = (n - 1) / 2
    for i in range(n):
        d = abs(i - center)
        v = wave01(d * p.get("scale", 0.08) - t * p.get("speed", 2.8))
        fade = max(0.0, 1.0 - d / center) if center else 1
        r, g, b = hsv_to_rgb(p["hue"] + d * p.get("hue_step", 0.001), p.get("sat", 0.8), v * (0.35 + 0.65 * fade))
        set_pixel(i, r, g, b)

def engine_center_burst(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    center = (n - 1) / 2
    radius = (t * p.get("speed", 22.0)) % max(1, center)
    width = p.get("width", 9)
    for i in range(n):
        d = abs(abs(i - center) - radius)
        v = max(0.0, 1.0 - d / width) ** 1.7
        r, g, b = hsv_to_rgb(p["hue"] + radius * p.get("hue_step", 0.004), p.get("sat", 0.9), v)
        set_pixel(i, r, g, b)

def engine_breathing_gradient(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    breath = 0.15 + 0.85 * smoothstep(wave01(t * p.get("speed", 0.8)))
    for i in range(n):
        x = i / max(1, n - 1)
        h = hue_mix(p["h1"], p["h2"], x)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.7), breath * (0.7 + 0.3 * wave01(i * 0.04 + t)))
        set_pixel(i, r, g, b)

def engine_segment_rotate(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    seg = max(1, int(p.get("segment", 12)))
    offset = int(t * p.get("speed", 10))
    for i in range(n):
        s = ((i + offset) // seg) % 4
        h = (p["hue"] + s * p.get("hue_step", 0.08)) % 1.0
        v = 0.25 + 0.75 * (1.0 if s % 2 == 0 else 0.55)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.8), v)
        set_pixel(i, r, g, b)

def engine_glitter_comet(anim: Any, t: float, p: Dict[str, Any]) -> None:
    engine_comet_tail(anim, t, p)
    n = anim.state["n"]
    for _ in range(int(p.get("glitters", 3))):
        if random.random() < p.get("chance", 0.45):
            i = random.randrange(n)
            r, g, b = hsv_to_rgb(p["hue"] + random.random() * 0.07, 0.3, 1.0)
            add_pixel(i, r, g, b)

def engine_soft_rainbow_noise(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    seed = anim.state["seed"]
    for i in range(n):
        x = i / max(1, n)
        noise = hash21(i, int(t * p.get("rate", 8)) + seed)
        h = (x * p.get("cycles", 1.0) + t * p.get("speed", 0.05) + 0.08 * noise) % 1.0
        v = 0.25 + 0.75 * wave01(i * 0.05 + t * 0.7)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.85), v)
        set_pixel(i, r, g, b)

def engine_subtle_restaurant(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    for i in range(n):
        slow = wave01(t * p.get("speed", 0.35) + i * 0.018)
        candle = 0.035 * (hash21(i, int(t * 8)) - 0.5)
        v = max(0.03, min(1.0, p.get("base", 0.34) + 0.18 * slow + candle))
        r, g, b = hsv_to_rgb(p["hue"] + 0.012 * slow, p.get("sat", 0.45), v)
        set_pixel(i, r, g, b)

def engine_luxury_marquee(anim: Any, t: float, p: Dict[str, Any]) -> None:
    n = anim.state["n"]
    offset = int(t * p.get("speed", 8))
    period = int(p.get("period", 18))
    for i in range(n):
        slot = (i + offset) % period
        v = 1.0 if slot < p.get("on", 3) else p.get("back", 0.09)
        h = p["hue"] + 0.02 * math.sin(i * 0.03 + t)
        r, g, b = hsv_to_rgb(h, p.get("sat", 0.38), v)
        set_pixel(i, r, g, b)

ENGINE_VARIANTS: List[tuple[str, Callable[[Any, float, Dict[str, Any]], None], List[tuple[str, Dict[str, Any]]]]] = [
    ('lux_aurora', engine_lux_aurora, [('champagne', {'h1': 0.09, 'h2': 0.14, 'speed': 0.45, 'scale': 0.032, 'sat': 0.42}),
 ('emerald', {'h1': 0.34, 'h2': 0.48, 'speed': 0.55, 'scale': 0.034, 'sat': 0.62}),
 ('royal', {'h1': 0.73, 'h2': 0.9, 'speed': 0.5, 'scale': 0.03, 'sat': 0.68}),
 ('ocean', {'h1': 0.52, 'h2': 0.62, 'speed': 0.62, 'scale': 0.038, 'sat': 0.72})]),
    ('champagne_twinkle', engine_champagne_twinkle, [('soft_gold', {'hue': 0.1, 'density': 0.035, 'fade': 0.93, 'sat': 0.32, 'base': 0.025}),
 ('ice', {'hue': 0.55, 'density': 0.05, 'fade': 0.91, 'sat': 0.28, 'base': 0.02}),
 ('rose', {'hue': 0.96, 'density': 0.045, 'fade': 0.92, 'sat': 0.38, 'base': 0.022}),
 ('pearl', {'hue': 0.08, 'density': 0.03, 'fade': 0.94, 'sat': 0.12, 'base': 0.035})]),
    ('comet_tail', engine_comet_tail, [('gold', {'hue': 0.11, 'speed': 15, 'tail': 20, 'sat': 0.8}),
 ('blue', {'hue': 0.6, 'speed': 17, 'tail': 18, 'sat': 0.95}),
 ('magenta', {'hue': 0.86, 'speed': 16, 'tail': 19, 'sat': 0.9}),
 ('white', {'hue': 0.12, 'speed': 13, 'tail': 26, 'sat': 0.08})]),
    ('meteor_shower', engine_meteor_shower, [('cyan', {'hue': 0.52, 'speed': 26, 'spawn': 0.2, 'decay': 0.84}),
 ('ember', {'hue': 0.04, 'speed': 20, 'spawn': 0.16, 'decay': 0.86}),
 ('violet', {'hue': 0.78, 'speed': 24, 'spawn': 0.18, 'decay': 0.85}),
 ('gold', {'hue': 0.11, 'speed': 18, 'spawn': 0.13, 'decay': 0.88, 'sat': 0.7})]),
    ('lava_lamp', engine_lava_lamp, [('molten_gold', {'hue': 0.06, 'sat': 0.92, 'speed1': 0.55, 'speed2': 0.32}),
 ('plum', {'hue': 0.82, 'sat': 0.78, 'speed1': 0.45, 'speed2': 0.37}),
 ('deep_sea', {'hue': 0.56, 'sat': 0.84, 'speed1': 0.42, 'speed2': 0.28}),
 ('emerald', {'hue': 0.36, 'sat': 0.82, 'speed1': 0.5, 'speed2': 0.34})]),
    ('ocean_swell', engine_ocean_swell, [('deep', {'hue': 0.57, 'speed': 0.9, 'scale': 0.052, 'foam': 0.1}),
 ('turquoise', {'hue': 0.5, 'speed': 1.1, 'scale': 0.062, 'foam': 0.18}),
 ('storm', {'hue': 0.62, 'speed': 1.45, 'scale': 0.075, 'foam': 0.28}),
 ('lagoon', {'hue': 0.45, 'speed': 0.7, 'scale': 0.045, 'foam': 0.12, 'sat': 0.64})]),
    ('ripple_center', engine_ripple_center, [('blue', {'hue': 0.6, 'speed': 5.0, 'freq': 0.72, 'decay': 0.75}),
 ('gold', {'hue': 0.11, 'speed': 4.2, 'freq': 0.6, 'decay': 0.7, 'sat': 0.72}),
 ('purple', {'hue': 0.78, 'speed': 5.5, 'freq': 0.8, 'decay': 0.72}),
 ('frost', {'hue': 0.53, 'speed': 6.0, 'freq': 0.9, 'decay': 0.82, 'sat': 0.45})]),
    ('nebula_cloud', engine_nebula_cloud, [('pink_blue', {'h1': 0.9, 'h2': 0.58, 'h3': 0.76, 'speed': 0.5}),
 ('green_gold', {'h1': 0.34, 'h2': 0.12, 'h3': 0.22, 'speed': 0.42}),
 ('ultraviolet', {'h1': 0.73, 'h2': 0.86, 'h3': 0.66, 'speed': 0.55}),
 ('amber_smoke', {'h1': 0.06, 'h2': 0.11, 'h3': 0.02, 'speed': 0.34, 'sat': 0.55})]),
    ('starfield', engine_starfield, [('silver', {'hue': 0.08, 'sat': 0.05, 'sparkle': 0.011, 'base': 0.025}),
 ('blue', {'hue': 0.6, 'sat': 0.28, 'sparkle': 0.014, 'base': 0.02}),
 ('gold', {'hue': 0.11, 'sat': 0.45, 'sparkle': 0.012, 'base': 0.02}),
 ('violet', {'hue': 0.78, 'sat': 0.4, 'sparkle': 0.013, 'base': 0.018})]),
    ('candle_flicker', engine_candle_flicker, [('warm', {'hue': 0.07, 'base': 0.42, 'noise': 0.2, 'rate': 14}),
 ('restaurant', {'hue': 0.09, 'base': 0.32, 'noise': 0.12, 'rate': 10, 'sat': 0.55}),
 ('ember', {'hue': 0.035, 'base': 0.36, 'noise': 0.26, 'rate': 18}),
 ('champagne', {'hue': 0.105, 'base': 0.4, 'noise': 0.1, 'rate': 9, 'sat': 0.38})]),
    ('gradient_flow', engine_gradient_flow, [('sunset', {'h1': 0.01, 'h2': 0.12, 'speed': 0.035}),
 ('forest', {'h1': 0.27, 'h2': 0.42, 'speed': 0.03}),
 ('ice', {'h1': 0.52, 'h2': 0.64, 'speed': 0.025, 'sat': 0.42}),
 ('royal', {'h1': 0.74, 'h2': 0.11, 'speed': 0.022, 'sat': 0.68})]),
    ('theatre_chase', engine_theatre_chase, [('amber', {'hue': 0.1, 'speed': 9, 'period': 5, 'back': 0.025}),
 ('red', {'hue': 0.0, 'speed': 13, 'period': 4, 'back': 0.015}),
 ('blue', {'hue': 0.6, 'speed': 12, 'period': 6, 'back': 0.02}),
 ('rainbow', {'hue': 0.0, 'speed': 10, 'period': 5, 'hue_step': 0.004, 'back': 0.012})]),
    ('equalizer', engine_equalizer, [('bar_gold', {'hue': 0.11, 'bands': 18, 'speed': 2.2, 'hue_step': 0.006}),
 ('club_prism', {'hue': 0.0, 'bands': 24, 'speed': 4.0, 'hue_step': 0.025}),
 ('cyan', {'hue': 0.52, 'bands': 14, 'speed': 3.0, 'hue_step': 0.004}),
 ('purple', {'hue': 0.77, 'bands': 16, 'speed': 2.6, 'hue_step': 0.01})]),
    ('bouncing_orbs', engine_bouncing_orbs, [('gold', {'hue': 0.11, 'count': 3, 'speed': 0.75, 'width': 13}),
 ('ocean', {'hue': 0.54, 'count': 5, 'speed': 0.85, 'width': 11, 'hue_step': 0.035}),
 ('violet', {'hue': 0.76, 'count': 4, 'speed': 0.7, 'width': 14}),
 ('party', {'hue': 0.0, 'count': 6, 'speed': 1.0, 'width': 9, 'hue_step': 0.12})]),
    ('confetti', engine_confetti, [('celebrate', {'hue': 0.0, 'spread': 1.0, 'drops': 4, 'fade': 0.76, 'chance': 0.9}),
 ('gold', {'hue': 0.08, 'spread': 0.08, 'drops': 3, 'fade': 0.8, 'sat': 0.68}),
 ('ice', {'hue': 0.52, 'spread': 0.12, 'drops': 3, 'fade': 0.78, 'sat': 0.5}),
 ('rose', {'hue': 0.92, 'spread': 0.16, 'drops': 3, 'fade': 0.79, 'sat': 0.7})]),
    ('sparkle_wave', engine_sparkle_wave, [('champagne', {'hue': 0.1, 'speed': 1.0, 'scale': 0.055, 'sparkle': 0.01, 'sat': 0.42}),
 ('aqua', {'hue': 0.5, 'speed': 1.3, 'scale': 0.065, 'sparkle': 0.014}),
 ('violet', {'hue': 0.77, 'speed': 1.2, 'scale': 0.06, 'sparkle': 0.012}),
 ('ruby', {'hue': 0.98, 'speed': 1.5, 'scale': 0.075, 'sparkle': 0.016})]),
    ('fireflies', engine_fireflies, [('garden', {'hue': 0.18, 'spawn': 0.012, 'fade': 0.95, 'sat': 0.52}),
 ('gold', {'hue': 0.1, 'spawn': 0.014, 'fade': 0.94, 'sat': 0.42}),
 ('blue', {'hue': 0.57, 'spawn': 0.01, 'fade': 0.955, 'sat': 0.48}),
 ('pink', {'hue': 0.92, 'spawn': 0.012, 'fade': 0.945, 'sat': 0.55})]),
    ('mirror_wave', engine_mirror_wave, [('blue', {'hue': 0.6, 'speed': 2.6, 'scale': 0.075}),
 ('gold', {'hue': 0.11, 'speed': 2.1, 'scale': 0.065, 'sat': 0.62}),
 ('purple', {'hue': 0.78, 'speed': 2.8, 'scale': 0.085}),
 ('green', {'hue': 0.34, 'speed': 2.2, 'scale': 0.07})]),
    ('center_burst', engine_center_burst, [('gold', {'hue': 0.1, 'speed': 18, 'width': 10}),
 ('cyan', {'hue': 0.52, 'speed': 22, 'width': 8}),
 ('violet', {'hue': 0.78, 'speed': 20, 'width': 9}),
 ('rainbow', {'hue': 0.0, 'speed': 16, 'width': 11, 'hue_step': 0.008})]),
    ('breathing_gradient', engine_breathing_gradient, [('rose_gold', {'h1': 0.96, 'h2': 0.1, 'speed': 0.6, 'sat': 0.54}),
 ('ocean', {'h1': 0.5, 'h2': 0.62, 'speed': 0.55, 'sat': 0.68}),
 ('forest', {'h1': 0.27, 'h2': 0.39, 'speed': 0.5, 'sat': 0.58}),
 ('royal', {'h1': 0.73, 'h2': 0.86, 'speed': 0.48, 'sat': 0.66})]),
    ('segment_rotate', engine_segment_rotate, [('gold_plum', {'hue': 0.1, 'segment': 14, 'speed': 7, 'hue_step': 0.16, 'sat': 0.62}),
 ('ocean', {'hue': 0.5, 'segment': 12, 'speed': 8, 'hue_step': 0.04}),
 ('candy', {'hue': 0.93, 'segment': 10, 'speed': 10, 'hue_step': 0.12}),
 ('forest', {'hue': 0.28, 'segment': 16, 'speed': 6, 'hue_step': 0.05})]),
    ('glitter_comet', engine_glitter_comet, [('champagne', {'hue': 0.1, 'speed': 12, 'tail': 22, 'sat': 0.48, 'glitters': 3}),
 ('ice', {'hue': 0.55, 'speed': 13, 'tail': 20, 'sat': 0.32, 'glitters': 4}),
 ('ruby', {'hue': 0.98, 'speed': 14, 'tail': 18, 'sat': 0.85, 'glitters': 3}),
 ('emerald', {'hue': 0.36, 'speed': 12, 'tail': 19, 'sat': 0.7, 'glitters': 3})]),
    ('soft_rainbow_noise', engine_soft_rainbow_noise, [('pastel', {'cycles': 1.0, 'speed': 0.03, 'sat': 0.42, 'rate': 5}),
 ('vivid', {'cycles': 1.4, 'speed': 0.05, 'sat': 0.85, 'rate': 8}),
 ('slow', {'cycles': 0.8, 'speed': 0.018, 'sat': 0.62, 'rate': 4}),
 ('party', {'cycles': 2.0, 'speed': 0.075, 'sat': 0.95, 'rate': 10})]),
    ('subtle_restaurant', engine_subtle_restaurant, [('warm_table', {'hue': 0.08, 'base': 0.3, 'speed': 0.28, 'sat': 0.42}),
 ('champagne_bar', {'hue': 0.105, 'base': 0.34, 'speed': 0.25, 'sat': 0.36}),
 ('soft_rose', {'hue': 0.96, 'base': 0.28, 'speed': 0.3, 'sat': 0.34}),
 ('lounge_blue', {'hue': 0.58, 'base': 0.24, 'speed': 0.22, 'sat': 0.3})]),
    ('luxury_marquee', engine_luxury_marquee, [('gold', {'hue': 0.1, 'speed': 6, 'period': 20, 'on': 3, 'back': 0.08}),
 ('pearl', {'hue': 0.08, 'speed': 5, 'period': 24, 'on': 4, 'back': 0.1, 'sat': 0.18}),
 ('ruby', {'hue': 0.98, 'speed': 7, 'period': 18, 'on': 3, 'back': 0.06}),
 ('emerald', {'hue': 0.36, 'speed': 6, 'period': 22, 'on': 3, 'back': 0.07})]),
]


def _make_animation(engine: Callable[[Any, float, Dict[str, Any]], None], params: Dict[str, Any]) -> Callable[[Any, float], None]:
    def _animation(animator: Any, elapsed: float) -> None:
        engine(animator, elapsed, params)
    return _animation


ANIMATIONS: Dict[str, Callable[[Any, float], None]] = {}
ANIMATION_META: Dict[str, Dict[str, Any]] = {}

for engine_name, engine_func, variants in ENGINE_VARIANTS:
    for variant_name, params in variants:
        key = f"hospitality_{engine_name}_{variant_name}"
        ANIMATIONS[key] = _make_animation(engine_func, params.copy())
        ANIMATION_META[key] = {
            "key": key,
            "collection": "hospitality-100",
            "engine": engine_name,
            "variant": variant_name,
            "params": params.copy(),
        }

ANIMATION_NAMES = list(ANIMATIONS.keys())


def validate_library(expected_count: int = 100, expected_engines: int = 25) -> None:
    if len(ENGINE_VARIANTS) != expected_engines:
        raise RuntimeError(f"Expected {expected_engines} engines, got {len(ENGINE_VARIANTS)}")
    if len(ANIMATIONS) != expected_count:
        raise RuntimeError(f"Expected {expected_count} animations, got {len(ANIMATIONS)}")
    if len(set(ANIMATION_NAMES)) != len(ANIMATION_NAMES):
        raise RuntimeError("Duplicate Hospitality 100 animation keys detected")


validate_library()
