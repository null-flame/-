"""Same-origin download proxy."""
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()

_PRIVATE = ("10.","127.","169.254.","192.168.",
            "172.16.","172.17.","172.18.","172.19.","172.20.","172.21.",
            "172.22.","172.23.","172.24.","172.25.","172.26.","172.27.",
            "172.28.","172.29.","172.30.","172.31.")


@router.get("/download")
async def download_proxy(url: str = "", name: str = ""):
    if not url: raise HTTPException(400, "Missing url")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Invalid scheme")
    host = (parsed.hostname or "").lower()
    if (not host or host in ("localhost", "0.0.0.0", "::1")
            or host.startswith(_PRIVATE)):
        raise HTTPException(403, "Forbidden host")

    if not name:
        name = url.split("/")[-1].split("?")[0] or "download"
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:120] or "download"
    if "." not in safe:
        low = url.lower()
        for ext in (".ogg",".oga",".opus",".mp3",".wav",".m4a",".aac",".flac",
                    ".weba",".mp4",".webm",".mov",".jpg",".jpeg",".png",".gif",".webp"):
            if ext in low: safe += ext; break
        else: safe += ".bin"

    def stream():
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept": "*/*",
                "Referer": f"{parsed.scheme}://{host}/",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk: return
                    yield chunk
        except Exception as e:
            print(f"⚠️  Download error: {e}")

    return StreamingResponse(stream(), media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{safe}"',
        "Cache-Control": "public, max-age=3600",
        "X-Accel-Buffering": "no",
    })