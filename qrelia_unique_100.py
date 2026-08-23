
import time
import math
import random
import qrelia_hospitality_100 as hospitality
from rpi_ws281x import PixelStrip, Color

# ============================================================
# QRelia Ultimate Unique 100 Animation Library
# WS2815 / WS2812 compatible via rpi_ws281x
# Run with sudo on Raspberry Pi
# ============================================================

LED_COUNT = 144
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_BRIGHTNESS = 50
LED_INVERT = False
LED_CHANNEL = 0
FPS = 60
FRAME_DELAY = 1.0 / FPS

#strip = PixelStrip(
#    LED_COUNT,
#    LED_PIN,
#    LED_FREQ_HZ,
#    LED_DMA,
#   LED_INVERT,
#    LED_BRIGHTNESS,
#    LED_CHANNEL
#)
#strip.begin()

strip = None

def init_strip(s):
    global strip
    strip = s
    hospitality.init_strip(s)

# -------------------------- helpers --------------------------

def clamp(x, lo=0, hi=255):
    return max(lo, min(hi, int(x)))

def rgb(r, g, b):
    return Color(clamp(r), clamp(g), clamp(b))

def set_pixel(i, r, g, b):
    if 0 <= i < strip.numPixels():
        strip.setPixelColor(i, rgb(r, g, b))

def show() :
    strip.show()

def clear(show_now=True):
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, 0)
    if show_now:
        strip.show()

def fill(r, g, b, show_now=True):
    c = rgb(r, g, b)
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, c)
    if show_now:
        strip.show()

def get_rgb_tuple(i):
    c = strip.getPixelColor(i)
    return (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF

def fade_all(factor):
    for i in range(strip.numPixels()):
        r, g, b = get_rgb_tuple(i)
        set_pixel(i, r * factor, g * factor, b * factor)

def wheel(pos):
    pos = pos % 256
    if pos < 85:
        return clamp(pos * 3), clamp(255 - pos * 3), 0
    if pos < 170:
        pos -= 85
        return clamp(255 - pos * 3), 0, clamp(pos * 3)
    pos -= 170
    return 0, clamp(pos * 3), clamp(255 - pos * 3)

def hsv_to_rgb(h, s, v):
    h = h % 1.0
    s = max(0.0, min(1.0, s))
    v = max(0.0, min(1.0, v))
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i %= 6
    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q
    return clamp(r * 255), clamp(g * 255), clamp(b * 255)

def lerp(a, b, t):
    return a + (b - a) * t

def smoothstep(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)

def frac(x):
    return x - math.floor(x)

# cheap deterministic pseudo-randomness from coordinate/time
def hash11(x):
    return frac(math.sin(x * 127.1) * 43758.5453123)

def hash21(x, y):
    return frac(math.sin(x * 127.1 + y * 311.7) * 43758.5453123)

class LedAnimator:
    def __init__(self):
        n = strip.numPixels()
        self.current = "premium_velvet_breath"
        self.started_at = time.time()
        self.state = {
            "n": n,
            "twinkle": [0.0] * n,
            "sparkle": [0.0] * n,
            "spark": [0.0] * n,
            "rain": [0.0] * n,
            "embers": [0.0] * n,
            "heat": [0.0] * n,
            "drips": [],
            "meteors": [],
            "orbs": [],
            "foam": [0.0] * n,
            "memory_a": [0.0] * n,
            "memory_b": [0.0] * n,
            "memory_c": [0.0] * n,
            "global": 0.0,
            "flash": 0.0,
            "seed": random.random() * 1000.0,
        }

    def set_animation(self, name):
        if name not in ANIMATIONS:
            raise ValueError(f"Unknown animation: {name}")
        self.current = name
        self.started_at = time.time()

    def step(self):
        t = time.time() - self.started_at
        ANIMATIONS[self.current](self, t)

# ---------------------- shared engines -----------------------

def _paint_palette_wave(anim, t, palette, speed=1.0, scale=0.18, sat=1.0, bias=0.0, shimmer=0.0):
    n = anim.state["n"]
    for i in range(n):
        x = i * scale
        v = 0.5 + 0.5 * math.sin(x + t * speed + bias * math.sin(t * 0.37 + i * 0.05))
        v = smoothstep(v)
        if shimmer:
            v *= 0.75 + 0.25 * math.sin(t * shimmer + i * 0.27)
        h = (palette + 0.13 * math.sin(i * 0.03 + t * 0.11) + 0.09 * v) % 1.0
        r, g, b = hsv_to_rgb(h, sat, v)
        set_pixel(i, r, g, b)


def _paint_dual_wave(anim, t, h1, h2, speed1, speed2, scale1, scale2):
    n = anim.state["n"]
    for i in range(n):
        a = 0.5 + 0.5 * math.sin(i * scale1 + t * speed1)
        b = 0.5 + 0.5 * math.sin(i * scale2 - t * speed2 + math.sin(t * 0.7))
        mix = 0.5 + 0.5 * math.sin(i * 0.07 + t * 0.3)
        h = lerp(h1, h2, mix)
        v = max(0.0, min(1.0, 0.55 * a + 0.45 * b))
        r, g, c = hsv_to_rgb(h, 1.0, v)
        set_pixel(i, r, g, c)


def _paint_comet_ring(anim, t, hue=0.0, speed=14.0, tail=10.0, softness=1.8, satellites=0, sat_hue_shift=0.15):
    n = anim.state["n"]
    pos = (t * speed) % n
    for i in range(n):
        d = min((i - pos) % n, (pos - i) % n)
        v = max(0.0, 1.0 - (d / tail) ** softness)
        h = (hue + 0.02 * i / n) % 1.0
        r, g, b = hsv_to_rgb(h, 1.0, v)
        set_pixel(i, r, g, b)
    for s in range(satellites):
        offset = (s + 1) * n / (satellites + 1)
        p2 = (pos + offset) % n
        for i in range(n):
            d = min((i - p2) % n, (p2 - i) % n)
            v = max(0.0, 0.75 - (d / (tail * 0.7)) ** 1.4)
            if v > 0:
                r, g, b = hsv_to_rgb((hue + sat_hue_shift * (s + 1)) % 1.0, 1.0, v)
                r0, g0, b0 = get_rgb_tuple(i)
                set_pixel(i, max(r0, r), max(g0, g), max(b0, b))


def _paint_rain(anim, t, hue=0.58, speed=18.0, spawn=0.12, blur=0.82, sparkle=False):
    n = anim.state["n"]
    mem = anim.state["memory_a"]
    for i in range(n):
        mem[i] *= blur
    if random.random() < spawn:
        mem[random.randint(0, n - 1)] = 1.0
    shift = int(t * speed) % max(1, n)
    tmp = mem[:]
    for i in range(n):
        src = (i - shift) % n
        v = tmp[src]
        if sparkle and random.random() < 0.03 and v > 0.2:
            v = 1.0
        r, g, b = hsv_to_rgb((hue + 0.03 * math.sin(i * 0.1 + t)) % 1.0, 0.9, v)
        set_pixel(i, r, g, b)


def _paint_twinkle_field(anim, t, hue=0.1, density=0.05, fade=0.92, sat=0.4, base=0.02):
    n = anim.state["n"]
    mem = anim.state["twinkle"]
    for i in range(n):
        mem[i] *= fade
        if random.random() < density * (0.5 + 0.5 * hash21(i, int(t * 10))):
            mem[i] = 1.0
        v = min(1.0, base + mem[i])
        r, g, b = hsv_to_rgb((hue + 0.08 * hash11(i)) % 1.0, sat, v)
        set_pixel(i, r, g, b)


def _paint_fireline(anim, t, base_hue=0.06, turbulence=1.0, cooling=0.91, spark_rate=0.12, white_hot=False):
    n = anim.state["n"]
    heat = anim.state["heat"]
    for i in range(n):
        left = heat[i - 1] if i > 0 else heat[i]
        right = heat[i + 1] if i < n - 1 else heat[i]
        heat[i] = (heat[i] + left + right) / 3.0 * cooling
        heat[i] += 0.12 * (0.5 + 0.5 * math.sin(i * 0.21 + t * 4.0 * turbulence))
    if random.random() < spark_rate:
        idx = random.randint(0, n - 1)
        heat[idx] = 1.0
    for i in range(n):
        v = max(0.0, min(1.0, heat[i]))
        sat = 1.0
        hue = base_hue - 0.045 * v
        r, g, b = hsv_to_rgb(hue, sat, v)
        if white_hot and v > 0.82:
            extra = (v - 0.82) / 0.18
            r = 255
            g = clamp(190 + 65 * extra)
            b = clamp(110 + 145 * extra)
        set_pixel(i, r, g, b)


def _paint_ripple(anim, t, hue=0.6, speed=5.0, freq=0.8, center_speed=0.7, decay=0.7):
    n = anim.state["n"]
    center = (0.5 + 0.5 * math.sin(t * center_speed)) * (n - 1)
    for i in range(n):
        d = abs(i - center)
        wave = 0.5 + 0.5 * math.sin(d * freq - t * speed)
        fade = max(0.0, 1.0 - d / (n * decay))
        v = wave * fade
        r, g, b = hsv_to_rgb(hue + 0.02 * fade, 0.95, v)
        set_pixel(i, r, g, b)


def _paint_plasma(anim, t, palette=0.7, contrast=1.0, speed=1.0):
    n = anim.state["n"]
    for i in range(n):
        x = i / max(1, n - 1)
        p = (
            math.sin((x * 8.0 + t * 0.9 * speed)) +
            math.sin((x * 13.0 - t * 1.3 * speed)) +
            math.sin((x * 21.0 + math.sin(t * 0.7) * 2.0))
        ) / 3.0
        p = 0.5 + 0.5 * p
        p = min(1.0, max(0.0, (p - 0.5) * contrast + 0.5))
        r, g, b = hsv_to_rgb((palette + 0.35 * p + 0.04 * math.sin(t + x * 10)) % 1.0, 1.0, p)
        set_pixel(i, r, g, b)


def premium_velvet_breath(anim, t):
    n = anim.state["n"]
    for i in range(n):
        v = 0.18 + 0.82 * (0.5 + 0.5 * math.sin(t * 0.55 + i * 0.04))
        h = 0.03 + 0.01 * math.sin(t * 0.17 + i * 0.03)
        r, g, b = hsv_to_rgb(h, 0.78, v)
        set_pixel(i, r, g, b)



def champagne_shimmer(anim, t):
    _paint_twinkle_field(anim, t, hue=0.11, density=0.08, fade=0.9, sat=0.28, base=0.05)


def aurora_royal(anim, t):
    _paint_dual_wave(anim, t, 0.74, 0.38, 0.9, 0.5, 0.17, 0.08)


def aurora_emerald(anim, t):
    _paint_dual_wave(anim, t, 0.36, 0.55, 0.7, 0.4, 0.14, 0.06)


def midnight_cyan_bloom(anim, t):
    _paint_palette_wave(anim, t, palette=0.54, speed=0.8, scale=0.13, sat=0.9, bias=0.6, shimmer=1.5)


def violet_silk_current(anim, t):
    _paint_palette_wave(anim, t, palette=0.78, speed=0.6, scale=0.11, sat=0.82, bias=0.9, shimmer=1.0)


def amber_halo(anim, t):
    _paint_palette_wave(anim, t, palette=0.08, speed=0.45, scale=0.10, sat=0.95, bias=0.4, shimmer=0.8)


def rose_gold_current(anim, t):
    _paint_dual_wave(anim, t, 0.98, 0.06, 0.5, 0.7, 0.09, 0.19)


def pearl_drift(anim, t):
    n = anim.state["n"]
    for i in range(n):
        v = 0.35 + 0.55 * (0.5 + 0.5 * math.sin(t * 0.4 + i * 0.05 + 0.5 * math.sin(i * 0.13 - t)))
        h = 0.08 + 0.02 * math.sin(i * 0.02 + t * 0.1)
        r, g, b = hsv_to_rgb(h, 0.12, v)
        set_pixel(i, r, g, b)



def sunset_glass(anim, t):
    n = anim.state["n"]
    for i in range(n):
        x = i / max(1, n - 1)
        h = 0.02 + 0.08 * x + 0.02 * math.sin(t * 0.25 + x * 8)
        v = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(i * 0.08 + t * 0.5))
        r, g, b = hsv_to_rgb(h, 0.85, v)
        set_pixel(i, r, g, b)



def molten_lux(anim, t):
    _paint_fireline(anim, t, base_hue=0.07, turbulence=0.7, cooling=0.95, spark_rate=0.08, white_hot=True)


def golden_orbit(anim, t):
    _paint_comet_ring(anim, t, hue=0.12, speed=10.0, tail=14.0, softness=1.6, satellites=1, sat_hue_shift=0.03)


def sapphire_orbit(anim, t):
    _paint_comet_ring(anim, t, hue=0.61, speed=11.5, tail=12.0, softness=1.5, satellites=1, sat_hue_shift=0.04)


def ruby_orbit(anim, t):
    _paint_comet_ring(anim, t, hue=0.99, speed=12.5, tail=11.0, softness=1.7, satellites=0)


def triple_crown_orbit(anim, t):
    _paint_comet_ring(anim, t, hue=0.10, speed=9.0, tail=10.0, softness=1.4, satellites=2, sat_hue_shift=0.2)


def frosted_mint(anim, t):
    _paint_palette_wave(anim, t, palette=0.42, speed=0.9, scale=0.16, sat=0.45, bias=0.7, shimmer=1.8)


def ocean_cathedral(anim, t):
    _paint_plasma(anim, t, palette=0.56, contrast=1.3, speed=0.7)


def electric_monsoon(anim, t):
    _paint_rain(anim, t, hue=0.60, speed=25.0, spawn=0.22, blur=0.85, sparkle=True)


def cyan_monsoon(anim, t):
    _paint_rain(anim, t, hue=0.51, speed=22.0, spawn=0.18, blur=0.84, sparkle=False)


def violet_rain(anim, t):
    _paint_rain(anim, t, hue=0.76, speed=19.0, spawn=0.16, blur=0.87, sparkle=True)


def gold_drizzle(anim, t):
    _paint_rain(anim, t, hue=0.12, speed=14.0, spawn=0.10, blur=0.89, sparkle=True)


def ember_rain(anim, t):
    _paint_rain(anim, t, hue=0.03, speed=17.0, spawn=0.12, blur=0.86, sparkle=True)


def scarlet_chase(anim, t):
    n = anim.state["n"]
    phase = int(t * 13) % 4
    for i in range(n):
        on = ((i + phase) % 4 == 0)
        v = 1.0 if on else 0.0
        set_pixel(i, 255 * v, 0, 0)



def azure_chase(anim, t):
    n = anim.state["n"]
    phase = int(t * 15) % 5
    for i in range(n):
        on = ((i + phase) % 5 == 0)
        v = 1.0 if on else 0.0
        set_pixel(i, 0, 110 * v, 255 * v)



def gold_chase(anim, t):
    n = anim.state["n"]
    phase = int(t * 10) % 6
    for i in range(n):
        on = ((i + phase) % 6 == 0)
        v = 1.0 if on else 0.0
        set_pixel(i, 255 * v, 170 * v, 35 * v)



def prism_chase(anim, t):
    n = anim.state["n"]
    phase = int(t * 12) % 7
    for i in range(n):
        if (i + phase) % 7 == 0:
            r, g, b = wheel(int((i * 256 / n) + t * 140))
            set_pixel(i, r, g, b)
        else:
            set_pixel(i, 0, 0, 0)



def dual_lane_chase(anim, t):
    n = anim.state["n"]
    p = int(t * 12) % n
    for i in range(n):
        v1 = max(0.0, 1.0 - min((p - i) % n, (i - p) % n) / 6.0)
        p2 = (p + n // 2) % n
        v2 = max(0.0, 1.0 - min((p2 - i) % n, (i - p2) % n) / 6.0)
        set_pixel(i, 255 * v1, 0, 255 * v2)



def arrowhead_run(anim, t):
    n = anim.state["n"]
    p = int(t * 18) % n
    clear(False)
    for k in range(6):
        idx = (p - k) % n
        v = 1.0 - k / 6.0
        set_pixel(idx, 255 * v, 140 * v, 40 * v)



def meteor_white(anim, t):
    _paint_comet_ring(anim, t, hue=0.0, speed=9.0, tail=24.0, softness=1.45, satellites=0)


def meteor_teal(anim, t):
    _paint_comet_ring(anim, t, hue=0.50, speed=8.5, tail=22.0, softness=1.42, satellites=0)


def meteor_magenta(anim, t):
    _paint_comet_ring(anim, t, hue=0.85, speed=8.8, tail=23.0, softness=1.44, satellites=0)


def binary_star(anim, t):
    _paint_comet_ring(anim, t, hue=0.13, speed=8.0, tail=9.0, softness=1.5, satellites=1, sat_hue_shift=0.5)


def trinary_star(anim, t):
    _paint_comet_ring(anim, t, hue=0.09, speed=7.5, tail=8.0, softness=1.4, satellites=2, sat_hue_shift=0.33)


def scanner_red(anim, t):
    n = anim.state["n"]
    pos = (0.5 + 0.5 * math.sin(t * 1.05)) * (n - 1)
    for i in range(n):
        d = abs(i - pos)
        v = max(0.0, 1.0 - d / 9.5)
        set_pixel(i, 255 * v, 0, 0)



def scanner_blue(anim, t):
    n = anim.state["n"]
    pos = (0.5 + 0.5 * math.sin(t * 0.95)) * (n - 1)
    for i in range(n):
        d = abs(i - pos)
        v = max(0.0, 1.0 - d / 10.0)
        set_pixel(i, 0, 120 * v, 255 * v)



def scanner_gold(anim, t):
    n = anim.state["n"]
    pos = (0.5 + 0.5 * math.sin(t * 0.82)) * (n - 1)
    for i in range(n):
        d = abs(i - pos)
        v = max(0.0, 1.0 - d / 13.0)
        set_pixel(i, 255 * v, 180 * v, 40 * v)



def scanner_dual(anim, t):
    n = anim.state["n"]
    p1 = (0.5 + 0.5 * math.sin(t * 0.9)) * (n - 1)
    p2 = n - 1 - p1
    for i in range(n):
        d1 = abs(i - p1)
        d2 = abs(i - p2)
        v1 = max(0.0, 1.0 - d1 / 7.0)
        v2 = max(0.0, 1.0 - d2 / 7.0)
        set_pixel(i, 255 * v1, 0, 255 * v2)



def scanner_prism(anim, t):
    n = anim.state["n"]
    pos = (0.5 + 0.5 * math.sin(t * 0.88)) * (n - 1)
    for i in range(n):
        d = abs(i - pos)
        v = max(0.0, 1.0 - d / 9.0)
        r, g, b = wheel(int((i * 256 / n) + t * 90))
        set_pixel(i, r * v, g * v, b * v)



def ripple_blue(anim, t):
    _paint_ripple(anim, t, hue=0.60, speed=5.5, freq=0.9, center_speed=0.9, decay=0.72)


def ripple_gold(anim, t):
    _paint_ripple(anim, t, hue=0.11, speed=4.8, freq=0.85, center_speed=0.7, decay=0.65)


def ripple_purple(anim, t):
    _paint_ripple(anim, t, hue=0.78, speed=6.2, freq=0.95, center_speed=1.0, decay=0.68)


def ripple_frost(anim, t):
    _paint_ripple(anim, t, hue=0.53, speed=7.0, freq=1.05, center_speed=0.6, decay=0.80)


def ripple_prism(anim, t):
    n = anim.state["n"]
    center = (0.5 + 0.5 * math.sin(t * 0.8)) * (n - 1)
    for i in range(n):
        d = abs(i - center)
        wave = 0.5 + 0.5 * math.sin(d * 1.05 - t * 7.5)
        fade = max(0.0, 1.0 - d / (n * 0.68))
        r, g, b = wheel(int((d * 22 - t * 100)))
        set_pixel(i, r * wave * fade, g * wave * fade, b * wave * fade)



def plasma_inferno(anim, t):
    _paint_plasma(anim, t, palette=0.02, contrast=1.45, speed=1.1)


def plasma_ocean(anim, t):
    _paint_plasma(anim, t, palette=0.56, contrast=1.35, speed=0.8)


def plasma_ultraviolet(anim, t):
    _paint_plasma(anim, t, palette=0.78, contrast=1.4, speed=1.0)


def plasma_nebula(anim, t):
    _paint_plasma(anim, t, palette=0.88, contrast=1.2, speed=0.6)


def plasma_jade(anim, t):
    _paint_plasma(anim, t, palette=0.34, contrast=1.25, speed=0.7)


def matrix_emerald(anim, t):
    n = anim.state["n"]
    mem = anim.state["memory_b"]
    for i in range(n):
        mem[i] *= 0.86
    if random.random() < 0.25:
        mem[random.randint(0, n - 1)] = 1.0
    offset = int(t * 20)
    for i in range(n):
        v = mem[(i - offset) % n]
        set_pixel(i, 0, 255 * v, 35 * v)



def matrix_lime(anim, t):
    n = anim.state["n"]
    for i in range(n):
        v = 0.25 + 0.75 * (0.5 + 0.5 * math.sin(t * 7.0 + i * 0.8))
        set_pixel(i, 90 * v, 255 * v, 0)



def matrix_cyan(anim, t):
    n = anim.state["n"]
    for i in range(n):
        v = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(t * 8.0 + i * 1.1))
        set_pixel(i, 0, 240 * v, 255 * v)



def codefall_prism(anim, t):
    n = anim.state["n"]
    mem = anim.state["memory_c"]
    for i in range(n):
        mem[i] *= 0.88
    if random.random() < 0.20:
        mem[random.randint(0, n - 1)] = 1.0
    shift = int(t * 17)
    for i in range(n):
        v = mem[(i - shift) % n]
        r, g, b = wheel(int(i * 256 / max(1, n) + t * 60))
        set_pixel(i, r * v, g * v, b * v)



def arc_welder(anim, t):
    n = anim.state["n"]
    flash = anim.state["flash"] * 0.82
    if random.random() < 0.06:
        flash = 1.0
    anim.state["flash"] = flash
    for i in range(n):
        base = 0.04 + 0.05 * math.sin(t * 0.7 + i * 0.2)
        spark = flash * (0.8 + 0.2 * random.random())
        set_pixel(i, 180 * (base + spark), 220 * (base + spark), 255 * (base + spark))



def fire_classic(anim, t):
    _paint_fireline(anim, t, base_hue=0.06, turbulence=0.9, cooling=0.93, spark_rate=0.10, white_hot=False)


def fire_whitehot(anim, t):
    _paint_fireline(anim, t, base_hue=0.06, turbulence=1.2, cooling=0.92, spark_rate=0.14, white_hot=True)


def fire_slowburn(anim, t):
    _paint_fireline(anim, t, base_hue=0.08, turbulence=0.45, cooling=0.96, spark_rate=0.05, white_hot=False)


def fire_violet_core(anim, t):
    n = anim.state["n"]
    heat = anim.state["heat"]
    for i in range(n):
        left = heat[i - 1] if i > 0 else heat[i]
        right = heat[i + 1] if i < n - 1 else heat[i]
        heat[i] = (heat[i] + left + right) / 3.0 * 0.93
        heat[i] += 0.15 * (0.5 + 0.5 * math.sin(i * 0.29 + t * 5.2))
    if random.random() < 0.10:
        heat[random.randint(0, n - 1)] = 1.0
    for i in range(n):
        v = max(0.0, min(1.0, heat[i]))
        h = 0.82 - 0.12 * v
        r, g, b = hsv_to_rgb(h, 0.95, v)
        set_pixel(i, r, g, b)



def lava_flow(anim, t):
    n = anim.state["n"]
    for i in range(n):
        wave = math.sin(i * 0.18 + t * 1.8) + 0.6 * math.sin(i * 0.49 - t * 1.2)
        v = (wave + 1.6) / 3.2
        set_pixel(i, 255, 45 + 120 * v, 0)



def lava_embers(anim, t):
    n = anim.state["n"]
    mem = anim.state["embers"]
    for i in range(n):
        mem[i] *= 0.94
        mem[i] += 0.02 * (0.5 + 0.5 * math.sin(i * 0.41 + t * 2.0))
        if random.random() < 0.03:
            mem[i] = 1.0
        v = min(1.0, mem[i])
        set_pixel(i, 255 * v, 80 * v, 10 * v)



def lava_crust(anim, t):
    n = anim.state["n"]
    for i in range(n):
        molten = 0.5 + 0.5 * math.sin(i * 0.11 + t * 1.6 + math.sin(i * 0.4 - t * 0.7))
        crust = 0.3 + 0.3 * math.sin(i * 0.9 + t * 0.2)
        set_pixel(i, 180 + 75 * molten, 20 + 90 * molten, 8 * crust)



def ocean_deep(anim, t):
    n = anim.state["n"]
    for i in range(n):
        wave = 0.5 + 0.5 * math.sin(i * 0.17 + t * 1.4 + 0.7 * math.sin(t + i * 0.07))
        set_pixel(i, 0, 40 + 90 * wave, 130 + 125 * wave)



def ocean_surface(anim, t):
    n = anim.state["n"]
    foam = anim.state["foam"]
    for i in range(n):
        wave = 0.5 + 0.5 * math.sin(i * 0.12 + t * 1.8)
        crest = (0.5 + 0.5 * math.sin(i * 0.48 - t * 5.0)) * wave
        foam[i] = foam[i] * 0.86 + crest * 0.3
        set_pixel(i, 20 * wave + 180 * foam[i], 130 * wave + 100 * foam[i], 180 + 75 * wave)



def ocean_turquoise(anim, t):
    n = anim.state["n"]
    for i in range(n):
        wave = 0.5 + 0.5 * math.sin(i * 0.09 + t * 1.1)
        sparkle = 0.5 + 0.5 * math.sin(i * 0.33 - t * 3.7)
        set_pixel(i, 0, 100 + 110 * wave, 100 + 140 * sparkle)



def tidal_pulse(anim, t):
    n = anim.state["n"]
    center = (0.5 + 0.5 * math.sin(t * 0.6)) * (n - 1)
    for i in range(n):
        d = abs(i - center)
        swell = 0.5 + 0.5 * math.sin(t * 4.0 - d * 0.55)
        fade = max(0.0, 1.0 - d / (n * 0.9))
        set_pixel(i, 0, 90 * swell * fade, 255 * swell * fade)



def storm_surge(anim, t):
    n = anim.state["n"]
    flash = anim.state["flash"] * 0.78
    if random.random() < 0.04:
        flash = 1.0
    anim.state["flash"] = flash
    for i in range(n):
        wave = 0.5 + 0.5 * math.sin(i * 0.2 + t * 2.5)
        lightning = flash * (0.75 + 0.25 * random.random())
        set_pixel(i, 20 + 40 * wave + 180 * lightning, 60 + 80 * wave + 150 * lightning, 160 + 95 * wave + 80 * lightning)



def twinkle_white(anim, t):
    _paint_twinkle_field(anim, t, hue=0.0, density=0.06, fade=0.90, sat=0.05, base=0.02)


def twinkle_gold(anim, t):
    _paint_twinkle_field(anim, t, hue=0.11, density=0.05, fade=0.92, sat=0.55, base=0.02)


def twinkle_ice(anim, t):
    _paint_twinkle_field(anim, t, hue=0.54, density=0.07, fade=0.89, sat=0.35, base=0.03)


def twinkle_pink(anim, t):
    _paint_twinkle_field(anim, t, hue=0.95, density=0.06, fade=0.91, sat=0.48, base=0.02)


def twinkle_prism(anim, t):
    n = anim.state["n"]
    mem = anim.state["twinkle"]
    for i in range(n):
        mem[i] *= 0.90
        if random.random() < 0.05:
            mem[i] = 1.0
        r, g, b = wheel(int((i * 256 / n) + t * 35))
        set_pixel(i, r * mem[i], g * mem[i], b * mem[i])



def starfield_silver(anim, t):
    n = anim.state["n"]
    for i in range(n):
        base = 6 + 10 * (0.5 + 0.5 * math.sin(i * 0.27 + t * 0.15))
        sparkle = 220 if random.random() < 0.012 else 0
        v = clamp(base + sparkle)
        set_pixel(i, v, v, v)



def starfield_blue(anim, t):
    n = anim.state["n"]
    for i in range(n):
        base = 6 + 12 * (0.5 + 0.5 * math.sin(i * 0.31 + t * 0.17))
        sparkle = 240 if random.random() < 0.015 else 0
        set_pixel(i, base + sparkle * 0.65, base + sparkle * 0.8, base + sparkle)



def starfield_gold(anim, t):
    n = anim.state["n"]
    for i in range(n):
        base = 4 + 8 * (0.5 + 0.5 * math.sin(i * 0.29 + t * 0.14))
        sparkle = 255 if random.random() < 0.013 else 0
        set_pixel(i, base + sparkle, base + sparkle * 0.7, base * 0.2)



def nebula_pink_blue(anim, t):
    n = anim.state["n"]
    for i in range(n):
        a = 0.5 + 0.5 * math.sin(i * 0.10 + t * 0.9)
        b = 0.5 + 0.5 * math.sin(i * 0.23 - t * 0.6)
        r = 180 * a + 70 * b
        g = 30 * a
        bl = 140 * b + 115 * a
        set_pixel(i, r, g, bl)



def nebula_green_gold(anim, t):
    n = anim.state["n"]
    for i in range(n):
        a = 0.5 + 0.5 * math.sin(i * 0.08 + t * 0.6)
        b = 0.5 + 0.5 * math.sin(i * 0.19 - t * 0.4)
        set_pixel(i, 170 * b, 80 + 140 * a, 30 * a)



def nebula_ultraviolet(anim, t):
    n = anim.state["n"]
    for i in range(n):
        a = 0.5 + 0.5 * math.sin(i * 0.09 + t * 0.7)
        b = 0.5 + 0.5 * math.sin(i * 0.17 + t * 1.1)
        set_pixel(i, 140 * a, 30 * b, 160 + 95 * a)



def heatmap_flow(anim, t):
    n = anim.state["n"]
    for i in range(n):
        v = (math.sin(i * 0.11 + t * 1.4) + math.sin(i * 0.37 - t * 0.9) + 2.0) / 4.0
        if v < 0.33:
            r = 0
            g = (v / 0.33) * 255
            b = 255
        elif v < 0.66:
            r = ((v - 0.33) / 0.33) * 255
            g = 255
            b = 255 - ((v - 0.33) / 0.33) * 255
        else:
            r = 255
            g = 255 - ((v - 0.66) / 0.34) * 255
            b = 0
        set_pixel(i, r, g, b)



def thermal_alert(anim, t):
    n = anim.state["n"]
    pulse = 0.5 + 0.5 * math.sin(t * 5.5)
    for i in range(n):
        v = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(i * 0.22 + t * 3.0))
        set_pixel(i, 255 * v, 120 * v * pulse, 0)



def thermal_core(anim, t):
    n = anim.state["n"]
    center = (n - 1) / 2
    for i in range(n):
        d = abs(i - center) / max(1, center)
        v = (1.0 - d) ** 0.4
        shimmer = 0.7 + 0.3 * math.sin(t * 8 + i * 0.3)
        set_pixel(i, 255 * v, 180 * v * shimmer, 20 * v)



def candy_stripes(anim, t):
    n = anim.state["n"]
    offset = int(t * 8)
    for i in range(n):
        if ((i + offset) // 3) % 2 == 0:
            set_pixel(i, 255, 0, 80)
        else:
            set_pixel(i, 255, 255, 255)



def barber_pole(anim, t):
    n = anim.state["n"]
    offset = int(t * 12)
    for i in range(n):
        stripe = ((i + offset) // 2) % 3
        if stripe == 0:
            set_pixel(i, 255, 0, 0)
        elif stripe == 1:
            set_pixel(i, 255, 255, 255)
        else:
            set_pixel(i, 0, 120, 255)



def royal_stripes(anim, t):
    n = anim.state["n"]
    offset = int(t * 7)
    for i in range(n):
        stripe = ((i + offset) // 4) % 2
        if stripe == 0:
            set_pixel(i, 255, 190, 45)
        else:
            set_pixel(i, 100, 0, 180)



def pulse_white(anim, t):
    v = 0.08 + 0.92 * (0.5 + 0.5 * math.sin(t * 1.8))
    fill(255 * v, 255 * v, 255 * v)


def pulse_red(anim, t):
    v = 0.08 + 0.92 * (0.5 + 0.5 * math.sin(t * 2.8))
    fill(255 * v, 0, 0)


def pulse_amber(anim, t):
    v = 0.10 + 0.90 * (0.5 + 0.5 * math.sin(t * 2.0))
    fill(255 * v, 110 * v, 20 * v)


def pulse_blue(anim, t):
    v = 0.08 + 0.92 * (0.5 + 0.5 * math.sin(t * 2.2))
    fill(0, 120 * v, 255 * v)


def pulse_violet(anim, t):
    v = 0.10 + 0.90 * (0.5 + 0.5 * math.sin(t * 2.4))
    fill(140 * v, 0, 255 * v)


def strobe_white(anim, t):
    on = int(t * 18) % 2 == 0
    fill(255, 255, 255) if on else clear()


def strobe_red(anim, t):
    on = int(t * 18) % 2 == 0
    fill(255, 0, 0) if on else clear()


def warning_flash(anim, t):
    on = int(t * 10) % 2 == 0
    fill(255, 60, 0) if on else clear()


def police_split(anim, t):
    n = anim.state["n"]
    phase = int(t * 6) % 4
    for i in range(n):
        if i < n // 2:
            set_pixel(i, 255 if phase in (0, 1) else 0, 0, 0)
        else:
            set_pixel(i, 0, 0, 255 if phase in (2, 3) else 0)



def police_rotate(anim, t):
    n = anim.state["n"]
    p = int(t * 14) % n
    for i in range(n):
        d = (i - p) % n
        if d < n // 2:
            set_pixel(i, 255, 0, 0)
        else:
            set_pixel(i, 0, 0, 255)



def prism_scroll(anim, t):
    n = anim.state["n"]
    for i in range(n):
        r, g, b = wheel(int((i * 256 / n) + t * 100))
        set_pixel(i, r, g, b)



def prism_breath(anim, t):
    n = anim.state["n"]
    pulse = 0.2 + 0.8 * (0.5 + 0.5 * math.sin(t * 1.3))
    for i in range(n):
        r, g, b = wheel(int(i * 256 / n))
        set_pixel(i, r * pulse, g * pulse, b * pulse)



def prism_wave(anim, t):
    n = anim.state["n"]
    for i in range(n):
        h = (i / n + 0.05 * math.sin(i * 0.2 + t * 2.0) + t * 0.08) % 1.0
        v = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(i * 0.13 + t * 3.2))
        r, g, b = hsv_to_rgb(h, 1.0, v)
        set_pixel(i, r, g, b)



def prism_tide(anim, t):
    n = anim.state["n"]
    center = (0.5 + 0.5 * math.sin(t * 0.5)) * (n - 1)
    for i in range(n):
        d = abs(i - center)
        fade = max(0.0, 1.0 - d / (n * 0.85))
        h = (0.02 * d + t * 0.1) % 1.0
        r, g, b = hsv_to_rgb(h, 1.0, fade)
        set_pixel(i, r, g, b)



def equinox_gradient(anim, t):
    n = anim.state["n"]
    for i in range(n):
        h = (0.03 + 0.62 * (i / max(1, n - 1)) + 0.06 * math.sin(t * 0.4)) % 1.0
        v = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(i * 0.07 + t))
        r, g, b = hsv_to_rgb(h, 0.9, v)
        set_pixel(i, r, g, b)



def royal_gradient(anim, t):
    n = anim.state["n"]
    for i in range(n):
        mix = i / max(1, n - 1)
        r = lerp(255, 110, mix)
        g = lerp(190, 0, mix)
        b = lerp(45, 180, mix)
        pulse = 0.6 + 0.4 * math.sin(t * 1.1 + i * 0.05)
        set_pixel(i, r * pulse, g * pulse, b * pulse)



def forest_gradient(anim, t):
    n = anim.state["n"]
    for i in range(n):
        mix = i / max(1, n - 1)
        r = lerp(20, 120, mix)
        g = lerp(80, 255, mix)
        b = lerp(10, 40, mix)
        pulse = 0.7 + 0.3 * math.sin(t * 0.9 + i * 0.04)
        set_pixel(i, r * pulse, g * pulse, b * pulse)



def icicle_gradient(anim, t):
    n = anim.state["n"]
    for i in range(n):
        mix = i / max(1, n - 1)
        r = lerp(80, 200, mix)
        g = lerp(150, 240, mix)
        b = lerp(255, 255, mix)
        pulse = 0.72 + 0.28 * math.sin(t * 1.2 + i * 0.06)
        set_pixel(i, r * pulse, g * pulse, b * pulse)


# ----------------------- registry -------------------------
ANIMATIONS = {
    "premium_velvet_breath": premium_velvet_breath,
    "champagne_shimmer": champagne_shimmer,
    "aurora_royal": aurora_royal,
    "aurora_emerald": aurora_emerald,
    "midnight_cyan_bloom": midnight_cyan_bloom,
    "violet_silk_current": violet_silk_current,
    "amber_halo": amber_halo,
    "rose_gold_current": rose_gold_current,
    "pearl_drift": pearl_drift,
    "sunset_glass": sunset_glass,
    "molten_lux": molten_lux,
    "golden_orbit": golden_orbit,
    "sapphire_orbit": sapphire_orbit,
    "ruby_orbit": ruby_orbit,
    "triple_crown_orbit": triple_crown_orbit,
    "frosted_mint": frosted_mint,
    "ocean_cathedral": ocean_cathedral,
    "electric_monsoon": electric_monsoon,
    "cyan_monsoon": cyan_monsoon,
    "violet_rain": violet_rain,
    "gold_drizzle": gold_drizzle,
    "ember_rain": ember_rain,
    "scarlet_chase": scarlet_chase,
    "azure_chase": azure_chase,
    "gold_chase": gold_chase,
    "prism_chase": prism_chase,
    "dual_lane_chase": dual_lane_chase,
    "arrowhead_run": arrowhead_run,
    "meteor_white": meteor_white,
    "meteor_teal": meteor_teal,
    "meteor_magenta": meteor_magenta,
    "binary_star": binary_star,
    "trinary_star": trinary_star,
    "scanner_red": scanner_red,
    "scanner_blue": scanner_blue,
    "scanner_gold": scanner_gold,
    "scanner_dual": scanner_dual,
    "scanner_prism": scanner_prism,
    "ripple_blue": ripple_blue,
    "ripple_gold": ripple_gold,
    "ripple_purple": ripple_purple,
    "ripple_frost": ripple_frost,
    "ripple_prism": ripple_prism,
    "plasma_inferno": plasma_inferno,
    "plasma_ocean": plasma_ocean,
    "plasma_ultraviolet": plasma_ultraviolet,
    "plasma_nebula": plasma_nebula,
    "plasma_jade": plasma_jade,
    "matrix_emerald": matrix_emerald,
    "matrix_lime": matrix_lime,
    "matrix_cyan": matrix_cyan,
    "codefall_prism": codefall_prism,
    "arc_welder": arc_welder,
    "fire_classic": fire_classic,
    "fire_whitehot": fire_whitehot,
    "fire_slowburn": fire_slowburn,
    "fire_violet_core": fire_violet_core,
    "lava_flow": lava_flow,
    "lava_embers": lava_embers,
    "lava_crust": lava_crust,
    "ocean_deep": ocean_deep,
    "ocean_surface": ocean_surface,
    "ocean_turquoise": ocean_turquoise,
    "tidal_pulse": tidal_pulse,
    "storm_surge": storm_surge,
    "twinkle_white": twinkle_white,
    "twinkle_gold": twinkle_gold,
    "twinkle_ice": twinkle_ice,
    "twinkle_pink": twinkle_pink,
    "twinkle_prism": twinkle_prism,
    "starfield_silver": starfield_silver,
    "starfield_blue": starfield_blue,
    "starfield_gold": starfield_gold,
    "nebula_pink_blue": nebula_pink_blue,
    "nebula_green_gold": nebula_green_gold,
    "nebula_ultraviolet": nebula_ultraviolet,
    "heatmap_flow": heatmap_flow,
    "thermal_alert": thermal_alert,
    "thermal_core": thermal_core,
    "candy_stripes": candy_stripes,
    "barber_pole": barber_pole,
    "royal_stripes": royal_stripes,
    "pulse_white": pulse_white,
    "pulse_red": pulse_red,
    "pulse_amber": pulse_amber,
    "pulse_blue": pulse_blue,
    "pulse_violet": pulse_violet,
    "strobe_white": strobe_white,
    "strobe_red": strobe_red,
    "warning_flash": warning_flash,
    "police_split": police_split,
    "police_rotate": police_rotate,
    "prism_scroll": prism_scroll,
    "prism_breath": prism_breath,
    "prism_wave": prism_wave,
    "prism_tide": prism_tide,
    "equinox_gradient": equinox_gradient,
    "royal_gradient": royal_gradient,
    "forest_gradient": forest_gradient,
    "icicle_gradient": icicle_gradient,
}

# Preserve all legacy keys and append the curated hospitality collection.
ANIMATIONS.update(hospitality.ANIMATIONS)
ANIMATION_META = {
    key: {"key": key, "collection": "legacy"}
    for key in ANIMATIONS
    if key not in hospitality.ANIMATIONS
}
ANIMATION_META.update(hospitality.ANIMATION_META)

ANIMATION_NAMES = list(ANIMATIONS.keys())


def run_animation(name, duration=8.0):
    if name not in ANIMATIONS:
        raise ValueError(f"Unknown animation: {name}")
    animator = LedAnimator()
    animator.set_animation(name)
    start = time.time()
    try:
        while True:
            animator.step()
            if duration is not None and (time.time() - start) >= duration:
                break
            time.sleep(FRAME_DELAY)
    except KeyboardInterrupt:
        pass
    finally:
        clear()

def demo_all(seconds_each=1.0):
    animator = LedAnimator()
    try:
        for name in ANIMATION_NAMES:
            print(f"Running: {name}")
            animator.set_animation(name)
            start = time.time()
            while (time.time() - start) < seconds_each:
                animator.step()
                time.sleep(FRAME_DELAY)
    except KeyboardInterrupt:
        pass
    finally:
        clear()

#if __name__ == "__main__":
#    demo_all(1.0)
