from __future__ import annotations

from fastapi import FastAPI

from routers import admin as admin_router
from routers import auth as auth_router
from routers import me as me_router
from routers import platform as platform_router

app = FastAPI(title="OpenViking Platform v0.1 Spike", version="0.1.0")

app.include_router(auth_router.router)
app.include_router(me_router.router)
app.include_router(admin_router.router)
app.include_router(platform_router.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
