from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/")
async def root():
    return FileResponse("templates/blog/index.html")

@router.get("/post/{name}")
async def post(name: str):
    return FileResponse("templates/blog/index.html")