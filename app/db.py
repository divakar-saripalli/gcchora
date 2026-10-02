"""MongoDB connection (Motor async driver) and collection accessors.

Kept deliberately thin: no ODM layer, just typed helpers around a handful of
collections. See the design doc's "MongoDB data model" section for the
schema each of these is expected to hold.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

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
    return get_db()["birth_profiles"]


def life_events_collection():
    return get_db()["life_events"]


def conversations_collection():
    return get_db()["conversations"]


def messages_collection():
    return get_db()["messages"]


def significator_rules_collection():
    return get_db()["significator_rules"]


def ad_reward_events_collection():
    return get_db()["ad_reward_events"]


def rectification_ratings_collection():
    return get_db()["rectification_ratings"]
