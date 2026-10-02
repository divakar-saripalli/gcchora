"""Centralized configuration, read from environment variables (.env in local dev)."""
import os
from dotenv import load_dotenv

load_dotenv(override=True)


class Settings:
    mongodb_uri: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    mongodb_db_name: str = os.getenv("MONGODB_DB_NAME", "googlehora")

    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")

    jwt_secret: str = os.getenv("JWT_SECRET", "insecure-dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))

    default_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "gemini")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    free_questions_initial: int = int(os.getenv("FREE_QUESTIONS_INITIAL", "5"))

    ad_ssv_shared_secret: str = os.getenv("AD_SSV_SHARED_SECRET", "")
    # Dev/QA-only escape hatch: the app doesn't have a real rewarded-ad SDK
    # wired up yet, so this lets a tester credit themselves an ad-unlock
    # without one -- see app/routers/ads.py:test_credit. Must stay false in
    # any real deployment; the real reward-callback endpoint is unaffected
    # by this flag either way.
    allow_test_ad_bypass: bool = os.getenv("ALLOW_TEST_AD_BYPASS", "false").lower() == "true"

    # Dev/debug aid: append every exact prompt sent to an LLM provider to a
    # local text file, so it can be copy-pasted and tested directly against
    # the provider's own UI (e.g. Gemini's web app or AI Studio) independent
    # of this backend. See app/llm_gateway.py:_log_prompt. On by default
    # since this is pre-production; set LOG_LLM_PROMPTS=false to disable
    # once this is no longer needed day-to-day (the log can contain user
    # life-event details, so don't leave it on carelessly in a real
    # deployment).
    log_llm_prompts: bool = os.getenv("LOG_LLM_PROMPTS", "true").lower() == "true"
    llm_prompt_log_path: str = os.getenv("LLM_PROMPT_LOG_PATH", "logs/llm_prompts.log")


settings = Settings()
