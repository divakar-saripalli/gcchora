import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import DEFAULT_JWT_SECRET, settings
from app.db import ensure_indexes
from app.routers import ads, auth, chat, me, profiles, rectify

logger = logging.getLogger(__name__)


def check_startup_config() -> None:
    """Refuses to start a non-dev deployment on insecure defaults. In
    "dev" (the default ENVIRONMENT) these are tolerated so local testing
    needs no extra setup; anywhere else, a forgotten setting should stop
    the app at startup rather than surface later as forgeable sessions
    or a free bypass of the question quota."""
    if settings.environment == "dev":
        return
    problems = []
    if settings.jwt_secret == DEFAULT_JWT_SECRET or len(settings.jwt_secret) < 32:
        problems.append("JWT_SECRET must be set to a long random value (32+ characters)")
    if settings.allow_test_ad_bypass:
        problems.append("ALLOW_TEST_AD_BYPASS must be false")
    if not settings.field_encryption_key:
        problems.append("FIELD_ENCRYPTION_KEY must be set")
    if settings.log_llm_prompts:
        problems.append("LOG_LLM_PROMPTS must be false")
    if problems:
        raise RuntimeError(
            f"Refusing to start with ENVIRONMENT={settings.environment}: " + "; ".join(problems) + "."
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_startup_config()
    try:
        await ensure_indexes()
    except Exception as exc:
        # The app can still serve requests without indexes (just more
        # slowly), so a database that is unreachable at startup shouldn't
        # stop it from coming up. Only the error type is logged -- driver
        # messages can include connection details.
        logger.warning("Could not create database indexes at startup (%s).", type(exc).__name__)
    yield
    from app.llm_gateway import close_http_client

    await close_http_client()


app = FastAPI(title="GoogleHora API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(me.router)
app.include_router(profiles.router)
app.include_router(rectify.router)
app.include_router(chat.router)
app.include_router(ads.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
