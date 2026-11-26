import time

import pytest
from fastapi import HTTPException
from jose import jwt

from app import rate_limit, security
from app.config import settings


def test_rate_limit_blocks_then_recovers_after_the_window():
    rate_limit.reset()
    for i in range(3):
        rate_limit._check("k", max_calls=3, window_seconds=60, now=100.0 + i)
    with pytest.raises(HTTPException) as exc:
        rate_limit._check("k", max_calls=3, window_seconds=60, now=110.0)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) > 0
    rate_limit._check("other-user", max_calls=3, window_seconds=60, now=110.0)  # separate key
    rate_limit._check("k", max_calls=3, window_seconds=60, now=161.0)  # oldest call has aged out


def test_token_carries_issued_at_and_decodes():
    token = security.create_access_token("u1")
    claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert abs(claims["iat"] - time.time()) < 5
    assert security.decode_access_token(token) == "u1"


def test_revocation_cutoff():
    assert not security.token_is_revoked({"iat": 100}, {})
    assert security.token_is_revoked({"iat": 100}, {"tokens_invalid_before": 101})
    assert not security.token_is_revoked({"iat": 101}, {"tokens_invalid_before": 101})
    assert security.token_is_revoked({}, {"tokens_invalid_before": 101})  # token from before iat existed


def test_tampered_token_is_rejected():
    with pytest.raises(HTTPException):
        security.decode_access_token(security.create_access_token("u1")[:-2] + "xx")
