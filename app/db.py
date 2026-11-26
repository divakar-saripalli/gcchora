"""MongoDB connection (Motor async driver) and collection accessors.

Kept deliberately thin: no ODM layer, just typed helpers around a handful of
collections. See the design doc's "MongoDB data model" section for the
schema each of these is expected to hold.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings
from app.field_encryption import EncryptedFieldsCollection

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_uri)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[settings.mongodb_db_name]


def users_collection():
    return get_db()["users"]


def birth_profiles_collection():
    # Encrypted at rest: exact birth time and geographic coordinates, for
    # both the profile's own subject and (when present) their spouse.
    # See app/field_encryption.py for what this does and doesn't cover.
    return EncryptedFieldsCollection(
        get_db()["birth_profiles"],
        string_fields=("birth_time_24h", "spouse_birth_time_24h"),
        json_fields=("geographic_coordinates", "spouse_geographic_coordinates"),
    )


def life_events_collection():
    # Encrypted at rest: free-text notes, which can contain health or
    # other sensitive personal detail. See app/field_encryption.py.
    return EncryptedFieldsCollection(get_db()["life_events"], string_fields=("notes",))


def conversations_collection():
    return get_db()["conversations"]


def messages_collection():
    # Encrypted at rest: full chat message content. See
    # app/field_encryption.py.
    return EncryptedFieldsCollection(get_db()["messages"], string_fields=("content",))


def significator_rules_collection():
    return get_db()["significator_rules"]


def ad_reward_events_collection():
    return get_db()["ad_reward_events"]


def rectification_ratings_collection():
    return get_db()["rectification_ratings"]


def device_tokens_collection():
    return get_db()["device_tokens"]


async def ensure_indexes() -> None:
    """Creates the indexes the app's queries rely on. Safe to call on
    every startup: creating an index that already exists is a no-op.

    Without these, every lookup by owner (a user's profiles, a profile's
    life events, a conversation's messages) scans the whole collection,
    which is unnoticeable with a handful of test users and slow with real
    volume. Created on the raw collections -- index definitions involve
    no document content, so the encryption wrapper has nothing to do."""
    db = get_db()
    await db["users"].create_index("googleId")
    await db["birth_profiles"].create_index([("userId", 1), ("createdAt", 1)])
    await db["life_events"].create_index("profileId")
    await db["conversations"].create_index([("userId", 1), ("lastMessageAt", -1)])
    await db["messages"].create_index([("conversationId", 1), ("createdAt", 1)])
    await db["rectification_ratings"].create_index("profileId")
    await db["ad_reward_events"].create_index("rewardToken")
    await db["device_tokens"].create_index("userId")
