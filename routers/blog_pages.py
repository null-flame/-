from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/")
async def chat():
    return FileResponse("templates/blog/index.html")