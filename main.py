"""👻 Ghost Union v4 — REST API edition."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from routers import blog_api, blog_pages, chat_api, chat_pages, joke, download
from services import content_service, chat_service

ASSETS_DIR = Path("assets")
for sub in ("avatars", "groups", "stickers"):
    (ASSETS_DIR / sub).mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("👻 Booting Ghost Union...")
    await content_service.on_startup()
    await chat_service.on_startup()
    print("👻 Ready · /  /chat  /admin")
    yield


app = FastAPI(title="Ghost Union", lifespan=lifespan)

app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Order: first-added = outermost. Chat's IP-block must win → add chat first.
chat_service.setup_middleware(app)
content_service.setup_middleware(app)

app.include_router(joke.router)
app.include_router(blog_api.router)
app.include_router(blog_pages.router)
app.include_router(chat_api.router)
app.include_router(chat_pages.router)
app.include_router(download.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)