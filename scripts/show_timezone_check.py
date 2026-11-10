"""Read-only: shows every profile's stored vs computed timezone offset,
side by side, without changing anything -- for verifying
fix_timezone_offsets.py's "already correct" results are real rather
than silently skipped. Run with:
    python -m scripts.show_timezone_check
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import birth_profiles_collection  # noqa: E402
from app.timezone_lookup import compute_timezone_offset_hours  # noqa: E402


async def main() -> None:
    profiles = await birth_profiles_collection().find({}).to_list(length=100)
    for p in profiles:
        coords = p.get("geographic_coordinates", {})
        lat, lng = coords.get("latitude"), coords.get("longitude")
        bd, bt = p.get("birth_date"), p.get("birth_time_24h")
        stored = coords.get("timezone_offset_hours")
        computed = compute_timezone_offset_hours(lat, lng, bd, bt)
        match = "MATCH" if computed == stored else "DIFFERS"
        print(f"{p.get('label')} [{p['_id'][:8]}]: born {bd} {bt} at ({lat}, {lng})")
        print(f"  stored offset:   {stored}")
        print(f"  computed offset: {computed}   -- {match}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
