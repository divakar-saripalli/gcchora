"""Transparent field-level encryption for the most sensitive stored
fields, applied at the collection boundary (app.db) rather than at each
call site -- so the many places that already read e.g.
profile["birth_time_24h"] or profile["geographic_coordinates"] (chat.py,
rectify.py, profiles.py, timezone_lookup.py...) don't need to change at
all. They keep reading plain values; EncryptedFieldsCollection decrypts
on the way out of Mongo and encrypts on the way in, transparently.

Scope (see DefectBacklog for the full writeup of what this does and
doesn't cover): a birth profile's own and spouse's exact birth time and
geographic coordinates, a life event's free-text notes (can contain
health/personal detail), and a chat message's content. NOT in scope:
category slugs, dates, ratings, email/name, and anything sent to an LLM
provider as part of a prompt -- those remain outside this app's control
regardless of what's encrypted at rest.
"""
import json
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

# Every value this module encrypts is stored as "<key version>:<token>",
# e.g. "v1:gAAAA...". The version says which key encrypted it, which is
# what makes replacing the key possible later: add the new key under a
# new version, point _CURRENT_KEY_VERSION at it, and records written
# under the old version keep decrypting with the old key until they are
# next saved. With a bare token and a single key there would be no way to
# tell which key a given record needs.
_CURRENT_KEY_VERSION = "v1"
_FERNET_TOKEN_PREFIX = "gAAAA"  # every Fernet token starts with this

_ciphers: dict[str, Fernet] = {}
_warned_legacy_plaintext = False


class FieldEncryptionNotConfigured(RuntimeError):
    """Raised when FIELD_ENCRYPTION_KEY isn't set but encryption/
    decryption was actually needed. Deliberately NOT caught anywhere to
    silently fall back to plaintext -- a missing key should be a loud
    startup-time problem, not a silent security regression."""


class FieldDecryptionError(RuntimeError):
    """A value that was clearly encrypted by this module could not be
    decrypted with the configured key -- almost always because
    FIELD_ENCRYPTION_KEY was changed or lost. Raised rather than handing
    the ciphertext back as if it were the real value."""


# One key today. A rotation adds a second entry here (e.g. "v2", read
# from its own setting) and keeps "v1" for records written under it.
_KEY_SETTINGS = {"v1": "field_encryption_key"}


def _key_for_version(version: str) -> str:
    return getattr(settings, _KEY_SETTINGS[version], "")


def _get_fernet(version: str = _CURRENT_KEY_VERSION) -> Fernet:
    if version not in _ciphers:
        key = _key_for_version(version)
        if not key:
            raise FieldEncryptionNotConfigured(
                "FIELD_ENCRYPTION_KEY is not set. Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"\n'
                "and set it in .env before storing or reading any profile, life event, or "
                "message data."
            )
        _ciphers[version] = Fernet(key.encode())
    return _ciphers[version]


def _old_fernets() -> list[Fernet]:
    """Previous keys (FIELD_ENCRYPTION_OLD_KEYS, comma-separated), tried
    for DECRYPTION only after the current key fails -- this is what makes
    a key rotation non-destructive. Writes always use the current key."""
    cache_key = "_old:" + settings.field_encryption_old_keys
    if cache_key not in _old_cache:
        keys = [k.strip() for k in settings.field_encryption_old_keys.split(",") if k.strip()]
        _old_cache[cache_key] = [Fernet(k.encode()) for k in keys]
    return _old_cache[cache_key]


_old_cache: dict[str, list[Fernet]] = {}


def _decrypt_token(token: str, version: str) -> str:
    """Current key first, then any old keys. Raises InvalidToken if none work."""
    try:
        return _get_fernet(version).decrypt(token.encode()).decode()
    except InvalidToken:
        for old in _old_fernets():
            try:
                return old.decrypt(token.encode()).decode()
            except InvalidToken:
                continue
        raise


def encrypt_str(value: str) -> str:
    token = _get_fernet().encrypt(value.encode()).decode()
    return f"{_CURRENT_KEY_VERSION}:{token}"


def decrypt_str(value: str) -> str:
    """Decrypts a value this module encrypted. Three shapes are handled:

    - "v1:<token>" -- the current format. A failure here means the key is
      wrong, and raises FieldDecryptionError.
    - a bare "<token>" -- written during the short period before the
      version prefix existed. Decrypted with the v1 key.
    - anything else -- plaintext stored before field encryption existed
      at all. Returned as-is (with one warning per process), so older
      records keep working without a migration; they are encrypted the
      next time they are saved."""
    global _warned_legacy_plaintext
    version, sep, rest = value.partition(":")
    if sep and version in _KEY_SETTINGS and rest.startswith(_FERNET_TOKEN_PREFIX):
        try:
            return _decrypt_token(rest, version)
        except InvalidToken:
            raise FieldDecryptionError(
                f"A stored value encrypted under key version {version} could not be decrypted. "
                "Check that FIELD_ENCRYPTION_KEY is the key the data was written with."
            ) from None
    if value.startswith(_FERNET_TOKEN_PREFIX):
        try:
            return _decrypt_token(value, "v1")
        except InvalidToken:
            pass
    if not _warned_legacy_plaintext:
        logger.warning(
            "field_encryption: read a value stored before field encryption existed -- returning "
            "it as-is. It will be encrypted the next time it is saved. This warning only logs "
            "once per process."
        )
        _warned_legacy_plaintext = True
    return value


def _encrypt_value(value: Any, as_json: bool) -> Any:
    if value is None:
        return None
    return encrypt_str(json.dumps(value) if as_json else value)


def _decrypt_value(value: Any, as_json: bool) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        # A json_field written before encryption existed is a plain
        # Mongo sub-document (e.g. {"lat": ..., "lng": ...}), not a
        # string -- decrypt_str assumes a string and would crash with
        # AttributeError on .encode(), not the InvalidToken it's built
        # to catch. Nothing to decrypt; hand it back as-is.
        return value
    decrypted = decrypt_str(value)
    if not as_json:
        return decrypted
    try:
        return json.loads(decrypted)
    except (TypeError, ValueError):
        # Legacy plaintext dict stored before encryption existed --
        # decrypt_str already returned it as-is (a dict, not a string,
        # if Mongo itself stored it as a sub-document rather than a
        # string). Nothing further to decode.
        return decrypted


class _DecryptingCursor:
    """Wraps a Motor cursor so callers can keep using it exactly as
    before. Documents are decrypted wherever they come out (to_list, or
    `async for`); everything else -- sort, limit, skip and any other
    cursor method -- is forwarded to the real cursor, so new call sites
    don't each need a method added here."""

    def __init__(self, cursor, decrypt_doc):
        self._cursor = cursor
        self._decrypt_doc = decrypt_doc

    def __getattr__(self, name):
        attr = getattr(self._cursor, name)
        if not callable(attr):
            return attr

        def forward(*args, **kwargs):
            result = attr(*args, **kwargs)
            # Chaining methods (sort/limit/skip...) return the cursor
            # itself; hand back this wrapper instead so the chain keeps
            # decrypting at the end.
            return self if result is self._cursor else result

        return forward

    async def to_list(self, length=None):
        docs = await self._cursor.to_list(length=length)
        return [self._decrypt_doc(d) for d in docs]

    async def __aiter__(self):
        async for doc in self._cursor:
            yield self._decrypt_doc(doc)


# Collection methods that neither read nor write document content, and
# so are safe to pass straight to the real collection.
_PASSTHROUGH_METHODS = frozenset({"delete_one", "delete_many", "count_documents", "create_index"})


class EncryptedFieldsCollection:
    """Wraps a Motor collection, transparently encrypting `string_fields`
    (plain text) and `json_fields` (dicts, stored as an encrypted JSON
    blob) on every write, and decrypting them on every read. Used
    exactly like the real collection for insert_one/find_one/find/
    update_one.

    Any other collection method is refused unless it is listed in
    _PASSTHROUGH_METHODS: forwarding, say, insert_many or
    find_one_and_update untouched would silently store or return these
    fields unencrypted/undecrypted. Failing loudly makes whoever adds
    such a call implement it here first."""

    def __init__(self, collection, string_fields: tuple[str, ...] = (), json_fields: tuple[str, ...] = ()):
        self._collection = collection
        self._string_fields = string_fields
        self._json_fields = json_fields

    def __getattr__(self, name):
        if name in _PASSTHROUGH_METHODS:
            return getattr(self._collection, name)
        raise AttributeError(
            f"EncryptedFieldsCollection does not support '{name}'. Add an encrypting/decrypting "
            "implementation in app/field_encryption.py before using it on this collection."
        )

    def _encrypt_doc(self, doc: dict) -> dict:
        doc = dict(doc)
        for f in self._string_fields:
            if doc.get(f) is not None:
                doc[f] = _encrypt_value(doc[f], as_json=False)
        for f in self._json_fields:
            if doc.get(f) is not None:
                doc[f] = _encrypt_value(doc[f], as_json=True)
        return doc

    def _decrypt_doc(self, doc: dict | None) -> dict | None:
        if doc is None:
            return None
        doc = dict(doc)
        for f in self._string_fields:
            if doc.get(f) is not None:
                doc[f] = _decrypt_value(doc[f], as_json=False)
        for f in self._json_fields:
            if doc.get(f) is not None:
                doc[f] = _decrypt_value(doc[f], as_json=True)
        return doc

    def _encrypt_set_update(self, update: dict) -> dict:
        if "$set" not in update:
            return update
        update = dict(update)
        new_set = dict(update["$set"])
        for f in self._string_fields:
            if f in new_set and new_set[f] is not None:
                new_set[f] = _encrypt_value(new_set[f], as_json=False)
        for f in self._json_fields:
            if f in new_set and new_set[f] is not None:
                new_set[f] = _encrypt_value(new_set[f], as_json=True)
        update["$set"] = new_set
        return update

    async def insert_one(self, doc: dict, *args, **kwargs):
        return await self._collection.insert_one(self._encrypt_doc(doc), *args, **kwargs)

    async def find_one(self, *args, **kwargs):
        result = await self._collection.find_one(*args, **kwargs)
        return self._decrypt_doc(result)

    async def update_one(self, query: dict, update: dict, *args, **kwargs):
        return await self._collection.update_one(query, self._encrypt_set_update(update), *args, **kwargs)

    def find(self, *args, **kwargs):
        return _DecryptingCursor(self._collection.find(*args, **kwargs), self._decrypt_doc)
