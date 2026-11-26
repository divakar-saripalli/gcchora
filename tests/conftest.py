"""Shared test setup. Environment variables are set BEFORE anything from
`app` is imported, because app.config reads them once at import time --
and app.config calls load_dotenv(override=True), so a developer's real
.env would otherwise leak real keys and settings into the tests. Patching
load_dotenv to a no-op first keeps the suite independent of it."""
import asyncio
import os

import dotenv
from cryptography.fernet import Fernet

dotenv.load_dotenv = lambda *args, **kwargs: False

os.environ.update({
    "MONGODB_URI": "mongodb://unused-in-tests",
    "MONGODB_DB_NAME": "test",
    "JWT_SECRET": "test-secret-that-is-long-enough-for-the-startup-check",
    "GOOGLE_CLIENT_ID": "test-client-id",
    "FIELD_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "FREE_QUESTIONS_INITIAL": "5",
    "RECTIFY_DAILY_LIMIT": "3",
    "ENVIRONMENT": "dev",
    "LOG_LLM_PROMPTS": "false",
    "ALLOW_TEST_AD_BYPASS": "false",
    "GEMINI_API_KEY": "",
    "OPENAI_API_KEY": "",
})

import pytest  # noqa: E402

from tests.fake_mongo import FakeDB  # noqa: E402


def run(coro):
    """Runs one coroutine to completion. The tests are plain functions
    that call this, rather than async tests, so no pytest plugin is
    needed."""
    return asyncio.run(coro)


@pytest.fixture
def db(monkeypatch):
    """A fresh in-memory database for each test, swapped in for the real
    MongoDB connection."""
    import app.db
    from app import rate_limit

    fake = FakeDB()
    monkeypatch.setattr(app.db, "get_db", lambda: fake)
    rate_limit.reset()
    return fake
