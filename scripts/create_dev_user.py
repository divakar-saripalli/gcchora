"""Dev-only helper: upserts a fake test user into MongoDB and prints a
matching access token, so you can exercise the API via /docs without
wiring up real Google Sign-In first.

Run with:
    python -m scripts.create_dev_user
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import users_collection  # noqa: E402
from app.security import create_access_token  # noqa: E402

TEST_USER_ID = "test-user-id"


async def main() -> None:
    await users_collection().update_one(
        {"_id": TEST_USER_ID},
        {
            "$set": {
                "_id": TEST_USER_ID,
                "googleId": "dev-fake-google-id",
                "email": "test@example.com",
                "name": "Test User",
                "authProvider": "dev-fake",
                "createdAt": datetime.now(timezone.utc),
                "usage": {},
            }
        },
        upsert=True,
    )
    token = create_access_token(TEST_USER_ID)
    print(f"Test user upserted: {TEST_USER_ID}")
    print("Access token (paste into /docs -> Authorize):")
    print(token)


if __name__ == "__main__":
    asyncio.run(main())
