"""Serve the chat SPA shell — no HTML in Python."""
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/chat")
async def chat():
    return FileResponse("templates/chat/index.html")