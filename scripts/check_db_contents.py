"""Read-only diagnostic: lists every user and birth profile in the configured
MongoDB database. Run this from your own machine (not from any sandboxed
environment) with your real .venv active:

    python scripts/check_db_contents.py

Use it to check whether a resumed/recreated Atlas cluster actually has your
old data, and whether birth profiles are linked to the user account you're
currently signed in with.
"""
import asyncio
from app.db import users_collection, birth_profiles_collection


async def main():
    users = await users_collection().find({}).to_list(length=100)
    print(f"=== USERS ({len(users)}) ===")
    for u in users:
        print(f"  _id={u.get('_id')} email={u.get('email')} googleId={u.get('googleId')} name={u.get('name')}")

    profiles = await birth_profiles_collection().find({}).to_list(length=100)
    print(f"=== BIRTH PROFILES ({len(profiles)}) ===")
    for p in profiles:
        print(f"  _id={p.get('_id')} userId={p.get('userId')} label={p.get('label')} birth_date={p.get('birth_date')}")

    if users and profiles:
        user_ids = {str(u["_id"]) for u in users}
        orphaned = [p for p in profiles if str(p.get("userId")) not in user_ids]
        if orphaned:
            print(f"\n!!! {len(orphaned)} profile(s) reference a userId that doesn't match any current user:")
            for p in orphaned:
                print(f"    profile {p.get('_id')} -> userId={p.get('userId')} (no matching user)")


if __name__ == "__main__":
    asyncio.run(main())
