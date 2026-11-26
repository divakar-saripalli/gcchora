import pytest

import main
from app.config import DEFAULT_JWT_SECRET, settings


def test_dev_tolerates_insecure_defaults(monkeypatch):
    monkeypatch.setattr(settings, "environment", "dev")
    monkeypatch.setattr(settings, "jwt_secret", DEFAULT_JWT_SECRET)
    main.check_startup_config()


def test_non_dev_refuses_insecure_settings(monkeypatch):
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "jwt_secret", DEFAULT_JWT_SECRET)
    monkeypatch.setattr(settings, "allow_test_ad_bypass", True)
    with pytest.raises(RuntimeError) as exc:
        main.check_startup_config()
    assert "JWT_SECRET" in str(exc.value) and "ALLOW_TEST_AD_BYPASS" in str(exc.value)


def test_non_dev_starts_when_configured_safely(monkeypatch):
    monkeypatch.setattr(settings, "environment", "prod")
    monkeypatch.setattr(settings, "jwt_secret", "x" * 40)
    monkeypatch.setattr(settings, "allow_test_ad_bypass", False)
    monkeypatch.setattr(settings, "log_llm_prompts", False)
    main.check_startup_config()
