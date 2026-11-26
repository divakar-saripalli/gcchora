"""Centralized configuration, read from environment variables (.env in local dev)."""
import os
from dotenv import load_dotenv

load_dotenv(override=True)


# The placeholder JWT secret. Fine for local development; main.py refuses
# to start with it when ENVIRONMENT is anything other than "dev".
DEFAULT_JWT_SECRET = "insecure-dev-secret-change-me"


class Settings:
    mongodb_uri: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    mongodb_db_name: str = os.getenv("MONGODB_DB_NAME", "googlehora")

    google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")

    jwt_secret: str = os.getenv("JWT_SECRET", DEFAULT_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "43200"))

    default_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "gemini")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    free_questions_initial: int = int(os.getenv("FREE_QUESTIONS_INITIAL", "5"))

    # Full rectification runs (each one calls every configured LLM
    # provider) allowed per user per calendar day. Rectification used to
    # be completely unmetered while chat was quota-gated, even though a
    # rectification run costs more than a chat message. See
    # app/quota.py:consume_rectify_run.
    rectify_daily_limit: int = int(os.getenv("RECTIFY_DAILY_LIMIT", "100"))

    # "dev" (default) tolerates insecure defaults such as the placeholder
    # JWT secret. Anything else ("staging", "prod") makes the app refuse
    # to start with them -- see main.py's startup checks.
    environment: str = os.getenv("ENVIRONMENT", "dev").lower()

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
    # of this backend. See app/llm_gateway.py:_log_prompt.
    #
    # Defaults to OFF now (was on by default earlier this session) -- this
    # file is a full, unencrypted copy of every birth detail, life event,
    # and chat message ever sent to an LLM, written to plain disk outside
    # the database entirely. Turn it on only while actively debugging a
    # prompt issue, and don't leave it on in anything resembling a real
    # deployment. Set LOG_LLM_PROMPTS=true to opt back in temporarily.
    log_llm_prompts: bool = os.getenv("LOG_LLM_PROMPTS", "false").lower() == "true"
    llm_prompt_log_path: str = os.getenv("LLM_PROMPT_LOG_PATH", "logs/llm_prompts.log")

    # Field-level encryption key (see app/field_encryption.py) for the
    # most sensitive stored fields: a profile's own and spouse's exact
    # birth time/coordinates, a life event's free-text notes (which can
    # contain health details), and chat message content. Must be a
    # Fernet key -- generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Required for the app to start encrypting/decrypting these fields;
    # see app/field_encryption.py for what happens if it's missing
    # (fails loudly rather than silently falling back to plaintext).
    field_encryption_key: str = os.getenv("FIELD_ENCRYPTION_KEY", "")
    # Previous keys still accepted for DECRYPTING (comma-separated), so a
    # key can be rotated: put the new key in FIELD_ENCRYPTION_KEY and the
    # old one here, then run scripts/reencrypt_fields.py.
    field_encryption_old_keys: str = os.getenv("FIELD_ENCRYPTION_OLD_KEYS", "")

    # LLM provider models and timeouts (seconds) -- previously literals in
    # app/llm_gateway.py.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    llm_chat_timeout_seconds: float = float(os.getenv("LLM_CHAT_TIMEOUT_SECONDS", "30"))
    llm_structured_timeout_seconds: float = float(os.getenv("LLM_STRUCTURED_TIMEOUT_SECONDS", "45"))

    # Push notifications (Firebase Cloud Messaging). Off by default so a
    # fresh checkout without the credentials file still runs normally --
    # app.push.send_push() just logs and no-ops when this is False, same
    # pattern as allow_test_ad_bypass above. The credentials file is the
    # *service account* key (Project settings -> Service accounts ->
    # Generate new private key), not google-services.json -- that one's
    # for the Flutter app, this one's for the server to send pushes.
    # Never commit it: see .gitignore (firebase-service-account.json).
    enable_push_notifications: bool = os.getenv("ENABLE_PUSH_NOTIFICATIONS", "false").lower() == "true"
    firebase_credentials_path: str = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase-service-account.json")


settings = Settings()
