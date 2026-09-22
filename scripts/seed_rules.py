"""One-time migration: load astro_rules.json into the significator_rules
Mongo collection, tagged system="vedic". Run with:
    python -m scripts.seed_rules
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import significator_rules_collection  # noqa: E402

RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "astro_rules.json")


async def seed() -> None:
    with open(RULES_PATH, "r") as f:
        rules = json.load(f)

    collection = significator_rules_collection()
    for category, indicators in rules.items():
        await collection.update_one(
            {"system": "vedic", "category": category},
            {"$set": {"system": "vedic", "category": category, "indicators": indicators, "version": 1}},
            upsert=True,
        )
    print(f"Seeded {len(rules)} vedic significator rules.")


if __name__ == "__main__":
    asyncio.run(seed())
