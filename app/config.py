"""Centralized configuration, read from environment variables (.env in local dev)."""
import os
from dotenv import load_dotenv

load_dotenv()


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


settings = Settings()
