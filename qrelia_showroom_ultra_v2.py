#!/usr/bin/env python3
"""
QRelia Premium Display Showcase
480x320 fullscreen Pygame mockup for Raspberry Pi 3 / SPI LCD.

Design goals:
- One coherent premium hospitality-appliance visual language
- Large glanceable states
- Motion that explains the state instead of random decoration
- Cached backgrounds / glows for better performance
- No expensive per-pixel plasma effects
- Proper QRelia logo support from ~/qrelia_logo.png or ~/qrelia_logo.webp
"""

import math
import os
import random
import time
import pygame

# ============================================================
# CONFIG
# ============================================================

W, H = 480, 320
TARGET_FPS = 60
AUTO_ADVANCE = 7.0
SHOW_FPS = True

pygame.init()
pygame.font.init()

screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.DOUBLEBUF)
pygame.display.set_caption("QRelia Premium Display")
pygame.mouse.set_visible(False)
clock = pygame.time.Clock()

# ============================================================
# PALETTE
# ============================================================

BG = (7, 10, 16)
BG2 = (11, 15, 23)
PANEL = (17, 22, 32)
PANEL2 = (21, 28, 40)
LINE = (42, 51, 68)

TEXT = (246, 244, 240)
SUB = (170, 176, 187)
MUTED = (105, 113, 128)

CORAL = (232, 148, 130)
CORAL_HI = (255, 188, 174)
BLUE = (91, 166, 235)
CYAN = (95, 215, 228)
GREEN = (90, 205, 142)
AMBER = (229, 181, 96)
RED = (232, 103, 105)
WHITE = (255, 255, 255)

# ============================================================
# FONTS
# ============================================================

def mkfont(size, bold=False):
    return pygame.font.SysFont("DejaVu Sans", size, bold=bold)

F9 = mkfont(9)
F10 = mkfont(10)
F11 = mkfont(11)
F12 = mkfont(12)
F13 = mkfont(13)
F14 = mkfont(14)
F16 = mkfont(16)
F18 = mkfont(18, True)
F20 = mkfont(20, True)
F22 = mkfont(22, True)
F24 = mkfont(24, True)
F28 = mkfont(28, True)
F32 = mkfont(32, True)
F38 = mkfont(38, True)
F46 = mkfont(46, True)

# ============================================================
# CORE HELPERS
# ============================================================

def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))

def lerp(a, b, t):
    return a + (b - a) * t

def ease_in_out(t):
    t = clamp(t)
    return 0.5 - 0.5 * math.cos(math.pi * t)

def smoothstep(t):
    t = clamp(t)
    return t * t * (3 - 2 * t)

def txt(surface, text, font, color, x, y, anchor="topleft"):
    image = font.render(str(text), True, color)
    rect = image.get_rect()
    setattr(rect, anchor, (x, y))
    surface.blit(image, rect)
    return rect

def rr(surface, color, rect, radius=16, width=0):
    pygame.draw.rect(surface, color, rect, width=width, border_radius=radius)

def line(surface, color, p1, p2, width=1):
    pygame.draw.line(surface, color, p1, p2, width)

# ============================================================
# CACHES
# ============================================================

_gradient_bg = None
_logo_original = None
_logo_scaled = {}
_glow_cache = {}
_grid_bg = None

def make_gradient_bg():
    global _gradient_bg
    if _gradient_bg is not None:
        return _gradient_bg

    surf = pygame.Surface((W, H)).convert()
    for y in range(H):
        t = y / (H - 1)
        color = (
            int(lerp(6, 12, t)),
            int(lerp(9, 16, t)),
            int(lerp(15, 25, t)),
        )
        pygame.draw.line(surf, color, (0, y), (W, y))

    overlay = pygame.Surface((W, H), pygame.SRCALPHA)
    pygame.draw.circle(overlay, (*CORAL, 13), (420, 40), 140)
    pygame.draw.circle(overlay, (*BLUE, 10), (40, 280), 120)
    surf.blit(overlay, (0, 0))
    _gradient_bg = surf
    return surf

def make_grid_bg():
    global _grid_bg
    if _grid_bg is not None:
        return _grid_bg
    surf = make_gradient_bg().copy()
    for x in range(0, W, 32):
        pygame.draw.line(surf, (18, 24, 34), (x, 0), (x, H), 1)
    for y in range(0, H, 32):
        pygame.draw.line(surf, (18, 24, 34), (0, y), (W, y), 1)
    _grid_bg = surf
    return surf

def load_logo():
    global _logo_original
    if _logo_original is not None:
        return _logo_original

    candidates = [
        os.path.expanduser("~/qrelia_logo.png"),
        os.path.expanduser("~/qrelia_logo.webp"),
        os.path.expanduser("~/QRelia_logo.png"),
        "qrelia_logo.png",
        "qrelia_logo.webp",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                _logo_original = pygame.image.load(p).convert_alpha()
                return _logo_original
            except Exception:
                pass
    return None

def logo_scaled(w, h):
    key = (w, h)
    if key in _logo_scaled:
        return _logo_scaled[key]
    logo = load_logo()
    if logo is None:
        return None
    img = pygame.transform.smoothscale(logo, (w, h))
    _logo_scaled[key] = img
    return img

def glow_sprite(radius, color):
    key = (radius, color)
    if key in _glow_cache:
        return _glow_cache[key]

    size = radius * 4
    s = pygame.Surface((size, size), pygame.SRCALPHA)
    c = size // 2
    layers = [
        (1.75, 10),
        (1.40, 16),
        (1.08, 25),
        (0.78, 40),
        (0.50, 68),
    ]
    for scale, alpha in layers:
        pygame.draw.circle(s, (*color, alpha), (c, c), max(1, int(radius * scale)))
    _glow_cache[key] = s
    return s

def glow(surface, x, y, radius, color, intensity=1.0):
    spr = glow_sprite(radius, color)
    if intensity >= 0.995:
        surface.blit(spr, (int(x - spr.get_width()/2), int(y - spr.get_height()/2)))
    else:
        temp = spr.copy()
        temp.set_alpha(int(255 * clamp(intensity)))
        surface.blit(temp, (int(x - temp.get_width()/2), int(y - temp.get_height()/2)))

# ============================================================
# DESIGN SYSTEM
# ============================================================

def draw_brand(surface, x=18, y=13, w=128, h=31):
    img = logo_scaled(w, h)
    if img is not None:
        surface.blit(img, (x, y))
    else:
        q = txt(surface, "Q", F28, CORAL, x, y - 2)
        txt(surface, "Relia", F28, CORAL, q.right - 2, y - 2)

def topbar(surface, state=None, state_color=GREEN):
    pygame.draw.rect(surface, (9, 12, 18), (0, 0, W, 57))
    pygame.draw.line(surface, LINE, (0, 56), (W, 56), 1)
    draw_brand(surface)
    if state:
        pygame.draw.circle(surface, state_color, (395, 28), 4)
        txt(surface, state.upper(), F11, SUB, 407, 22)

def footer(surface, left="", right=""):
    pygame.draw.line(surface, LINE, (18, H-30), (W-18, H-30), 1)
    if left:
        txt(surface, left, F10, MUTED, 18, H-21)
    if right:
        txt(surface, right, F10, MUTED, W-18, H-21, anchor="topright")

def chip(surface, x, y, label, dot_color=None, fill=PANEL):
    image = F11.render(label, True, TEXT)
    w = image.get_width() + 18 + (14 if dot_color else 0)
    h = 25
    rr(surface, fill, (x, y, w, h), 12)
    pygame.draw.rect(surface, LINE, (x, y, w, h), 1, border_radius=12)
    tx = x + 9
    if dot_color:
        pygame.draw.circle(surface, dot_color, (x+11, y+h//2), 3)
        tx += 12
    surface.blit(image, (tx, y+6))
    return w

def card(surface, rect, radius=18, color=PANEL, border=LINE):
    shadow = pygame.Surface((rect[2]+14, rect[3]+14), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0,0,0,45), (7,7,rect[2],rect[3]), border_radius=radius)
    surface.blit(shadow, (rect[0]-7, rect[1]-7))
    rr(surface, color, rect, radius)
    pygame.draw.rect(surface, border, rect, 1, border_radius=radius)

def status_dot(surface, x, y, color, t, speed=2.0):
    p = (math.sin(t*speed)+1)/2
    glow(surface, x, y, 10, color, 0.45 + p*0.45)
    pygame.draw.circle(surface, color, (int(x), int(y)), 5)

def section_title(surface, eyebrow, title, subtitle=None, y=78):
    txt(surface, eyebrow.upper(), F10, MUTED, 24, y)
    txt(surface, title, F28, TEXT, 24, y+18)
    if subtitle:
        txt(surface, subtitle, F12, SUB, 24, y+53)

# ============================================================
# MOTION PRIMITIVES
# ============================================================

def pulse_rings(surface, center, t, color=CORAL, count=3, max_r=90, speed=0.42):
    cx, cy = center
    for i in range(count):
        p = (t*speed + i/count) % 1.0
        r = int(10 + p*max_r)
        alpha = int(110*(1-p))
        layer = pygame.Surface((W,H), pygame.SRCALPHA)
        pygame.draw.circle(layer, (*color, alpha), (cx,cy), r, 2)
        surface.blit(layer, (0,0))

def moving_packet(surface, start, end, t, color=BLUE, offset=0.0):
    p = (t*0.45 + offset) % 1.0
    p = smoothstep(p)
    x = lerp(start[0], end[0], p)
    y = lerp(start[1], end[1], p)
    glow(surface, x, y, 8, color, 0.9)
    pygame.draw.circle(surface, color, (int(x), int(y)), 4)

def waveform(surface, rect, t, color=GREEN, phase=0.0, thickness=2):
    x0,y0,w,h = rect
    pts=[]
    for x in range(x0, x0+w, 4):
        n=(x-x0)/w
        y = y0+h/2 + math.sin(n*math.pi*4 + t*2.4 + phase)*(h*0.24) + math.sin(n*math.pi*9 + t*1.25)*(h*0.08)
        pts.append((x,int(y)))
    if len(pts)>1:
        glow_layer = pygame.Surface((W,H), pygame.SRCALPHA)
        pygame.draw.lines(glow_layer, (*color, 42), False, pts, 7)
        surface.blit(glow_layer,(0,0))
        pygame.draw.lines(surface, color, False, pts, thickness)

def light_ribbon(surface, y_base, amplitude, t, color, phase=0.0, thickness=2):
    pts=[]
    for x in range(-20, W+21, 8):
        y = y_base + math.sin(x*0.022 + t*1.05 + phase)*amplitude
        y += math.sin(x*0.009 - t*0.65 + phase*0.5)*(amplitude*0.35)
        pts.append((x,int(y)))
    layer=pygame.Surface((W,H),pygame.SRCALPHA)
    pygame.draw.lines(layer,(*color,24),False,pts,13)
    pygame.draw.lines(layer,(*color,54),False,pts,7)
    surface.blit(layer,(0,0))
    pygame.draw.lines(surface,color,False,pts,thickness)

def arc_gauge(surface, center, radius, value, color, bg=(42,51,68), width=6):
    cx,cy=center
    rect=pygame.Rect(cx-radius,cy-radius,radius*2,radius*2)
    start=math.radians(145)
    end=math.radians(395)
    pygame.draw.arc(surface,bg,rect,start,end,width)
    pygame.draw.arc(surface,color,rect,start,start+(end-start)*clamp(value),width)

def particle_burst(surface, cx, cy, t, color_a=GREEN, color_b=CORAL, count=22):
    phase = t % 2.7
    p = clamp(phase/1.55)
    if phase > 1.8:
        return
    for i in range(count):
        ang = i*(math.pi*2/count) + (i%3)*0.09
        speed = 28 + (i%5)*8
        d = speed*p
        x = cx + math.cos(ang)*d
        y = cy + math.sin(ang)*d*0.72
        color = color_a if i%2==0 else color_b
        alpha = 1.0-p
        glow(surface,x,y,5,color,alpha)
        pygame.draw.circle(surface,color,(int(x),int(y)),2)

# ============================================================
# SCENES
# ============================================================

class Scene:
    name = "Scene"

    def draw(self, surface, t):
        raise NotImplementedError

class ReadyScene(Scene):
    name = "READY"

    def draw(self, s, t):
        s.blit(make_gradient_bg(), (0,0))
        topbar(s, "online", GREEN)

        # calm ambient field
        x = 380 + math.sin(t*0.36)*18
        y = 120 + math.cos(t*0.31)*10
        glow(s, x, y, 68, CORAL, 0.55)
        glow(s, 115, 248, 48, BLUE, 0.24)

        txt(s, "LE POMMIER", F10, MUTED, 24, 91)
        txt(s, "Ready for", F38, TEXT, 24, 116)
        txt(s, "service.", F38, TEXT, 24, 154)
        txt(s, "Quiet when nothing needs attention.", F13, SUB, 24, 207)

        chip(s, 24, 240, "Receiver linked", GREEN)
        chip(s, 152, 240, "Cloud online", GREEN)

        status_dot(s, 388, 148, GREEN, t, 1.7)
        pulse_rings(s,(388,148),t, GREEN, 2, 62, 0.22)
        footer(s, "Ambient Device", time.strftime("%H:%M"))

class NewOrderScene(Scene):
    name = "NEW ORDER"

    def draw(self, s, t):
        s.blit(make_gradient_bg(), (0,0))
        topbar(s, "new order", CORAL)

        # meaningful alert pulse
        pulse_rings(s,(387,145),t,CORAL,4,110,0.52)
        glow(s,387,145,35,CORAL,0.78)

        txt(s, "ROOM 24 · 2 GUESTS", F10, MUTED, 24, 88)
        txt(s, "Order", F38, TEXT, 24, 114)
        txt(s, "#1842", F46, TEXT, 24, 151)
        txt(s, "£31.80 · 2 courses", F13, SUB, 24, 211)

        # alert beacon
        rr(s, PANEL, (322,103,132,95), 20)
        pygame.draw.rect(s, CORAL, (322,103,132,95), 1, border_radius=20)
        status_dot(s,388,139,CORAL,t,5.0)
        txt(s,"NEW",F18,TEXT,388,160,anchor="center")

        chip(s,24,245,"Tap to open",CORAL)
        footer(s, "Receiver notified", "just now")

class ProcessingScene(Scene):
    name = "PROCESSING"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"live",GREEN)
        section_title(s,"Room 24 · Order #1842","Service in progress","Anna & Tomas · kitchen and service stay in sync",76)

        # course rail
        labels=[("Received",True),("Starter",True),("Main",False),("Dessert",False)]
        xs=[52,164,280,398]
        y=190
        for i in range(len(xs)-1):
            pygame.draw.line(s,(45,54,71),(xs[i]+16,y),(xs[i+1]-16,y),3)
        for i,(label,done) in enumerate(labels):
            c=GREEN if done else (BLUE if i==2 else LINE)
            if i==2:
                status_dot(s,xs[i],y,BLUE,t,3.3)
            else:
                glow(s,xs[i],y,10,c,0.4 if not done else 0.7)
                pygame.draw.circle(s,c,(xs[i],y),7)
            txt(s,label,F10,TEXT if i<=2 else SUB,xs[i],208,anchor="center")

        # packets physically travelling towards current course
        moving_packet(s,(178,190),(264,190),t,BLUE,0.0)
        moving_packet(s,(178,190),(264,190),t,BLUE,0.5)

        card(s,(24,237,432,43),14,PANEL2)
        txt(s,"CURRENT ACTION",F9,MUTED,36,247)
        txt(s,"Main course preparing",F13,TEXT,36,261)
        chip(s,365,246,"8 min",BLUE)
        footer(s,"Guest note retained","Cloud synced")

class WaitingScene(Scene):
    name = "WAITING"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"waiting",AMBER)

        txt(s,"ROOM 24",F10,MUTED,24,91)
        txt(s,"Guest",F32,TEXT,24,118)
        txt(s,"informed.",F32,TEXT,24,151)
        txt(s,"Main course estimate",F13,SUB,24,196)

        # premium countdown ring; no progress bar
        cx,cy=369,157
        value=0.68+math.sin(t*0.35)*0.02
        arc_gauge(s,(cx,cy),61,value,AMBER,width=7)
        glow(s,cx,cy,34,AMBER,0.22)
        txt(s,"14",F38,TEXT,cx,cy-8,anchor="center")
        txt(s,"MIN",F10,SUB,cx,cy+25,anchor="center")

        # subtle orbiting marker
        ang=t*0.65
        r=61
        px=cx+math.cos(math.radians(145)+ang)*r
        py=cy+math.sin(math.radians(145)+ang)*r
        glow(s,px,py,7,AMBER,0.9)
        pygame.draw.circle(s,AMBER,(int(px),int(py)),3)

        chip(s,24,242,"Main course pending",AMBER)
        footer(s,"Estimate visible to guest","updated 1m ago")

class CompletedScene(Scene):
    name = "COMPLETED"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"complete",GREEN)

        txt(s,"ORDER #1842",F10,MUTED,24,91)
        txt(s,"Service",F32,TEXT,24,119)
        txt(s,"complete.",F32,TEXT,24,153)
        txt(s,"All courses served · £31.80",F13,SUB,24,202)

        # checkmark with restrained celebration
        cx,cy=379,153
        glow(s,cx,cy,48,GREEN,0.38)
        pygame.draw.circle(s,(37,66,54),(cx,cy),49)
        pygame.draw.circle(s,GREEN,(cx,cy),49,2)
        p1=ease_in_out(min((t%3.0)/0.55,1))
        p2=ease_in_out(clamp(((t%3.0)-0.45)/0.65))
        a=(351,154); b=(371,175); c=(409,129)
        pygame.draw.line(s,GREEN,a,(int(lerp(a[0],b[0],p1)),int(lerp(a[1],b[1],p1))),5)
        if p2>0:
            pygame.draw.line(s,GREEN,b,(int(lerp(b[0],c[0],p2)),int(lerp(b[1],c[1],p2))),5)
        particle_burst(s,cx,cy,t,GREEN,CORAL,18)

        chip(s,24,242,"History synced",GREEN)
        footer(s,"Order closed","21:42")

class MultiOrderScene(Scene):
    name = "SERVICE OVERVIEW"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"3 live",GREEN)
        section_title(s,"Service overview","What needs attention",None,76)

        rows=[
            ("ROOM 24","New order #1848","2 guests · £42.60",CORAL,"NEW"),
            ("TABLE 7","Waiting for mains","12 min estimate",AMBER,"6m"),
            ("ROOM 11","Dessert in progress","Order #1843",BLUE,"18m"),
        ]
        y=136
        for i,(loc,title,sub,c,badge) in enumerate(rows):
            rect=(24,y,432,46)
            rr(s,PANEL,rect,13)
            pygame.draw.rect(s,LINE,rect,1,border_radius=13)
            pygame.draw.rect(s,c,(24,y,3,46),border_radius=2)
            txt(s,loc,F9,MUTED,38,y+9)
            txt(s,title,F13,TEXT,108,y+8)
            txt(s,sub,F10,SUB,108,y+27)
            chip(s,390,y+10,badge,c)
            y+=54

        # moving attention marker on new order
        status_dot(s,32,151,CORAL,t,4.2)
        footer(s,"Prioritised by attention","Receiver linked")

class StaleScene(Scene):
    name = "STALE ALERT"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"attention",AMBER)

        # breathing border is the alert
        p=(math.sin(t*2.3)+1)/2
        border=(int(lerp(LINE[0],AMBER[0],p*0.65)),
                int(lerp(LINE[1],AMBER[1],p*0.65)),
                int(lerp(LINE[2],AMBER[2],p*0.65)))

        card(s,(24,86,432,163),20,PANEL,border)
        txt(s,"UNACKNOWLEDGED · 7 MIN",F10,AMBER,42,104)
        txt(s,"Order #1837",F28,TEXT,42,131)
        txt(s,"Table 12 · 3 guests · £26.40",F13,SUB,42,169)
        txt(s,"Needs a decision before service can move.",F12,SUB,42,195)

        rr(s,CORAL,(42,218,172,28),12)
        txt(s,"Accept order",F12,(20,20,20),128,226,anchor="center")
        rr(s,PANEL2,(224,218,118,28),12)
        pygame.draw.rect(s,LINE,(224,218,118,28),1,border_radius=12)
        txt(s,"Decline",F12,TEXT,283,226,anchor="center")

        pulse_rings(s,(402,137),t,AMBER,2,44,0.28)
        footer(s,"Repeats gently until acknowledged","7:04")

class OfflineScene(Scene):
    name = "RECONNECTING"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"reconnecting",RED)

        txt(s,"Connection",F32,TEXT,24,103)
        txt(s,"interrupted.",F32,TEXT,24,138)
        txt(s,"Local service remains visible.",F13,SUB,24,186)

        # device/cloud endpoints
        left=(98,236); right=(382,236)
        glow(s,*left,18,BLUE,0.45)
        glow(s,*right,18,RED,0.40)
        pygame.draw.circle(s,BLUE,left,8)
        pygame.draw.circle(s,RED,right,8)
        pygame.draw.line(s,LINE,left,right,2)

        # packets that fade before reaching cloud
        for off in (0.0,0.28,0.56):
            p=(t*0.32+off)%1.0
            x=lerp(left[0]+14,right[0]-14,p)
            y=236+math.sin(p*math.pi)*-15
            alpha=1.0 if p<0.78 else 1.0-(p-0.78)/0.22
            glow(s,x,y,7,BLUE,alpha)
            pygame.draw.circle(s,BLUE,(int(x),int(y)),3)

        txt(s,"DEVICE",F9,MUTED,left[0],260,anchor="center")
        txt(s,"CLOUD",F9,MUTED,right[0],260,anchor="center")
        footer(s,"Wi-Fi connected","Retrying automatically")

class PairingScene(Scene):
    name = "PAIRING"

    def draw(self,s,t):
        s.blit(make_grid_bg(),(0,0))
        topbar(s,"device setup",BLUE)

        txt(s,"PAIR THIS DEVICE",F10,MUTED,24,88)
        txt(s,"QRL-4821",F38,TEXT,24,118)
        txt(s,"PIN · 7319",F18,CORAL,24,170)
        txt(s,"Admin → Ambient Device → Pair",F12,SUB,24,203)

        # scanner block
        rect=(308,90,134,144)
        card(s,rect,18,PANEL2)
        scan_y=104+int(((math.sin(t*1.0)+1)/2)*108)
        layer=pygame.Surface((W,H),pygame.SRCALPHA)
        pygame.draw.rect(layer,(*BLUE,42),(320,scan_y,110,2))
        glow(s,375,scan_y,18,BLUE,0.3)
        s.blit(layer,(0,0))

        # corner trackers
        corners=[(321,103),(429,103),(321,221),(429,221)]
        for x,y in corners:
            dx=12
            pygame.draw.line(s,BLUE,(x,y),(x+dx if x<375 else x-dx,y),2)
            pygame.draw.line(s,BLUE,(x,y),(x,y+dx if y<160 else y-dx),2)

        # pairing matrix
        random.seed(13)
        for gy in range(5):
            for gx in range(5):
                if random.random()>0.38:
                    rr(s,TEXT,(337+gx*15,121+gy*15,8,8),2)

        footer(s,"Wi-Fi connected","Awaiting pairing")

class WiFiScene(Scene):
    name = "WI-FI SETUP"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"setup",BLUE)
        section_title(s,"Network","Choose venue Wi-Fi",None,76)

        networks=[
            ("LePommier-Operations",4,True),
            ("Hotel-Guest",4,False),
            ("Office-5G",3,False),
            ("Kitchen-Tablet",2,False),
        ]
        y=130
        scan_index=int((t*0.7)%len(networks))
        for i,(name,strength,selected) in enumerate(networks):
            rect=(24,y,432,38)
            fill=PANEL2 if selected else PANEL
            rr(s,fill,rect,12)
            pygame.draw.rect(s,BLUE if selected else LINE,rect,1,border_radius=12)
            if i==scan_index:
                glow(s,39,y+19,7,BLUE,0.7)
            txt(s,name,F12,TEXT,42,y+11)
            for b in range(4):
                bh=4+b*4
                c=GREEN if b<strength else LINE
                pygame.draw.rect(s,c,(397+b*9,y+28-bh,5,bh),border_radius=2)
            if selected:
                txt(s,"SELECTED",F9,BLUE,322,y+12)
            y+=45
        footer(s,"Touch a network to continue","Scanning")

class ShowroomScene(Scene):
    name = "SHOWROOM"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))

        # premium ambient ribbons only
        light_ribbon(s,92,18,t,CORAL,0.0,2)
        light_ribbon(s,135,22,t,BLUE,1.2,2)
        light_ribbon(s,183,20,t,GREEN,2.4,2)
        light_ribbon(s,230,15,t,AMBER,3.4,2)

        # travelling light seeds
        for i,c in enumerate((CORAL,BLUE,GREEN,AMBER)):
            p=(t*0.12+i*0.21)%1.0
            x=int(lerp(25,455,p))
            y=[92,135,183,230][i] + math.sin(x*0.022+t*1.05+i)*[18,22,20,15][i]
            glow(s,x,y,10,c,0.95)
            pygame.draw.circle(s,c,(x,int(y)),3)

        # central brand, minimal and premium
        glow(s,240,157,54,CORAL,0.12)
        draw_brand(s,160,111,160,39)
        txt(s,"Service, made visible.",F24,TEXT,240,167,anchor="center")
        txt(s,"New order · Processing · Waiting · Complete",F11,SUB,240,204,anchor="center")
        chip(s,24,262,"SHOWROOM",CORAL)
        txt(s,"Ambient animation profile",F10,MUTED,456,269,anchor="topright")

class DiagnosticsScene(Scene):
    name = "DIAGNOSTICS"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"system",GREEN)
        section_title(s,"Device health","Everything visible at a glance",None,76)

        boxes=[
            ("CLOUD","Authenticated",GREEN),
            ("NETWORK","Wi-Fi strong",GREEN),
            ("AMBIENT LED","GPIO18 ready",CORAL),
            ("DISPLAY","480 × 320",BLUE),
        ]
        coords=[(24,130),(244,130),(24,200),(244,200)]
        for (label,value,c),(x,y) in zip(boxes,coords):
            card(s,(x,y,212,58),14,PANEL)
            txt(s,label,F9,MUTED,x+12,y+10)
            txt(s,value,F12,TEXT,x+12,y+26)
            status_dot(s,x+190,y+29,c,t,2.0+(x%3))

        waveform(s,(36,169,176,18),t,GREEN)
        arc_gauge(s,(393,229),18,0.82,BLUE,width=3)
        footer(s,"Hold logo 3 sec to exit","All systems healthy")

class NightScene(Scene):
    name = "NIGHT"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        # extremely restrained
        glow(s,240,150,54,BLUE,0.13)
        draw_brand(s,18,14,116,29)
        status_dot(s,437,28,GREEN,t,1.2)

        txt(s,time.strftime("%H:%M"),F46,TEXT,240,112,anchor="center")
        txt(s,"Quiet standby",F14,SUB,240,174,anchor="center")
        light_ribbon(s,249,7,t,BLUE,0.4,1)
        light_ribbon(s,268,5,t,CORAL,1.7,1)
        txt(s,"Le Pommier",F10,MUTED,24,291)
        txt(s,"Cloud online",F10,MUTED,456,291,anchor="topright")

class CourseScene(Scene):
    name = "COURSE SERVICE"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"live",GREEN)
        section_title(s,"Order #1842","Course service","Progress the meal without closing the order",76)

        guests=[("Anna","Starter served","Main preparing"),("Tomas","Starter served","Main preparing")]
        y=141
        for gi,(guest,done,current) in enumerate(guests):
            card(s,(24,y,432,57),14,PANEL)
            txt(s,guest,F13,TEXT,38,y+10)
            txt(s,done,F10,GREEN,38,y+31)
            txt(s,current,F11,SUB,206,y+19)
            # moving plate token
            sx=331; ex=424
            pygame.draw.line(s,LINE,(sx,y+29),(ex,y+29),2)
            moving_packet(s,(sx,y+29),(ex,y+29),t,BLUE,0.3*gi)
            y+=66
        footer(s,"Course controls remain available","Receiver synced")

class KitchenBarScene(Scene):
    name = "STATIONS"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"routing",BLUE)
        section_title(s,"Preparation routing","Kitchen + bar split","Orders stay coordinated while stations work independently",76)

        # two operational lanes
        lanes=[
            ("KITCHEN",CORAL,["Sticky toffee pudding","Main course"]),
            ("BAR",BLUE,["Sparkling water","White wine"]),
        ]
        y=139
        for name,c,items in lanes:
            card(s,(24,y,432,54),14,PANEL)
            status_dot(s,44,y+27,c,t,2.5)
            txt(s,name,F11,c,61,y+10)
            txt(s," · ".join(items),F11,TEXT,61,y+28)
            # packets routed to station
            moving_packet(s,(315,y+27),(425,y+27),t,c,0.2 if name=="BAR" else 0)
            y+=65
        footer(s,"One guest order · multiple stations","Routing active")

class GuestUpdateScene(Scene):
    name = "GUEST UPDATE"

    def draw(self,s,t):
        s.blit(make_gradient_bg(),(0,0))
        topbar(s,"guest view",GREEN)
        section_title(s,"Room 24","Guest status update","Keep reassurance visible without staff needing to call",76)

        card(s,(24,139,432,99),18,PANEL)
        txt(s,"Your order is being prepared",F20,TEXT,42,159)
        txt(s,"Main course · approximately 8 minutes",F12,SUB,42,194)

        # three calm status pulses
        for i,(label,c) in enumerate((("Received",GREEN),("Preparing",BLUE),("On its way",LINE))):
            x=74+i*154
            pygame.draw.circle(s,c,(x,258),7)
            if i==1: status_dot(s,x,258,BLUE,t,2.6)
            txt(s,label,F10,TEXT if i<2 else SUB,x,275,anchor="center")
            if i<2:
                pygame.draw.line(s,(50,60,75),(x+12,258),(x+142,258),2)
        footer(s,"Guest sees the same service journey","Live")

SCENES = [
    ReadyScene(),
    NewOrderScene(),
    ProcessingScene(),
    WaitingScene(),
    CompletedScene(),
    MultiOrderScene(),
    StaleScene(),
    CourseScene(),
    KitchenBarScene(),
    GuestUpdateScene(),
    OfflineScene(),
    PairingScene(),
    WiFiScene(),
    ShowroomScene(),
    DiagnosticsScene(),
    NightScene(),
]

# ============================================================
# TRANSITION
# ============================================================

transition_surface = pygame.Surface((W,H), pygame.SRCALPHA)

def draw_transition(surface, elapsed, duration=0.42):
    if elapsed >= duration:
        return
    p = elapsed / duration
    # quick black veil fade-out from old scene, then clean reveal
    alpha = int(120 * (1.0 - p))
    transition_surface.fill((0,0,0,alpha))
    surface.blit(transition_surface,(0,0))

# ============================================================
# MAIN LOOP
# ============================================================

def main():
    running = True
    idx = 0
    auto = True
    scene_started = time.monotonic()
    app_started = scene_started

    while running:
        dt = clock.tick(TARGET_FPS) / 1000.0
        now = time.monotonic()
        scene_t = now - scene_started
        app_t = now - app_started

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key in (pygame.K_RIGHT, pygame.K_SPACE, pygame.K_RETURN):
                    idx = (idx + 1) % len(SCENES)
                    scene_started = time.monotonic()
                elif event.key == pygame.K_LEFT:
                    idx = (idx - 1) % len(SCENES)
                    scene_started = time.monotonic()
                elif event.key == pygame.K_s:
                    auto = not auto
                elif event.key == pygame.K_HOME:
                    idx = 0
                    scene_started = time.monotonic()
            elif event.type == pygame.MOUSEBUTTONDOWN:
                idx = (idx + 1) % len(SCENES)
                scene_started = time.monotonic()

        if auto and scene_t >= AUTO_ADVANCE:
            idx = (idx + 1) % len(SCENES)
            scene_started = time.monotonic()
            scene_t = 0.0

        SCENES[idx].draw(screen, scene_t)
        draw_transition(screen, scene_t)

        if SHOW_FPS:
            txt(screen, f"{SCENES[idx].name}  {clock.get_fps():4.1f} FPS", F9, (92,99,112), 8, 4)

        pygame.display.flip()

    pygame.mouse.set_visible(True)
    pygame.quit()

if __name__ == "__main__":
    main()
