"""Tests for the architecture-review fixes: request validation, atomic
calibration updates, key rotation, and the background recheck."""
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app import field_encryption as fe
from app.config import settings
from app.models import BirthProfileCreate, BirthProfileOut, LifeEventIn, LifeEventOut
from tests.conftest import run
from tests.fake_mongo import FakeCollection


def _profile(**over):
    base = dict(
        birth_date="1990-07-01",
        birth_time_24h="14:30:00",
        geographic_coordinates={"latitude": 40.7, "longitude": -74.0, "timezone_offset_hours": -4},
    )
    base.update(over)
    return base


def test_profile_validation_accepts_good_and_rejects_bad_input():
    BirthProfileCreate(**_profile())
    for bad in (
        _profile(birth_date="1990-02-31"),
        _profile(birth_date="2999-01-01"),
        _profile(birth_time_24h="25:00:00"),
        _profile(geographic_coordinates={"latitude": 95, "longitude": 0, "timezone_offset_hours": 0}),
        _profile(geographic_coordinates={"latitude": 0, "longitude": 0, "timezone_offset_hours": 30}),
        _profile(birth_time_uncertainty_minutes=-5),
    ):
        with pytest.raises(ValidationError):
            BirthProfileCreate(**bad)


def test_response_models_stay_lenient_for_already_stored_data():
    out = BirthProfileOut(
        id="p", birth_date="1990-02-31", birth_time_24h="99:99",
        geographic_coordinates={"latitude": 95, "longitude": 0, "timezone_offset_hours": 0},
        birth_time_uncertainty_minutes=5000,
    )
    assert out.birth_date == "1990-02-31"
    ev = LifeEventOut(id="e", category="x", exact_date="not-a-date", impact_weight_score=99)
    assert ev.impact_weight_score == 99


def test_life_event_validation():
    LifeEventIn(category="marriage", exact_date="2015-06-01", impact_weight_score=2)
    with pytest.raises(ValidationError):
        LifeEventIn(category="marriage", exact_date="2015-13-01")
    with pytest.raises(ValidationError):
        LifeEventIn(category="marriage", exact_date="2015-06-01", impact_weight_score=50)


def test_old_key_still_decrypts_after_rotation(monkeypatch):
    old_key = settings.field_encryption_key
    token = fe.encrypt_str("14:30:00")
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "field_encryption_key", new_key)
    monkeypatch.setattr(settings, "field_encryption_old_keys", old_key)
    fe._ciphers.clear()
    fe._old_cache.clear()
    try:
        assert fe.decrypt_str(token) == "14:30:00"
        fresh = fe.encrypt_str("14:30:00")
        monkeypatch.setattr(settings, "field_encryption_old_keys", "")
        fe._old_cache.clear()
        assert fe.decrypt_str(fresh) == "14:30:00"  # new key alone is enough
        with pytest.raises(fe.FieldDecryptionError):
            fe.decrypt_str(token)  # old key dropped -> loud failure
    finally:
        fe._ciphers.clear()
        fe._old_cache.clear()


def test_push_and_unset_operators_in_fake_collection():
    col = FakeCollection()
    run(col.insert_one({"_id": "p", "calibration": {"history": [1], "pending_candidates": [9]}}))
    run(col.update_one({"_id": "p"}, {"$push": {"calibration.history": 2}, "$unset": {"calibration.pending_candidates": ""}}))
    run(col.update_one({"_id": "p"}, {"$addToSet": {"calibration.cats": "a"}}))
    run(col.update_one({"_id": "p"}, {"$addToSet": {"calibration.cats": "a"}}))
    doc = run(col.find_one({"_id": "p"}))
    assert doc["calibration"]["history"] == [1, 2]
    assert "pending_candidates" not in doc["calibration"]
    assert doc["calibration"]["cats"] == ["a"]


def test_background_recheck_swallows_failures(db, monkeypatch):
    from app.routers import rectify

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(rectify, "_metered_rectification", boom)
    run(rectify.background_recheck("p", "u"))  # must not raise
