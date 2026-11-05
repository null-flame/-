"""Fake admin login — the joke that replaces the old admin panel.

The password AND the message are ONLY in this file (server-side).
The client never sees them until /api/joke-login succeeds.
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

_JOKE_PASSWORD = "12345678"
_JOKE_SUCCESS   = "عمار جون خوشگل بچه شدی فکر کردی واقعا پنل ادمینه؟؟"
_JOKE_WRONG     = "رمز اشتباه است"


@router.get("/admin")
async def joke_page():
    return FileResponse("templates/joke/index.html")


@router.post("/api/joke-login")
async def joke_login(payload: dict):
    password = (payload.get("password") or "").strip()
    if password == _JOKE_PASSWORD:
        return JSONResponse({"ok": True, "message": _JOKE_SUCCESS})
    return JSONResponse({"ok": False, "error": _JOKE_WRONG})