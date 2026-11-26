import httpx
import pytest
from pydantic import ValidationError

from app import models
from app.llm_gateway import _raise_for_status_with_body, _sanitize_url_for_log, summarize_provider_error


def test_request_models_cap_free_text():
    with pytest.raises(ValidationError):
        models.MessageCreate(content="x" * (models.MAX_CHAT_MESSAGE_LEN + 1))
    with pytest.raises(ValidationError):
        models.LifeEventIn(category="OTHER", exact_date="2020-01-01", notes="x" * (models.MAX_NOTES_LEN + 1))
    with pytest.raises(ValidationError):
        models.RectifyAnswer(category="X" * (models.MAX_CATEGORY_LEN + 1))
    models.MessageCreate(content="x" * models.MAX_CHAT_MESSAGE_LEN)


def test_response_models_can_still_return_older_long_records():
    models.LifeEventOut(id="e1", category="X" * 500, notes="x" * 10_000)
    models.BirthProfileOut(
        id="p1", label="L" * 500, birth_date="1990-01-01", birth_time_24h="10:00:00",
        geographic_coordinates={"latitude": 1, "longitude": 2, "timezone_offset_hours": 5.5},
    )


def test_provider_errors_never_carry_the_api_key_or_response_body():
    url = "https://generativelanguage.googleapis.com/v1/models/x:generateContent?key=AIzaSECRET"
    assert _sanitize_url_for_log(url).endswith(":generateContent")
    response = httpx.Response(400, request=httpx.Request("POST", url), text="echo: born 14:32 at 12.9,77.6")
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _raise_for_status_with_body(response)
    assert "AIzaSECRET" not in str(exc.value)
    summary = summarize_provider_error(exc.value)
    assert summary == "HTTP 400 error"
    assert summarize_provider_error(RuntimeError("born 14:32")) == "request failed"
