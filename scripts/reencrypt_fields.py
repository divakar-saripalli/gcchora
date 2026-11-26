"""Re-encrypts every encrypted field under the CURRENT key.

Key rotation: 1) generate a new key; 2) set FIELD_ENCRYPTION_KEY to it and
FIELD_ENCRYPTION_OLD_KEYS to the previous key(s); 3) run once from the
project root:
    python -m scripts.reencrypt_fields           # dry run (counts only)
    python -m scripts.reencrypt_fields --apply   # write
4) after an --apply run, the old key can be dropped from the environment.

Also encrypts any legacy plaintext values. Never prints field contents.
"""
import asyncio
import sys

from app.db import birth_profiles_collection, life_events_collection, messages_collection


async def _run(apply: bool) -> None:
    for name, wrapper in (
        ("birth_profiles", birth_profiles_collection()),
        ("life_events", life_events_collection()),
        ("messages", messages_collection()),
    ):
        fields = wrapper._string_fields + wrapper._json_fields
        raw = wrapper._collection
        total = changed = 0
        async for doc in raw.find({}):
            total += 1
            plain = wrapper._decrypt_doc(doc)  # raises if no configured key works
            fresh = wrapper._encrypt_doc(plain)
            update = {f: fresh[f] for f in fields if doc.get(f) is not None}
            if update:
                changed += 1
                if apply:
                    await raw.update_one({"_id": doc["_id"]}, {"$set": update})
        print(f"{name}: {total} documents, {changed} {'re-encrypted' if apply else 'would be re-encrypted'}")


if __name__ == "__main__":
    asyncio.run(_run("--apply" in sys.argv))
