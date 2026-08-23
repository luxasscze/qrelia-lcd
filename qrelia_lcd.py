#!/usr/bin/env python3
"""Production 480x320 LCD renderer for the QRelia ambient device."""
import math
import os
import threading
import time

import pygame

W, H = 480, 320
BG = (7, 10, 16); PANEL = (17, 22, 32); PANEL2 = (21, 28, 40); LINE = (42, 51, 68)
TEXT = (246, 244, 240); SUB = (170, 176, 187); MUTED = (105, 113, 128)
CORAL = (232, 148, 130); BLUE = (91, 166, 235); CYAN = (95, 215, 228)
GREEN = (90, 205, 142); AMBER = (229, 181, 96); RED = (232, 103, 105)


def clamp(v, lo=0.0, hi=1.0): return max(lo, min(hi, v))
def lerp(a, b, t): return a + (b-a)*t
def smoothstep(t): t=clamp(t); return t*t*(3-2*t)


class QReliaLCDDisplay:
    """Snapshot-driven display. It never owns cloud/order state."""
    def __init__(self, target_fps=30):
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        pygame.init(); pygame.font.init()
        self.target_fps = float(target_fps)
        flags = pygame.DOUBLEBUF
        if os.environ.get("QRELIA_LCD_WINDOWED", "0").lower() not in ("1","true","yes"):
            flags |= pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((W,H), flags)
        pygame.display.set_caption("QRelia")
        pygame.mouse.set_visible(False)
        self.lock = threading.Lock()
        self.model = {}
        self.started = time.monotonic(); self.scene_started = self.started
        self.scene_key = None; self.previous_frame = None
        self.transition_seconds = max(0.08, float(os.environ.get("QRELIA_LCD_TRANSITION_SECONDS","0.38")))
        self.fonts = {s: pygame.font.SysFont("DejaVu Sans", s, bold=b) for s,b in [(9,0),(10,0),(11,0),(12,0),(13,0),(14,0),(16,0),(18,1),(20,1),(22,1),(24,1),(28,1),(32,1),(38,1),(46,1)]}
        self.gradient = self._gradient(); self.logo_cache={}; self.logo=self._load_logo()

    def update(self, snapshot):
        with self.lock: self.model = dict(snapshot or {})

    def close(self):
        pygame.mouse.set_visible(True); pygame.quit()

    def _f(self,n): return self.fonts[n]
    def _txt(self,s,text,size,color,x,y,anchor="topleft"):
        image=self._f(size).render(str(text),True,color); r=image.get_rect(); setattr(r,anchor,(x,y)); s.blit(image,r); return r
    def _rr(self,s,color,rect,r=16,w=0): pygame.draw.rect(s,color,rect,width=w,border_radius=r)
    def _card(self,s,rect,r=16,color=PANEL):
        self._rr(s,color,rect,r); pygame.draw.rect(s,LINE,rect,1,border_radius=r)
    def _gradient(self):
        surf=pygame.Surface((W,H)).convert()
        for y in range(H):
            p=y/(H-1); c=(int(lerp(6,12,p)),int(lerp(9,16,p)),int(lerp(15,25,p))); pygame.draw.line(surf,c,(0,y),(W,y))
        o=pygame.Surface((W,H),pygame.SRCALPHA); pygame.draw.circle(o,(*CORAL,13),(420,40),140); pygame.draw.circle(o,(*BLUE,10),(40,280),120); surf.blit(o,(0,0)); return surf
    def _load_logo(self):
        for p in [os.path.expanduser("~/qrelia_logo.png"),os.path.expanduser("~/qrelia_logo.webp"),"qrelia_logo.png","qrelia_logo.webp"]:
            if os.path.exists(p):
                try: return pygame.image.load(p).convert_alpha()
                except Exception: pass
        return None
    def _brand(self,s,x=18,y=13,w=128,h=31):
        if self.logo:
            key=(w,h)
            if key not in self.logo_cache: self.logo_cache[key]=pygame.transform.smoothscale(self.logo,key)
            s.blit(self.logo_cache[key],(x,y))
        else:
            q=self._txt(s,"Q",28,CORAL,x,y-2); self._txt(s,"Relia",28,CORAL,q.right-2,y-2)
    def _topbar(self,s,state,color):
        pygame.draw.rect(s,(9,12,18),(0,0,W,57)); pygame.draw.line(s,LINE,(0,56),(W,56),1); self._brand(s)
        pygame.draw.circle(s,color,(395,28),4); self._txt(s,state.upper(),11,SUB,407,22)
    def _footer(self,s,left="",right=""):
        pygame.draw.line(s,LINE,(18,H-30),(W-18,H-30),1)
        self._txt(s,left,10,MUTED,18,H-21)
        self._txt(s,right,10,MUTED,W-18,H-21,"topright")
    def _title(self,s,eyebrow,title,subtitle=None,y=77):
        self._txt(s,eyebrow.upper(),10,MUTED,24,y); self._txt(s,title,28,TEXT,24,y+18)
        if subtitle: self._txt(s,subtitle,12,SUB,24,y+53)
    def _dot(self,s,x,y,c,t):
        p=(math.sin(t*2.3)+1)/2; pygame.draw.circle(s,(*c,), (int(x),int(y)),5)
        ring=pygame.Surface((W,H),pygame.SRCALPHA); pygame.draw.circle(ring,(*c,int(25+45*p)),(int(x),int(y)),int(8+4*p),2); s.blit(ring,(0,0))
    def _ribbon(self,s,y,a,t,c,phase=0):
        pts=[]
        for x in range(-20,W+21,8): pts.append((x,int(y+math.sin(x*.022+t*1.05+phase)*a+math.sin(x*.009-t*.65+phase*.5)*(a*.35))))
        layer=pygame.Surface((W,H),pygame.SRCALPHA); pygame.draw.lines(layer,(*c,35),False,pts,9); s.blit(layer,(0,0)); pygame.draw.lines(s,c,False,pts,2)
    def _packet(self,s,start,end,t,c,offset=0):
        p=smoothstep((t*.45+offset)%1); x=lerp(start[0],end[0],p); y=lerp(start[1],end[1],p); pygame.draw.circle(s,c,(int(x),int(y)),4)
    def _order_label(self,o):
        loc=o.get("location") or "Guest order"; count=o.get("itemCount"); return f"{loc} · {count} item{'s' if count != 1 else ''}" if count else loc
    def _fmt_age(self,seconds):
        seconds=max(0,int(seconds or 0)); return f"{seconds//60}m {seconds%60:02d}s" if seconds>=60 else f"{seconds}s"

    def _select_scene(self,m):
        ev=m.get("event") or {}
        if m.get("showroom"): return "showroom"
        if not m.get("provisioned"): return "pairing" if m.get("runtimeMode") == "pairing" else "setup"
        if m.get("connectionLost"): return "offline"
        if ev: return "event:"+str(ev.get("kind") or "update")
        if m.get("stale"): return "stale"
        orders=m.get("activeOrders") or []
        if len(orders)>1: return "multi"
        if orders: return orders[0].get("phase") or "ready"
        if m.get("systemAmbientState") in ("error","connectionLost"): return "offline"
        return "ready"

    def render(self):
        for e in pygame.event.get():
            if e.type==pygame.QUIT: return False
            if e.type==pygame.KEYDOWN and e.key in (pygame.K_ESCAPE,pygame.K_q): return False
        with self.lock: m=dict(self.model)
        now=time.monotonic(); key=self._select_scene(m)
        if key != self.scene_key:
            self.previous_frame=self.screen.copy() if self.scene_key is not None else None
            self.scene_key=key; self.scene_started=now
        t=now-self.scene_started
        s=self.screen; s.blit(self.gradient,(0,0))
        self._draw_scene(s,key,m,t)
        if self.previous_frame is not None and t < self.transition_seconds:
            p=clamp(t/self.transition_seconds); old=self.previous_frame.copy(); old.set_alpha(int(190*(1-p))); s.blit(old,(int(-20*p),0))
        pygame.display.flip(); return True

    def _draw_scene(self,s,key,m,t):
        if key=="showroom": return self._showroom(s,m,t)
        if key=="setup": return self._setup(s,m,t)
        if key=="pairing": return self._pairing(s,m,t)
        if key=="offline": return self._offline(s,m,t)
        if key=="stale": return self._stale(s,m,t)
        if key=="multi": return self._multi(s,m,t)
        if key.startswith("event:"): return self._event(s,m,t,key.split(":",1)[1])
        if key=="pending": return self._pending(s,m,t)
        if key=="processing": return self._processing(s,m,t)
        if key=="waiting": return self._waiting(s,m,t)
        return self._ready(s,m,t)

    def _ready(self,s,m,t):
        self._topbar(s,"live",GREEN); self._title(s,"Ambient service","Ready for the next request","QRelia is connected and listening")
        self._card(s,(24,159,432,82),18,PANEL2); self._dot(s,52,200,GREEN,t); self._txt(s,"READY",32,TEXT,78,173); self._txt(s,"No active orders",13,SUB,80,211)
        self._ribbon(s,260,7,t,BLUE,.4); self._footer(s,m.get("deviceName","QRelia"),"Cloud online" if m.get("signalRConnected") else "Connecting")
    def _pending(self,s,m,t):
        o=(m.get("activeOrders") or [{}])[0]; self._topbar(s,"new order",CORAL); self._title(s,"Needs attention",f"Order #{o.get('shortId','--')}",self._order_label(o))
        self._card(s,(24,159,432,84),18,PANEL2); self._txt(s,"ACCEPT OR DECLINE",18,CORAL,42,174); self._txt(s,"Waiting for staff acknowledgement",12,SUB,42,207); self._dot(s,425,201,CORAL,t)
        self._footer(s,"Pending " + self._fmt_age(o.get("pendingAgeSeconds")),o.get("notes") or "Live from QRelia")
    def _processing(self,s,m,t):
        o=(m.get("activeOrders") or [{}])[0]; self._topbar(s,"processing",BLUE); self._title(s,"In preparation",f"Order #{o.get('shortId','--')}",self._order_label(o))
        self._card(s,(24,160,432,82),18,PANEL); self._txt(s,"PREPARING",24,TEXT,42,174); self._txt(s,"Service is moving",12,SUB,42,208)
        pygame.draw.line(s,LINE,(274,201),(430,201),3)
        for i in range(3): self._packet(s,(282,201),(425,201),t,BLUE,i*.33)
        self._footer(s,"Active " + self._fmt_age(o.get("ageSeconds")),o.get("notes") or "Receiver synced")
    def _waiting(self,s,m,t):
        o=(m.get("activeOrders") or [{}])[0]; wait=o.get("waitMinutes"); self._topbar(s,"waiting",AMBER); self._title(s,"Guest waiting",f"Order #{o.get('shortId','--')}",self._order_label(o))
        self._card(s,(24,158,432,88),18,PANEL2); self._txt(s,f"{wait} MIN" if wait else "WAITING",32,AMBER,42,171); self._txt(s,"Guest has been updated",12,SUB,43,214); self._dot(s,420,202,AMBER,t)
        self._footer(s,"Waiting state",o.get("notes") or "Keep service visible")
    def _multi(self,s,m,t):
        orders=(m.get("activeOrders") or [])[:3]; self._topbar(s,"live orders",GREEN); self._title(s,"Service overview",f"{len(m.get('activeOrders') or [])} active orders",f"{m.get('pendingCount',0)} pending · {m.get('processingCount',0)} preparing · {m.get('waitingCount',0)} waiting")
        y=148
        colors={"pending":CORAL,"processing":BLUE,"waiting":AMBER}
        for o in orders:
            c=colors.get(o.get("phase"),GREEN); self._card(s,(24,y,432,43),12,PANEL); pygame.draw.rect(s,c,(24,y,4,43),border_radius=2)
            self._txt(s,"#"+str(o.get("shortId","--")),13,TEXT,40,y+8); self._txt(s,o.get("location") or o.get("phase",""),11,SUB,130,y+10); self._txt(s,str(o.get("phase","" )).upper(),10,c,438,y+11,"topright"); y+=48
        self._footer(s,"Prioritised by service state","SignalR live")
    def _stale(self,s,m,t):
        pending=[o for o in (m.get("activeOrders") or []) if o.get("phase")=="pending"]; o=pending[0] if pending else {}
        self._topbar(s,"attention",AMBER); self._title(s,"Unacknowledged order",f"Order #{o.get('shortId','--')}",self._order_label(o))
        self._card(s,(24,158,432,88),18,PANEL2); self._txt(s,"ACTION REQUIRED",24,AMBER,42,171); self._txt(s,"Pending for " + self._fmt_age(o.get("pendingAgeSeconds")),13,TEXT,43,208); self._dot(s,421,202,AMBER,t)
        self._footer(s,"Stale threshold reached","Accept or decline in Receiver")
    def _event(self,s,m,t,kind):
        e=m.get("event") or {}; oid=str(e.get("order_id") or ""); short=oid[-6:] if oid.isdigit() else oid.split("-")[0][:8].upper(); loc=e.get("location") or "Guest order"
        if kind=="new": c,title,big=CORAL,"NEW ORDER","#"+short
        elif kind=="completed": c,title,big=GREEN,"ORDER COMPLETE","DONE"
        elif kind=="cancelled": c,title,big=RED,"ORDER CANCELLED","CANCELLED"
        elif kind=="waiting": c,title,big=AMBER,"GUEST WAITING",(str(e.get("wait_minutes"))+" MIN") if e.get("wait_minutes") else "WAITING"
        else: c,title,big=BLUE,"ORDER UPDATE","PREPARING"
        self._topbar(s,title,c); self._title(s,"Live update",big,loc)
        self._card(s,(24,160,432,84),18,PANEL2); self._dot(s,53,201,c,t); self._txt(s,title,22,c,78,176); detail=e.get("notes") or ((str(e.get("item_count"))+" items") if e.get("item_count") else "Status synced across QRelia"); self._txt(s,detail[:50],12,SUB,79,211)
        self._footer(s,"Realtime SignalR event","Device + Receiver synchronized")
    def _offline(self,s,m,t):
        self._topbar(s,"offline",RED); self._title(s,"Connection interrupted","Reconnecting to QRelia","Orders already known by the device remain visible")
        self._card(s,(24,161,432,78),18,PANEL2); self._dot(s,53,199,RED,t); self._txt(s,"RECONNECTING",24,TEXT,78,174); self._txt(s,m.get("runtimeMessage") or "Checking Wi-Fi and cloud",12,SUB,79,210)
        self._footer(s,m.get("ssid") or "Venue Wi-Fi",m.get("ipAddress") or "No network address")
    def _setup(self,s,m,t):
        self._topbar(s,"setup",BLUE); self._title(s,"Device setup","Connect QRelia to the venue",m.get("runtimeMessage") or "Open QRelia-Setup to continue")
        self._card(s,(24,159,432,82),18,PANEL); self._txt(s,"QRelia-Setup",22,BLUE,42,174); self._txt(s,"qrelia.local · 192.168.4.1",13,TEXT,42,209); self._packet(s,(309,201),(425,201),t,BLUE)
        self._footer(s,"Wi-Fi + setup code + PIN","Secure provisioning")
    def _pairing(self,s,m,t):
        self._topbar(s,"pairing",BLUE); self._title(s,"Secure pairing","Linking this display to the venue",m.get("runtimeMessage") or "Validating setup code and PIN")
        self._card(s,(24,159,432,82),18,PANEL2); self._dot(s,56,201,BLUE,t); self._txt(s,"PAIRING",28,TEXT,82,175); self._txt(s,"Claiming device identity",12,SUB,83,211)
        self._footer(s,m.get("ssid") or "Venue Wi-Fi",m.get("ipAddress") or "Network ready")
    def _showroom(self,s,m,t):
        self._ribbon(s,92,18,t,CORAL,0); self._ribbon(s,135,22,t,BLUE,1.2); self._ribbon(s,183,20,t,GREEN,2.4); self._ribbon(s,230,15,t,AMBER,3.4)
        self._brand(s,160,108,160,39); self._txt(s,"Service, made visible.",24,TEXT,240,168,"center"); self._txt(s,"New order · Processing · Waiting · Complete",11,SUB,240,205,"center")
        self._footer(s,"SHOWROOM",m.get("deviceName") or "QRelia")
