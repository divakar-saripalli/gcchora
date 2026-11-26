import pytest
from cryptography.fernet import Fernet

from app import field_encryption as fe
from app.config import settings
from app.field_encryption import EncryptedFieldsCollection, FieldDecryptionError, decrypt_str, encrypt_str
from tests.conftest import run
from tests.fake_mongo import FakeCollection


def make(raw=None):
    raw = raw or FakeCollection()
    wrapped = EncryptedFieldsCollection(
        raw, string_fields=("birth_time_24h", "notes"), json_fields=("geographic_coordinates",)
    )
    return raw, wrapped


def test_roundtrip_uses_versioned_format():
    token = encrypt_str("14:30:00")
    assert token.startswith("v1:gAAAA")
    assert decrypt_str(token) == "14:30:00"


def test_insert_encrypts_only_tracked_fields():
    raw, wrapped = make()
    run(wrapped.insert_one({
        "_id": "p1", "birth_time_24h": "14:30:00", "label": "Self",
        "geographic_coordinates": {"latitude": 12.9, "longitude": 77.6},
    }))
    stored = raw.docs[0]
    assert stored["label"] == "Self"
    assert "14:30" not in stored["birth_time_24h"]
    assert isinstance(stored["geographic_coordinates"], str) and "12.9" not in stored["geographic_coordinates"]

    got = run(wrapped.find_one({"_id": "p1"}))
    assert got["birth_time_24h"] == "14:30:00"
    assert got["geographic_coordinates"] == {"latitude": 12.9, "longitude": 77.6}


def test_update_set_encrypts_tracked_fields_and_leaves_the_rest():
    raw, wrapped = make()
    run(wrapped.insert_one({"_id": "p1", "birth_time_24h": "14:30:00"}))
    run(wrapped.update_one({"_id": "p1"}, {"$set": {"birth_time_24h": "09:15:00", "calibration": {"locked": True}}}))
    assert raw.docs[0]["calibration"] == {"locked": True}
    assert raw.docs[0]["birth_time_24h"].startswith("v1:")
    assert run(wrapped.find_one({"_id": "p1"}))["birth_time_24h"] == "09:15:00"


def test_cursor_decrypts_through_sort_limit_and_async_iteration():
    raw, wrapped = make()
    for i, note in enumerate(["third", "first", "second"]):
        run(wrapped.insert_one({"_id": f"e{i}", "profileId": "p1", "notes": note, "order": [3, 1, 2][i]}))

    docs = run(wrapped.find({"profileId": "p1"}).sort("order", 1).limit(2).to_list(length=10))
    assert [d["notes"] for d in docs] == ["first", "second"]

    async def collect():
        return [d["notes"] async for d in wrapped.find({"profileId": "p1"}).sort("order", 1)]

    assert run(collect()) == ["first", "second", "third"]


def test_legacy_plaintext_and_legacy_subdocument_read_back_unchanged():
    raw, wrapped = make()
    raw.docs.append({
        "_id": "old", "birth_time_24h": "06:45:00", "notes": "v1: my first note",
        "geographic_coordinates": {"latitude": 17.4, "longitude": 78.5},
    })
    got = run(wrapped.find_one({"_id": "old"}))
    assert got["birth_time_24h"] == "06:45:00"
    assert got["notes"] == "v1: my first note"
    assert got["geographic_coordinates"] == {"latitude": 17.4, "longitude": 78.5}


def test_bare_token_from_before_the_version_prefix_still_decrypts():
    bare = Fernet(settings.field_encryption_key.encode()).encrypt(b"hello").decode()
    assert decrypt_str(bare) == "hello"


def test_wrong_key_raises_instead_of_returning_ciphertext(monkeypatch):
    token = encrypt_str("secret")
    monkeypatch.setattr(settings, "field_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(fe, "_ciphers", {})
    with pytest.raises(FieldDecryptionError):
        decrypt_str(token)


def test_missing_key_fails_loudly(monkeypatch):
    monkeypatch.setattr(settings, "field_encryption_key", "")
    monkeypatch.setattr(fe, "_ciphers", {})
    with pytest.raises(fe.FieldEncryptionNotConfigured):
        encrypt_str("anything")


def test_unsupported_collection_methods_are_refused_and_safe_ones_pass_through():
    raw, wrapped = make()
    run(wrapped.insert_one({"_id": "p1", "notes": "x"}))
    assert run(wrapped.count_documents({})) == 1
    with pytest.raises(AttributeError):
        wrapped.insert_many
    assert run(wrapped.delete_many({})).deleted_count == 1
