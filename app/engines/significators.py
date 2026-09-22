"""Loads the event-category -> significator-planet matrix.

Prefers MongoDB (`significator_rules` collection, seeded by
scripts/seed_rules.py) so rules can be edited without a redeploy; falls
back to the local astro_rules.json for local development or if the
collection hasn't been seeded yet, matching the original prototype's
fallback behavior.
"""
import json
import os
from typing import Any

from app.db import significator_rules_collection

_LOCAL_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "astro_rules.json")


def _load_local_fallback() -> dict[str, list[str]]:
    if not os.path.exists(_LOCAL_RULES_PATH):
        return {"MARRIAGE": ["Venus", "Mars", "Saturn"]}
    with open(_LOCAL_RULES_PATH, "r") as f:
        return json.load(f)


async def load_significator_matrix(system: str = "vedic") -> dict[str, list[str]]:
    cursor = significator_rules_collection().find({"system": system})
    rows: list[dict[str, Any]] = await cursor.to_list(length=1000)
    if not rows:
        return _load_local_fallback()
    return {row["category"]: row["indicators"] for row in rows}
