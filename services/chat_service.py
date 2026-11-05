"""Chat backend: DB, accounts, rooms, monitor, middleware."""
import asyncio
import base64
import collections
import json
import secrets
import time
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

# ============================================================
#   Image config
# ============================================================
IMG_ANNOUNCEMENT = "https://cdn.imgurl.ir/uploads/b120819_1789221806450.png"
IMG_NEGOTIATION  = "https://cdn.imgurl.ir/uploads/o047787_1789300950521.png"
IMG_GENERAL      = "https://cdn.imgurl.ir/uploads/o047787_1789300950521.png"
IMG_RED          = "https://cdn.imgurl.ir/uploads/z444928_1789220774719.png"
IMG_BLUE         = "https://cdn.imgurl.ir/uploads/a03825_1789221806450.png"
IMG_GREEN        = "https://cdn.imgurl.ir/uploads/t443304_1789335939464.png"
IMG_GRAY         = "https://cdn.imgurl.ir/uploads/o960165_1789223766398.png"
IMG_YELLOW       = "https://cdn.imgurl.ir/uploads/w344566_1789223522454.png"
IMG_LAYER1       = "https://cdn.imgurl.ir/uploads/h971156_IMG_20261013_173614_673.jpg"
IMG_LAYER2       = ""
IMG_LAYER3       = ""
IMG_LAYER4       = "https://cdn.imgurl.ir/uploads/b72596_1789398783677.png"

AVATAR_NO_PYTHON  = "https://cdn.imgurl.ir/uploads/z444928_1789220774719.png"
AVATAR_AMIR       = "https://cdn.imgurl.ir/uploads/a03825_1789221806450.png"
AVATAR_EZ         = "https://cdn.imgurl.ir/uploads/w344566_1789223522454.png"
AVATAR_TAHA       = "https://cdn.imgurl.ir/uploads/o960165_1789223766398.png"
AVATAR_PEZESHKEAN = "https://cdn.imgurl.ir/uploads/t443304_1789335939464.png"
AVATAR_KJP        = "https://cdn.imgurl.ir/uploads/k34601_u_2_TSp_Tco.jpeg"

ASSETS_DIR = Path("assets")

HARDCODED_AVATARS = {
    "no.python": AVATAR_NO_PYTHON, "amir": AVATAR_AMIR, "ez": AVATAR_EZ,
    "taha": AVATAR_TAHA, "pezeshkean": AVATAR_PEZESHKEAN, "kjp": AVATAR_KJP,
}


def resolve_asset(path):
    if not path: return None
    p = str(path).strip()
    if not p: return None
    if p.lower().startswith(("http://", "https://", "data:")): return p
    if (ASSETS_DIR / p).exists(): return f"/assets/{p}"
    return None


def hardcoded_avatar_url(username):
    return resolve_asset(HARDCODED_AVATARS.get(username))


# ============================================================
#   Rooms
# ============================================================
ROOMS = {
    "announcement": {"id": "announcement", "name": "Announcement Channel",
                     "desc": "Official statements — L1 admins only",
                     "image": IMG_ANNOUNCEMENT, "type": "announcement", "category": "channel"},
    "negotiation": {"id": "negotiation", "name": "Negotiation", "desc": "Talks with enemies, rivals & friends",
                    "image": IMG_NEGOTIATION, "type": "public", "category": "group"},
    "general":     {"id": "general", "name": "General", "desc": "Open group for all operators",
                    "image": IMG_GENERAL, "type": "account", "category": "group"},
    "red":    {"id": "red", "name": "Red Group", "desc": "Red team operations", "image": IMG_RED, "type": "team", "team": "RED", "category": "group"},
    "blue":   {"id": "blue", "name": "Blue Group", "desc": "Blue team operations", "image": IMG_BLUE, "type": "team", "team": "BLUE", "category": "group"},
    "green":  {"id": "green", "name": "Green Group", "desc": "Green team operations", "image": IMG_GREEN, "type": "team", "team": "GREEN", "category": "group"},
    "gray":   {"id": "gray", "name": "Gray Group", "desc": "Gray team operations", "image": IMG_GRAY, "type": "team", "team": "GRAY", "category": "group"},
    "yellow": {"id": "yellow", "name": "Yellow Group", "desc": "Yellow team operations", "image": IMG_YELLOW, "type": "team", "team": "YELLOW", "category": "group"},
    "layer1": {"id": "layer1", "name": "Layer 1", "desc": "Highest security — L1 clearance", "image": IMG_LAYER1, "type": "layer", "level": 1, "category": "group"},
    "layer2": {"id": "layer2", "name": "Layer 2", "desc": "L2 clearance", "image": IMG_LAYER2, "type": "layer", "level": 2, "category": "group"},
    "layer3": {"id": "layer3", "name": "Layer 3", "desc": "L3 clearance", "image": IMG_LAYER3, "type": "layer", "level": 3, "category": "group"},
    "layer4": {"id": "layer4", "name": "Layer 4", "desc": "L4 clearance — all team members", "image": IMG_LAYER4, "type": "layer", "level": 4, "category": "group"},
}


def room_image_url(room_id):
    r = ROOMS.get(room_id)
    return resolve_asset(r.get("image")) if r else None


TRUST_LEVELS: dict = {}


def user_trust_level(username, account):
    if account.get("guest"): return 0
    if username in TRUST_LEVELS: return TRUST_LEVELS[username]
    if account.get("team"): return 4
    return 0


def user_can_access(username, account, room_id):
    room = ROOMS.get(room_id)
    if not room: return False
    t = room.get("type")
    if t == "public": return True
    if account.get("admin"): return True
    if account.get("guest"): return t == "announcement"
    if t == "account": return True
    if t == "announcement": return True
    if t == "team": return account.get("team") == room.get("team")
    if t == "layer":
        lvl = user_trust_level(username, account)
        return lvl != 0 and lvl <= room.get("level", 4)
    return False


def user_can_post(username, account, room_id):
    if not user_can_access(username, account, room_id): return False
    if ROOMS.get(room_id, {}).get("type") == "announcement":
        return account.get("admin") or user_trust_level(username, account) == 1
    return True


# ============================================================
#   Stickers & ranks
# ============================================================
STICKERS = list("♔♕♖♗♘♙♚♛♜♝♞♟♩♪♫♬ϟ☀☁☂☃⛇☽☾❄❆𖤓𖣔☏✈✎✏✐𓍝⚐⚑⚖𖠦𖠿𖣘𖠚𖥠𖣳𓊝⛀⛁⛂⛃⏎⇧⊞⌨⎗⎘⎙☺︎☹︎☠︎❣︎❤︎☘︎⛸︎♠︎♥︎♦︎♣︎♟︎⛷︎⛰︎⏱︎⏲︎⚜︎☯︎☢︎♡♢♤♧⚀⚁⚂⚃⚄⚅➊➋➌➍➎➏➐➑➒➓★☆✡✦✧⌑✩✪⍟❂✫✬✭✮✯✰☪⚝⛤⛥⛦⛧⬅⬆⬇")


class Rank(IntEnum):
    Shadow = 1; Phantom = 2; Spectre = 3; Wraith = 4
    Revenat = 5; ECLIPSE = 6; VOID_LORD = 7; GHOST_PRIME = 8


RED_RANKS    = {Rank.Shadow:"Spark",Rank.Phantom:"Glitch",Rank.Spectre:"Payload",Rank.Wraith:"Overload",Rank.Revenat:"Disruptor",Rank.ECLIPSE:"Exploit",Rank.VOID_LORD:"Blackout Harbinger",Rank.GHOST_PRIME:"Chaos Sovereign"}
BLUE_RANKS   = {Rank.Shadow:"Shield",Rank.Phantom:"Lag",Rank.Spectre:"Freeze",Rank.Wraith:"Vanguard",Rank.Revenat:"Sentinel",Rank.ECLIPSE:"Purifier",Rank.VOID_LORD:"Iron Bastion",Rank.GHOST_PRIME:"Apex Defender"}
GREEN_RANKS  = {Rank.Shadow:"Signal",Rank.Phantom:"Patch",Rank.Spectre:"Gatekeeper",Rank.Wraith:"Firewall",Rank.Revenat:"Encryption",Rank.ECLIPSE:"Architect",Rank.VOID_LORD:"High Inquisitor",Rank.GHOST_PRIME:"Fortress Master"}
GRAY_RANKS   = {Rank.Shadow:"Trace",Rank.Phantom:"Lurker",Rank.Spectre:"Infiltrator",Rank.Wraith:"Cipher",Rank.Revenat:"Specter",Rank.ECLIPSE:"Ghost",Rank.VOID_LORD:"Eclipse Agent",Rank.GHOST_PRIME:"Void Director"}
YELLOW_RANKS = {Rank.Shadow:"Scout",Rank.Phantom:"Analyst",Rank.Spectre:"Tactician",Rank.Wraith:"Commander",Rank.Revenat:"Strategist",Rank.ECLIPSE:"Overseer",Rank.VOID_LORD:"Grand Marshal",Rank.GHOST_PRIME:"Supreme Overlord"}

RANK_COLORS = {"RED":"#ff3b3b","BLUE":"#3b9bff","GREEN":"#2ecc71","GRAY":"#9aa4b2","YELLOW":"#f1c40f"}
GOLD = "#ffd700"
MONITOR_USER = "no.python"


ACCOUNTS = {
    "no.python": {"password":"Gh0st#9x7","rank":Rank.GHOST_PRIME,"team":"RED","admin":True,"display":"no.python"},
    "amir":      {"password":"amir1010","rank":Rank.GHOST_PRIME,"team":"BLUE","admin":True},
    "ez":        {"password":"ezez9010","rank":Rank.GHOST_PRIME,"team":"YELLOW","admin":True},
    "taha":      {"password":"grtaha1020","rank":Rank.GHOST_PRIME,"team":"GRAY","admin":True},
    "vexor":     {"password":"green10pez","rank":Rank.GHOST_PRIME,"team":"GREEN","admin":True},
    "kjp":       {"password":"kjpkjp1010","rank":Rank.Phantom,"team":"GRAY","admin":False},
    "mahan":     {"password":"mahan1050","rank":Rank.Phantom,"team":"YELLOW","admin":False},
    "master":    {"password":"master104080","rank":Rank.Phantom,"team":"GREEN","admin":False},
}


def rank_label(account):
    if account.get("guest"): return "Guest — No Section"
    team = account.get("team", "GRAY")
    rank = account.get("rank", Rank.Shadow)
    mapping = {"RED":RED_RANKS,"BLUE":BLUE_RANKS,"GREEN":GREEN_RANKS,"GRAY":GRAY_RANKS,"YELLOW":YELLOW_RANKS}
    return f"{team.capitalize()} — {mapping.get(team, GRAY_RANKS).get(rank, 'Unknown')}"


def rank_color(account):
    if account.get("guest"): return "#9aa4b2"
    return RANK_COLORS.get(account.get("team", "GRAY"), "#9aa4b2")


# ============================================================
#   DB
# ============================================================
class GhostDB:
    def __init__(self):
        self.sessions: dict = {}
        self.messages: dict[str, list] = {rid: [] for rid in ROOMS}
        self.dms: dict[str, list] = {}
        self.banned_users: set = set()
        self.images: dict = {}
        self.avatars: dict = {}
        self.avatar_urls: dict = {}
        self.display_names: dict = {}
        self.lock = asyncio.Lock()
        self._msg_id = 1
        self._img_counter = 0
        self._max_history = 200

    async def add_session(self, token, username, account, ws, ip):
        async with self.lock:
            self.sessions[token] = {
                "username": username, "account": account, "ws": ws, "ip": ip,
                "joined": time.time(), "muted": False,
                "disconnected_at": time.time() if ws is None else None,
            }

    async def remove_session(self, token):
        async with self.lock:
            self.sessions.pop(token, None)

    async def get_session(self, token):
        return self.sessions.get(token)

    async def add_image(self, b64_data, mime, owner):
        try:
            raw = base64.b64decode(b64_data, validate=False)
        except Exception:
            return None
        if not raw or len(raw) > 800 * 1024: return None
        async with self.lock:
            self._img_counter += 1
            iid = f"u_{self._img_counter}_{secrets.token_urlsafe(5)}"
            self.images[iid] = {"data": b64_data, "mime": mime, "size": len(raw), "owner": owner, "ts": time.time()}
            return iid

    def get_image(self, iid): return self.images.get(iid)

    async def set_avatar(self, username, iid):
        async with self.lock:
            self.avatars[username] = iid

    async def add_message(self, **kw):
        async with self.lock:
            msg = {"id": self._msg_id, "time": datetime.now().strftime("%H:%M:%S"),
                   "ts": time.time(), **kw}
            self._msg_id += 1
            ch = kw["channel"]
            self.messages.setdefault(ch, []).append(msg)
            if len(self.messages[ch]) > self._max_history:
                self.messages[ch] = self.messages[ch][-self._max_history:]
            return msg

    def history(self, channel, limit=150):
        return self.messages.get(channel, [])[-limit:]

    async def clear_messages(self, channel):
        async with self.lock:
            self.messages[channel] = []

    async def delete_message(self, channel, mid):
        async with self.lock:
            arr = self.messages.get(channel, [])
            for i, m in enumerate(arr):
                if m["id"] == mid:
                    arr.pop(i); return True
        return False

    @staticmethod
    def dm_key(a, b): return "|".join(sorted([a, b]))

    async def add_dm(self, sender, receiver, **kw):
        async with self.lock:
            key = self.dm_key(sender, receiver)
            msg = {"id": self._msg_id, "time": datetime.now().strftime("%H:%M:%S"),
                   "ts": time.time(), "sender": sender, "receiver": receiver, **kw}
            self._msg_id += 1
            self.dms.setdefault(key, []).append(msg)
            if len(self.dms[key]) > self._max_history:
                self.dms[key] = self.dms[key][-self._max_history:]
            return msg

    def dm_history(self, a, b, limit=200):
        return self.dms.get(self.dm_key(a, b), [])[-limit:]

    def all_dms_for(self, username, limit=150):
        result = {}
        for key, msgs in self.dms.items():
            parts = key.split("|")
            if len(parts) == 2 and username in parts:
                other = parts[1] if parts[0] == username else parts[0]
                result[other] = msgs[-limit:]
        return result

    def online_users(self):
        result, seen = [], set()
        for s in self.sessions.values():
            if s.get("ws") is None or s["username"] in seen: continue
            seen.add(s["username"])
            acct = s["account"]; un = s["username"]
            result.append({
                "username": un, "display": display_of(un, acct),
                "rank": rank_label(acct), "color": rank_color(acct),
                "admin": acct.get("admin", False), "guest": acct.get("guest", False),
                "team": acct.get("team"), "muted": s.get("muted", False),
                "avatar": user_avatar_url(un),
                "trust_level": user_trust_level(un, acct), "ip": s.get("ip"),
            })
        return result


db = GhostDB()


def display_of(username, account):
    return db.display_names.get(username) or account.get("display", username)


def user_avatar_url(username):
    if username in db.avatars: return f"/api/image/{db.avatars[username]}"
    if username in db.avatar_urls: return db.avatar_urls[username]
    return hardcoded_avatar_url(username)


# ============================================================
#   Monitor
# ============================================================
def get_client_ip(request_or_ws):
    try:
        headers = request_or_ws.headers
    except Exception:
        return "unknown"
    for h in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for", "x-client-ip"):
        v = headers.get(h)
        if v: return v.split(",")[0].strip()
    try:
        c = request_or_ws.client
        return c.host if c else "unknown"
    except Exception:
        return "unknown"


def get_user_agent(request_or_ws):
    try:
        return (request_or_ws.headers.get("user-agent") or "")[:160]
    except Exception:
        return ""


class ConnectionMonitor:
    def __init__(self, max_entries=800):
        self.http_events = collections.deque(maxlen=max_entries)
        self.ws_events = collections.deque(maxlen=max_entries)
        self.active_ws: dict = {}
        self.ip_stats: dict = {}
        self.subscribers: set = set()
        self._id = 0
        self._http_count = 0
        self._ws_count = 0
        self._blocked_ips: set = set()
        self._started = time.time()

    def _next_id(self):
        self._id += 1
        return self._id

    async def log_http(self, ip, method, path, status, ua, referer):
        self._http_count += 1
        evt = {"id": self._next_id(), "time": datetime.now().strftime("%H:%M:%S"),
               "ts": time.time(), "ip": ip, "method": method, "path": path,
               "status": status, "ua": ua, "ref": referer[:120]}
        self.http_events.append(evt)
        st = self.ip_stats.setdefault(ip, {"ip": ip, "count": 0, "first": evt["ts"],
                                            "last": evt["ts"], "paths": collections.Counter(), "uas": set()})
        st["count"] += 1; st["last"] = evt["ts"]; st["paths"][path] += 1
        if ua: st["uas"].add(ua[:60])
        await self._broadcast({"type": "monitor_http", "event": evt})

    async def log_ws(self, ip, username, token, action, ua=""):
        self._ws_count += 1
        short_tok = (token[:10] + "…") if token else ""
        evt = {"id": self._next_id(), "time": datetime.now().strftime("%H:%M:%S"),
               "ts": time.time(), "ip": ip, "username": username or "?",
               "token": short_tok, "action": action, "ua": ua}
        self.ws_events.append(evt)
        if action == "connect":
            self.active_ws[token] = {"token": short_tok, "ip": ip,
                                     "username": username, "ts": evt["ts"], "ua": ua}
        else:
            self.active_ws.pop(token, None)
        await self._broadcast({"type": "monitor_ws", "event": evt})

    async def _broadcast(self, payload):
        if not self.subscribers: return
        data = json.dumps(payload, ensure_ascii=False)
        for token in list(self.subscribers):
            s = db.sessions.get(token)
            if not s or not s.get("ws"):
                self.subscribers.discard(token); continue
            try:
                await s["ws"].send_text(data)
            except Exception:
                self.subscribers.discard(token)

    def snapshot(self):
        ips = [{"ip": st["ip"], "count": st["count"], "first": st["first"],
                "last": st["last"], "blocked": st["ip"] in self._blocked_ips,
                "top_paths": st["paths"].most_common(5), "uas": list(st["uas"])[:3]}
               for st in sorted(self.ip_stats.values(), key=lambda x: -x["count"])]
        return {"type": "monitor_snapshot",
                "http": list(self.http_events)[-250:],
                "ws": list(self.ws_events)[-250:],
                "active": list(self.active_ws.values()),
                "ips": ips,
                "counters": {"http_total": self._http_count, "ws_total": self._ws_count,
                             "unique_ips": len(self.ip_stats), "active_ws": len(self.active_ws),
                             "uptime": int(time.time() - self._started)}}

    def clear(self):
        self.http_events.clear(); self.ws_events.clear()
        self._http_count = 0; self._ws_count = 0

    def block_ip(self, ip): self._blocked_ips.add(ip)
    def unblock_ip(self, ip): self._blocked_ips.discard(ip)
    def is_blocked(self, ip): return ip in self._blocked_ips


monitor = ConnectionMonitor()


# ============================================================
#   Middleware + startup
# ============================================================
def setup_middleware(app):
    @app.middleware("http")
    async def monitor_http(request: Request, call_next):
        ip = get_client_ip(request)
        if monitor.is_blocked(ip):
            try:
                await monitor.log_http(ip, request.method, request.url.path, 403,
                                       get_user_agent(request),
                                       request.headers.get("referer", ""))
            except Exception: pass
            return JSONResponse({"ok": False, "error": "🚫 IP blocked"}, 403)

        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            try:
                await monitor.log_http(ip, request.method, request.url.path, status,
                                       get_user_agent(request),
                                       request.headers.get("referer", ""))
            except Exception: pass
            raise
        try:
            await monitor.log_http(ip, request.method, request.url.path, status,
                                   get_user_agent(request),
                                   request.headers.get("referer", ""))
        except Exception: pass
        return response


async def cleanup_stale_sessions():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        async with db.lock:
            to_remove = [t for t, s in db.sessions.items()
                         if s.get("ws") is None and
                         (now - (s.get("disconnected_at") or s.get("joined", now)) > 180)]
            for t in to_remove: db.sessions.pop(t, None)


async def on_startup():
    asyncio.create_task(cleanup_stale_sessions())
    print(f"💬 Chat ready · monitor for {MONITOR_USER}")


# ============================================================
#   Shared helpers
# ============================================================
def make_token(): return secrets.token_urlsafe(24)


def verify_credentials(u, p):
    acc = ACCOUNTS.get(u)
    if acc and acc["password"] == p:
        return {**acc, "guest": False}
    return None


def make_guest(u):
    return {"password": None, "rank": Rank.Shadow, "team": None,
            "admin": False, "display": u, "guest": True}


def user_payload(username, account):
    return {"username": username, "display": display_of(username, account),
            "rank": rank_label(account), "color": rank_color(account),
            "team": account.get("team"), "admin": account.get("admin", False),
            "guest": account.get("guest", False), "avatar": user_avatar_url(username),
            "trust_level": user_trust_level(username, account),
            "can_monitor": username == MONITOR_USER}


def rooms_for_user(username, account):
    return [{"id": r["id"], "name": r["name"], "desc": r["desc"],
             "image": room_image_url(r["id"]), "type": r.get("type", "public"),
             "category": r.get("category", "group"),
             "accessible": user_can_access(username, account, r["id"]),
             "can_post": user_can_post(username, account, r["id"])}
            for r in ROOMS.values()]


async def send_json(ws, payload):
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception: pass


async def broadcast(payload, exclude_token=None):
    data = json.dumps(payload, ensure_ascii=False)
    dead = []
    for token, s in list(db.sessions.items()):
        if token == exclude_token: continue
        ws = s.get("ws")
        if ws is None: continue
        try: await ws.send_text(data)
        except Exception: dead.append(token)
    for t in dead: await db.remove_session(t)


async def broadcast_to_room(payload, room_id, exclude_token=None):
    data = json.dumps(payload, ensure_ascii=False)
    dead = []
    for token, s in list(db.sessions.items()):
        if token == exclude_token: continue
        ws = s.get("ws")
        if ws is None: continue
        if not user_can_access(s["username"], s["account"], room_id): continue
        try: await ws.send_text(data)
        except Exception: dead.append(token)
    for t in dead: await db.remove_session(t)


async def broadcast_presence():
    users = db.online_users()
    await broadcast({"type": "presence", "users": users, "count": len(users)})