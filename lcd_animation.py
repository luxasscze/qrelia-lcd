#!/usr/bin/env python3

import math
import random
import time
import pygame

WIDTH = 480
HEIGHT = 320
TARGET_FPS = 60
MODE_DURATION = 8.0

pygame.init()

screen = pygame.display.set_mode(
    (WIDTH, HEIGHT),
    pygame.FULLSCREEN | pygame.DOUBLEBUF
)

pygame.display.set_caption("QRelia LCD Animation Test")
pygame.mouse.set_visible(False)

clock = pygame.time.Clock()
font = pygame.font.SysFont("DejaVu Sans", 18, bold=True)
small_font = pygame.font.SysFont("DejaVu Sans", 13)

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def hsv_to_rgb(h, s=1.0, v=1.0):
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = int(255 * v * (1.0 - s))
    q = int(255 * v * (1.0 - f * s))
    t = int(255 * v * (1.0 - (1.0 - f) * s))
    vv = int(255 * v)

    i %= 6

    if i == 0:
        return vv, t, p
    if i == 1:
        return q, vv, p
    if i == 2:
        return p, vv, t
    if i == 3:
        return p, q, vv
    if i == 4:
        return t, p, vv
    return vv, p, q


def draw_overlay(mode_name, fps):
    panel = pygame.Surface((215, 53), pygame.SRCALPHA)
    panel.fill((0, 0, 0, 150))
    screen.blit(panel, (8, 8))

    title = font.render(mode_name, True, (255, 255, 255))
    screen.blit(title, (16, 12))

    info = small_font.render(
        f"{WIDTH}x{HEIGHT}   Render: {fps:5.1f} FPS",
        True,
        (220, 220, 220)
    )
    screen.blit(info, (16, 37))


# ------------------------------------------------------------
# Particles
# ------------------------------------------------------------

class Particle:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = random.uniform(0, WIDTH)
        self.y = random.uniform(0, HEIGHT)
        self.vx = random.uniform(-130, 130)
        self.vy = random.uniform(-130, 130)
        self.radius = random.randint(2, 7)
        self.hue = random.random()

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt

        if self.x < 0:
            self.x = 0
            self.vx *= -1
        elif self.x >= WIDTH:
            self.x = WIDTH - 1
            self.vx *= -1

        if self.y < 0:
            self.y = 0
            self.vy *= -1
        elif self.y >= HEIGHT:
            self.y = HEIGHT - 1
            self.vy *= -1


particles = [Particle() for _ in range(80)]


# ------------------------------------------------------------
# Demo modes
# ------------------------------------------------------------

def demo_gradient(t):
    """
    Deliberately repaints basically the entire screen.
    Very useful for seeing the real SPI/full-frame refresh capability.
    """

    for y in range(HEIGHT):
        hue = ((y / HEIGHT) + t * 0.10) % 1.0
        color = hsv_to_rgb(hue, 0.85, 1.0)
        pygame.draw.line(screen, color, (0, y), (WIDTH, y))


def demo_plasma(t):
    """
    Heavy full-screen effect.
    Render at reduced internal resolution then scale to 480x320.
    """

    pw = 120
    ph = 80

    plasma = pygame.Surface((pw, ph))
    pixels = pygame.PixelArray(plasma)

    for y in range(ph):
        for x in range(pw):
            v = (
                math.sin(x * 0.15 + t * 2.0)
                + math.sin(y * 0.18 + t * 1.5)
                + math.sin((x + y) * 0.10 + t)
                + math.sin(
                    math.sqrt(
                        (x - pw / 2) ** 2 +
                        (y - ph / 2) ** 2
                    ) * 0.22 - t * 2.5
                )
            )

            h = (v + 4.0) / 8.0
            color = hsv_to_rgb(h % 1.0, 1.0, 1.0)
            pixels[x, y] = color

    del pixels

    plasma = pygame.transform.scale(plasma, (WIDTH, HEIGHT))
    screen.blit(plasma, (0, 0))


def demo_particles(t, dt):
    screen.fill((5, 7, 15))

    # moving background grid
    offset = int((t * 35) % 40)

    for x in range(-40 + offset, WIDTH, 40):
        pygame.draw.line(screen, (18, 25, 45), (x, 0), (x, HEIGHT))

    for y in range(-40 + offset, HEIGHT, 40):
        pygame.draw.line(screen, (18, 25, 45), (0, y), (WIDTH, y))

    for p in particles:
        p.update(dt)

        p.hue = (p.hue + dt * 0.08) % 1.0
        color = hsv_to_rgb(p.hue, 0.8, 1.0)

        pygame.draw.circle(
            screen,
            color,
            (int(p.x), int(p.y)),
            p.radius
        )


def demo_geometry(t):
    screen.fill((3, 5, 12))

    cx = WIDTH // 2
    cy = HEIGHT // 2

    for i in range(30):
        phase = t * (0.4 + i * 0.015)

        radius = 15 + i * 7

        x = cx + math.cos(phase + i * 0.35) * radius
        y = cy + math.sin(phase * 1.3 + i * 0.25) * radius * 0.55

        hue = (i / 30.0 + t * 0.08) % 1.0
        color = hsv_to_rgb(hue, 0.85, 1.0)

        size = 4 + int((math.sin(t * 3 + i) + 1) * 3)

        pygame.draw.circle(
            screen,
            color,
            (int(x), int(y)),
            size
        )

    # rotating centre rings
    for i in range(5):
        r = 25 + i * 13 + int(math.sin(t * 3 + i) * 5)
        hue = (t * 0.1 + i * 0.12) % 1.0

        pygame.draw.circle(
            screen,
            hsv_to_rgb(hue),
            (cx, cy),
            r,
            2
        )


def demo_qrelia(t):
    """
    More representative of the eventual QRelia UI:
    mostly static UI + smooth smaller animations.
    """

    screen.fill((8, 11, 18))

    # Header
    pygame.draw.rect(screen, (16, 21, 32), (0, 0, WIDTH, 62))

    logo = pygame.font.SysFont("DejaVu Sans", 30, bold=True).render(
        "QRELIA",
        True,
        (245, 245, 245)
    )
    screen.blit(logo, (20, 13))

    # online indicator
    pulse = (math.sin(t * 4) + 1) / 2
    radius = int(6 + pulse * 5)

    pygame.draw.circle(
        screen,
        (30, 220, 120),
        (430, 31),
        radius
    )

    # Main card
    pygame.draw.rect(
        screen,
        (18, 24, 37),
        (20, 82, 440, 170),
        border_radius=18
    )

    pygame.draw.rect(
        screen,
        (45, 54, 75),
        (20, 82, 440, 170),
        width=2,
        border_radius=18
    )

    order = pygame.font.SysFont("DejaVu Sans", 22, bold=True).render(
        "ORDER #1842",
        True,
        (205, 210, 220)
    )
    screen.blit(order, (40, 102))

    status = pygame.font.SysFont("DejaVu Sans", 33, bold=True).render(
        "PROCESSING",
        True,
        (255, 255, 255)
    )
    screen.blit(status, (40, 140))

    # Animated progress bar
    pygame.draw.rect(
        screen,
        (32, 39, 54),
        (40, 200, 400, 14),
        border_radius=7
    )

    progress = (math.sin(t * 1.7) + 1) / 2

    pygame.draw.rect(
        screen,
        (75, 190, 255),
        (40, 200, int(400 * progress), 14),
        border_radius=7
    )

    # animated dots
    for i in range(5):
        phase = (t * 3 - i * 0.5)
        brightness = int(90 + 165 * ((math.sin(phase) + 1) / 2))

        pygame.draw.circle(
            screen,
            (brightness, brightness, brightness),
            (180 + i * 30, 235),
            5
        )

    # Footer status
    wifi = small_font.render("Wi-Fi  ✓", True, (160, 220, 185))
    cloud = small_font.render("QRelia Cloud  ✓", True, (160, 220, 185))
    screen.blit(wifi, (24, 282))
    screen.blit(cloud, (135, 282))


MODES = [
    "FULL-SCREEN GRADIENT",
    "PLASMA",
    "PARTICLES",
    "GEOMETRY",
    "QRELIA UI"
]

start_time = time.monotonic()
mode_started = start_time
mode = 0

running = True

try:
    while running:
        dt = clock.tick(TARGET_FPS) / 1000.0
        now = time.monotonic()
        t = now - start_time

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False

                elif event.key in (
                    pygame.K_SPACE,
                    pygame.K_RIGHT
                ):
                    mode = (mode + 1) % len(MODES)
                    mode_started = now

            elif event.type == pygame.MOUSEBUTTONDOWN:
                mode = (mode + 1) % len(MODES)
                mode_started = now

        if now - mode_started >= MODE_DURATION:
            mode = (mode + 1) % len(MODES)
            mode_started = now

        if mode == 0:
            demo_gradient(t)

        elif mode == 1:
            demo_plasma(t)

        elif mode == 2:
            demo_particles(t, dt)

        elif mode == 3:
            demo_geometry(t)

        elif mode == 4:
            demo_qrelia(t)

        fps = clock.get_fps()
        draw_overlay(MODES[mode], fps)

        pygame.display.flip()

finally:
    pygame.mouse.set_visible(True)
    pygame.quit()
