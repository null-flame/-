"""Chat REST API + WebSocket."""
import json
import time
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
import base64

from services import chat_service as cs

router = APIRouter()


@router.post("/api/login")
async def login(payload: dict, request: Request):
    username = (payload.get("username") or "").strip()
    password = (payload.get("password") or "").strip()
    guest = payload.get("guest", False)

    if not username or len(username) > 20:
        return JSONResponse({"ok": False, "error": "نام کاربری نامعتبر"}, 400)
    if username in cs.db.banned_users:
        return JSONResponse({"ok": False, "error": "🚫 مسدود هستید"}, 403)

    account = cs.make_guest(username) if guest else cs.verify_credentials(username, password)
    if not account:
        return JSONResponse({"ok": False, "error": "نام کاربری یا رمز اشتباه"}, 401)

    token = cs.make_token()
    await cs.db.add_session(token, username, account, None, cs.get_client_ip(request))
    return {"ok": True, "token": token, **cs.user_payload(username, account)}


@router.post("/api/logout")
async def logout(payload: dict):
    token = payload.get("token")
    if token:
        s = cs.db.sessions.get(token)
        if s and s.get("ws"):
            await cs.send_json(s["ws"], {"type": "kick", "reason": "خروج"})
        await cs.db.remove_session(token)
        cs.monitor.subscribers.discard(token)
        await cs.broadcast_presence()
    return {"ok": True}


@router.post("/api/verify")
async def verify(payload: dict):
    token = payload.get("token")
    s = cs.db.sessions.get(token)
    if not s:
        return JSONResponse({"ok": False}, 401)
    return {"ok": True, "token": token, **cs.user_payload(s["username"], s["account"])}


@router.post("/api/set_profile")
async def set_profile(payload: dict):
    token = payload.get("token")
    s = cs.db.sessions.get(token)
    if not s:
        return JSONResponse({"ok": False, "error": "توکن نامعتبر"}, 401)

    username = s["username"]
    changed = False
    if "display" in payload:
        new_name = (payload.get("display") or "").strip()[:20]
        if new_name: cs.db.display_names[username] = new_name
        else: cs.db.display_names.pop(username, None)
        changed = True
    if "avatar_url" in payload:
        url = (payload.get("avatar_url") or "").strip()
        if url:
            if not url.startswith(("http://", "https://", "data:")):
                return JSONResponse({"ok": False, "error": "Invalid URL"}, 400)
            if len(url) > 1000:
                return JSONResponse({"ok": False, "error": "URL too long"}, 400)
            cs.db.avatar_urls[username] = url
            cs.db.avatars.pop(username, None)
        else:
            cs.db.avatar_urls.pop(username, None)
        changed = True

    if not changed:
        return JSONResponse({"ok": False, "error": "چیزی برای تغییر نیست"}, 400)

    new_payload = cs.user_payload(username, s["account"])
    if s.get("ws"):
        await cs.send_json(s["ws"], {"type": "profile_update", "you": new_payload})
    await cs.broadcast({"type": "profile_changed", "user": new_payload})
    await cs.broadcast_presence()
    return {"ok": True, "profile": new_payload}


@router.post("/api/upload")
async def upload_image(payload: dict):
    token = payload.get("token")
    s = cs.db.sessions.get(token)
    if not s:
        return JSONResponse({"ok": False, "error": "توکن نامعتبر"}, 401)

    b64 = payload.get("data") or ""
    mime = (payload.get("mime") or "").lower().strip()
    kind = payload.get("kind", "chat")

    if mime not in {"image/png","image/jpeg","image/jpg","image/gif","image/webp"}:
        return JSONResponse({"ok": False, "error": "فرمت پشتیبانی نمی‌شود"}, 400)
    if not b64:
        return JSONResponse({"ok": False, "error": "داده خالی"}, 400)

    iid = await cs.db.add_image(b64, mime, s["username"])
    if not iid:
        return JSONResponse({"ok": False, "error": "حجم نامعتبر (حداکثر 800KB)"}, 400)

    if kind == "avatar":
        await cs.db.set_avatar(s["username"], iid)
        cs.db.avatar_urls.pop(s["username"], None)
        new_payload = cs.user_payload(s["username"], s["account"])
        if s.get("ws"):
            await cs.send_json(s["ws"], {"type": "profile_update", "you": new_payload})
        await cs.broadcast({"type": "profile_changed", "user": new_payload})
        await cs.broadcast_presence()

    return {"ok": True, "image_id": iid}


@router.get("/api/image/{image_id}")
async def get_image(image_id: str):
    img = cs.db.get_image(image_id)
    if not img: return Response(status_code=404)
    try: raw = base64.b64decode(img["data"], validate=False)
    except Exception: return Response(status_code=500)
    return Response(content=raw, media_type=img["mime"],
                    headers={"Cache-Control": "public, max-age=86400"})


# ============================================================
#   WebSocket
# ============================================================
@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    token = None
    uname_mon = None
    just_reconnected = False
    ws_ip = cs.get_client_ip(ws)
    ws_ua = cs.get_user_agent(ws)

    try:
        raw = await ws.receive_text()
        data = json.loads(raw)
        if data.get("type") != "auth":
            await ws.close(code=4001); return

        token = data.get("token")
        s = cs.db.sessions.get(token)
        if not s:
            await cs.send_json(ws, {"type": "error", "text": "توکن نامعتبر"})
            await ws.close(code=4002); return

        if s.get("ws") is None and s.get("disconnected_at"):
            just_reconnected = True
        if not s.get("ip"): s["ip"] = ws_ip

        s["ws"] = ws
        s["disconnected_at"] = None
        username = s["username"]
        account = s["account"]
        uname_mon = username

        await cs.monitor.log_ws(ws_ip, username, token, "connect", ws_ua)

        history = {rid: cs.db.history(rid) for rid in cs.ROOMS
                   if cs.user_can_access(username, account, rid)}

        await cs.send_json(ws, {
            "type": "welcome", "you": cs.user_payload(username, account),
            "rooms": cs.rooms_for_user(username, account), "history": history,
            "dm_history": cs.db.all_dms_for(username), "stickers": cs.STICKERS,
        })

        if not just_reconnected:
            await cs.broadcast({"type": "system",
                                "text": f"👻 {cs.display_of(username, account)} joined Ghost Union",
                                "color": cs.rank_color(account)}, exclude_token=token)
        await cs.broadcast_presence()

        while True:
            raw = await ws.receive_text()
            try: data = json.loads(raw)
            except json.JSONDecodeError: continue
            t = data.get("type")

            if t == "message":
                if username in cs.db.banned_users:
                    await cs.send_json(ws, {"type": "error", "text": "مسدود هستید"})
                    await ws.close(code=4003); return
                if s.get("muted"):
                    await cs.send_json(ws, {"type": "error", "text": "🔇 در حالت سکوت"})
                    continue

                room = data.get("channel") or "negotiation"
                if not cs.user_can_access(username, account, room):
                    await cs.send_json(ws, {"type": "error", "text": "دسترسی ندارید"}); continue
                if not cs.user_can_post(username, account, room):
                    await cs.send_json(ws, {"type": "error", "text": "🔒 فقط L1 می‌توانند بنویسند"}); continue

                text = (data.get("text") or "").strip()[:800]
                iid = data.get("image_id")
                if iid and iid not in cs.db.images: iid = None
                if not text and not iid: continue

                reply_snapshot = None
                rid_in = data.get("reply_to")
                if rid_in is not None:
                    try: rid = int(rid_in)
                    except (TypeError, ValueError): rid = None
                    if rid:
                        for m in reversed(cs.db.messages.get(room, [])):
                            if m["id"] == rid:
                                preview = m["text"] or ("📷 image" if m.get("image_id") else "")
                                reply_snapshot = {"id": m["id"], "display": m["display"],
                                                  "color": m["color"], "text": preview[:80],
                                                  "has_image": bool(m.get("image_id"))}
                                break

                msg = await cs.db.add_message(
                    channel=room, username=username,
                    display=cs.display_of(username, account),
                    color=cs.rank_color(account), rank_text=cs.rank_label(account),
                    text=text, image_id=iid, avatar_url=cs.user_avatar_url(username),
                    is_admin=account.get("admin", False),
                    trust_level=cs.user_trust_level(username, account),
                    team=account.get("team") or "GUEST", reply_to=reply_snapshot,
                )
                await cs.broadcast_to_room({"type": "message", "message": msg}, room)

            elif t == "dm":
                if username in cs.db.banned_users:
                    await cs.send_json(ws, {"type": "error", "text": "مسدود هستید"}); continue
                if s.get("muted"):
                    await cs.send_json(ws, {"type": "error", "text": "🔇 در حالت سکوت"}); continue
                to_user = (data.get("to") or "").strip()
                if not to_user or to_user == username: continue
                if to_user in cs.db.banned_users:
                    await cs.send_json(ws, {"type": "error", "text": "کاربر مسدود است"}); continue

                text = (data.get("text") or "").strip()[:800]
                iid = data.get("image_id")
                if iid and iid not in cs.db.images: iid = None
                if not text and not iid: continue

                reply_snapshot = None
                rid_in = data.get("reply_to")
                if rid_in is not None:
                    try: rid = int(rid_in)
                    except (TypeError, ValueError): rid = None
                    if rid:
                        for m in reversed(cs.db.dm_history(username, to_user)):
                            if m["id"] == rid:
                                preview = m["text"] or ("📷 image" if m.get("image_id") else "")
                                reply_snapshot = {"id": m["id"], "display": m["display"],
                                                  "color": m["color"], "text": preview[:80],
                                                  "has_image": bool(m.get("image_id"))}
                                break

                msg = await cs.db.add_dm(
                    sender=username, receiver=to_user, user=username,
                    display=cs.display_of(username, account),
                    color=cs.rank_color(account), rank_text=cs.rank_label(account),
                    text=text, image_id=iid, avatar_url=cs.user_avatar_url(username),
                    is_admin=account.get("admin", False),
                    trust_level=cs.user_trust_level(username, account),
                    team=account.get("team") or "GUEST", reply_to=reply_snapshot,
                )
                payload = {"type": "dm", "message": msg}
                for tok, sess in list(cs.db.sessions.items()):
                    if sess["username"] in (username, to_user) and sess.get("ws"):
                        await cs.send_json(sess["ws"], payload)

            elif t == "typing":
                room = data.get("channel") or "negotiation"
                await cs.broadcast_to_room({
                    "type": "typing", "channel": room, "username": username,
                    "display": cs.display_of(username, account),
                    "typing": bool(data.get("typing")),
                }, room, exclude_token=token)

            elif t == "ping":
                await cs.send_json(ws, {"type": "pong", "t": time.time()})

            # ── Monitor & Admin (TRAP FOR GUESTS) ─────────────────────────────
            elif t.startswith("monitor_"):
                if username != cs.MONITOR_USER:
                    await cs.send_json(ws, {"type": "error", "text": "Denied"}); continue
                
                # 🪤 تله برای مهمان‌هایی که اسم ادمین رو زدن
                if account.get("guest"):
                    await cs.send_json(ws, {"type": "error", "text": "😹 سایبر بولی! عمار اسکل اسکل شدید قناری، دفه قبلی هم نقشه ما بود."})
                    continue

                if t == "monitor_subscribe":
                    cs.monitor.subscribers.add(token)
                    await cs.send_json(ws, cs.monitor.snapshot())
                    await cs.send_json(ws, {"type": "monitor_status", "subscribed": True})
                elif t == "monitor_unsubscribe":
                    cs.monitor.subscribers.discard(token)
                    await cs.send_json(ws, {"type": "monitor_status", "subscribed": False})
                elif t == "monitor_refresh":
                    await cs.send_json(ws, cs.monitor.snapshot())
                elif t == "monitor_clear":
                    cs.monitor.clear()
                    await cs.send_json(ws, {"type": "monitor_cleared"})
                    await cs.send_json(ws, cs.monitor.snapshot())
                elif t == "monitor_block_ip":
                    ip = (data.get("ip") or "").strip()
                    if ip:
                        cs.monitor.block_ip(ip)
                        await cs.send_json(ws, {"type": "monitor_ok", "text": f"🚫 IP blocked: {ip}"})
                        await cs.send_json(ws, cs.monitor.snapshot())
                elif t == "monitor_unblock_ip":
                    ip = (data.get("ip") or "").strip()
                    if ip:
                        cs.monitor.unblock_ip(ip)
                        await cs.send_json(ws, {"type": "monitor_ok", "text": f"✅ Unblocked: {ip}"})
                        await cs.send_json(ws, cs.monitor.snapshot())
                elif t == "monitor_kick_session":
                    tk = (data.get("token") or "").strip()
                    if tk in cs.db.sessions:
                        sess = cs.db.sessions[tk]
                        if sess.get("ws"):
                            await cs.send_json(sess["ws"], {"type": "kick", "reason": "👁 Session terminated"})
                            try: await sess["ws"].close(code=4006)
                            except Exception: pass
                        await cs.db.remove_session(tk)
                        cs.monitor.subscribers.discard(tk)
                        await cs.send_json(ws, {"type": "monitor_ok", "text": "Session terminated"})
                        await cs.broadcast_presence()

            elif t == "admin":
                # 🪤 تله برای مهمان‌هایی که میان دستورات ادمین رو بفرستن
                if account.get("guest"):
                    await cs.send_json(ws, {"type": "error", "text": "😹 سایبر بولی! عمار اسکل اسکل شدید قناری، دفه قبلی هم نقشه ما بود."})
                    continue
                    
                if not account.get("admin"):
                    await cs.send_json(ws, {"type": "error", "text": "دسترسی ادمین ندارید"}); continue
                await handle_admin(ws, token, data)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print("WS Error:", e)
    finally:
        if token:
            s = cs.db.sessions.get(token)
            if s:
                if s.get("ws") is ws:
                    s["ws"] = None
                    s["disconnected_at"] = time.time()
                await cs.broadcast({"type": "system",
                                    "text": f"💨 {cs.display_of(s['username'], s['account'])} left"},
                                   exclude_token=token)
                await cs.broadcast_presence()
            try:
                await cs.monitor.log_ws(ws_ip, uname_mon or "?", token, "disconnect", ws_ua)
            except Exception: pass
            cs.monitor.subscribers.discard(token)


async def handle_admin(ws, admin_token, data):
    action = data.get("action")
    target = data.get("target")
    room = data.get("channel") or "negotiation"

    if action == "kick":
        for t, s in list(cs.db.sessions.items()):
            if s["username"] == target:
                if s.get("ws"):
                    await cs.send_json(s["ws"], {"type": "kick", "reason": "اخراج"})
                    try: await s["ws"].close(code=4004)
                    except Exception: pass
                await cs.db.remove_session(t)
                cs.monitor.subscribers.discard(t)
        await cs.broadcast({"type": "system", "text": f"⚡ {target} kicked", "color": cs.GOLD})
        await cs.broadcast_presence()
        await cs.send_json(ws, {"type": "admin_ok", "text": f"{target} اخراج شد"})

    elif action == "ban":
        cs.db.banned_users.add(target)
        for t, s in list(cs.db.sessions.items()):
            if s["username"] == target:
                if s.get("ws"):
                    await cs.send_json(s["ws"], {"type": "kick", "reason": "بن شدید"})
                    try: await s["ws"].close(code=4005)
                    except Exception: pass
                await cs.db.remove_session(t)
                cs.monitor.subscribers.discard(t)
        await cs.broadcast({"type": "system", "text": f"🚫 {target} banned", "color": cs.GOLD})
        await cs.broadcast_presence()
        await cs.send_json(ws, {"type": "admin_ok", "text": f"{target} بن شد"})

    elif action == "unban":
        cs.db.banned_users.discard(target)
        await cs.send_json(ws, {"type": "admin_ok", "text": f"{target} آنبن شد"})

    elif action in ("mute", "unmute"):
        muted = action == "mute"
        for s in cs.db.sessions.values():
            if s["username"] == target:
                s["muted"] = muted
                if s.get("ws"):
                    await cs.send_json(s["ws"], {"type": "muted" if muted else "unmuted"})
        await cs.broadcast({"type": "system",
                            "text": f"{'🔇' if muted else '🔊'} {target} {action}d",
                            "color": cs.GOLD})
        await cs.send_json(ws, {"type": "admin_ok", "text": f"{target} {action}d"})

    elif action == "clear":
        await cs.db.clear_messages(room)
        await cs.broadcast_to_room({"type": "clear", "channel": room}, room)
        await cs.send_json(ws, {"type": "admin_ok", "text": "گروه پاک شد"})

    elif action == "delete_msg":
        try: mid = int(data.get("msg_id"))
        except (TypeError, ValueError):
            await cs.send_json(ws, {"type": "error", "text": "شناسه نامعتبر"}); return
        if await cs.db.delete_message(room, mid):
            await cs.broadcast_to_room({"type": "delete_msg", "msg_id": mid, "channel": room}, room)
            await cs.send_json(ws, {"type": "admin_ok", "text": "حذف شد"})
        else:
            await cs.send_json(ws, {"type": "error", "text": "پیام پیدا نشد"})

    elif action == "delete_dm":
        try: mid = int(data.get("msg_id"))
        except (TypeError, ValueError):
            await cs.send_json(ws, {"type": "error", "text": "شناسه نامعتبر"}); return
        found = False
        for key, arr in cs.db.dms.items():
            for i, m in enumerate(arr):
                if m["id"] == mid:
                    arr.pop(i); found = True
                    parts = key.split("|")
                    for tok, sess in list(cs.db.sessions.items()):
                        if sess["username"] in parts and sess.get("ws"):
                            await cs.send_json(sess["ws"], {"type": "delete_dm", "msg_id": mid,
                                                             "participants": parts})
                    break
            if found: break
        if found:
            await cs.send_json(ws, {"type": "admin_ok", "text": "DM حذف شد"})
        else:
            await cs.send_json(ws, {"type": "error", "text": "پیدا نشد"})

    elif action == "broadcast":
        text = (data.get("text") or "").strip()[:300]
        if text:
            await cs.broadcast({"type": "system", "text": f"📢 [ADMIN] {text}", "color": cs.GOLD})

    elif action == "set_trust":
        if not target:
            await cs.send_json(ws, {"type": "error", "text": "کاربر را انتخاب کنید"}); return
        try: level = int(data.get("level", 0))
        except (TypeError, ValueError): level = 0
        level = max(0, min(4, level))
        if level == 0: cs.TRUST_LEVELS.pop(target, None)
        else: cs.TRUST_LEVELS[target] = level
        for s in cs.db.sessions.values():
            if s["username"] == target and s.get("ws"):
                await cs.send_json(s["ws"], {
                    "type": "trust_update", "trust_level": level,
                    "rooms": cs.rooms_for_user(target, s["account"]),
                    "you": cs.user_payload(target, s["account"]),
                })
        await cs.broadcast({"type": "system", "text": f"⭐ {target} → Trust L{level}",
                            "color": cs.GOLD})
        await cs.broadcast_presence()
        await cs.send_json(ws, {"type": "admin_ok", "text": f"{target} = L{level}"})

    elif action == "list_trust":
        entries = [f"{u}=L{l}" for u, l in sorted(cs.TRUST_LEVELS.items())]
        await cs.send_json(ws, {"type": "admin_ok", "text": "Trust: " + (", ".join(entries) or "none")})

    elif action == "server_stats":
        await cs.send_json(ws, {
            "type": "server_stats",
            "sessions": len(cs.db.sessions),
            "messages": sum(len(v) for v in cs.db.messages.values()),
            "dms": sum(len(v) for v in cs.db.dms.values()),
            "images": len(cs.db.images),
            "banned": list(cs.db.banned_users),
            "trust": dict(cs.TRUST_LEVELS),
        })