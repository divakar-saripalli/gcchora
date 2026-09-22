from fastapi import FastAPI

from app.routers import ads, auth, chat, me, profiles, rectify

app = FastAPI(title="GoogleHora API", version="0.1.0")

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(profiles.router)
app.include_router(rectify.router)
app.include_router(chat.router)
app.include_router(ads.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
