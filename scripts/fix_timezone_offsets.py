"""One-time backfill: recomputes every existing birth profile's
timezone_offset_hours using app.timezone_lookup (historically-correct,
DST-aware), replacing whatever was manually typed into the profile-setup
form when it was created. Run with:
    python -m scripts.fix_timezone_offsets

Safe to re-run; it's idempotent (recomputing an already-correct offset
just rewrites the same value). Prints a before/after line only for
profiles whose offset actually changes, so you can see exactly which
ones were wrong and by how much.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import birth_profiles_collection  # noqa: E402
from app.timezone_lookup import compute_timezone_offset_hours  # noqa: E402


async def fix() -> None:
    collection = birth_profiles_collection()
    cursor = collection.find({})
    profiles = await cursor.to_list(length=10000)

    checked = 0
    changed = 0
    skipped = 0
    for profile in profiles:
        checked += 1
        coords = profile.get("geographic_coordinates") or {}
        lat, lng = coords.get("latitude"), coords.get("longitude")
        birth_date, birth_time = profile.get("birth_date"), profile.get("birth_time_24h")
        if lat is None or lng is None or not birth_date or not birth_time:
            skipped += 1
            continue

        new_offset = compute_timezone_offset_hours(lat, lng, birth_date, birth_time)
        if new_offset is None:
            skipped += 1
            continue

        old_offset = coords.get("timezone_offset_hours")
        if old_offset == new_offset:
            continue

        await collection.update_one(
            {"_id": profile["_id"]},
            {"$set": {"geographic_coordinates.timezone_offset_hours": new_offset}},
        )
        changed += 1
        print(
            f"  {profile.get('label', '(unlabeled)')} [{profile['_id']}]: "
            f"{old_offset} -> {new_offset}  (born {birth_date} {birth_time} at {lat},{lng})"
        )

    print(f"\nChecked {checked} profile(s): {changed} corrected, "
          f"{checked - changed - skipped} already correct, {skipped} skipped (missing data).")


if __name__ == "__main__":
    asyncio.run(fix())
