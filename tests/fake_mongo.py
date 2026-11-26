"""A small in-memory stand-in for the parts of Motor/MongoDB this app
uses, so the test suite needs no database. It implements only what the
app calls: the query operators ($gt, $gte, $lt, $lte, $ne, $in,
$exists), the update operators ($set, $inc), dotted field paths, and
cursors with sort/limit/to_list/async iteration.

Like MongoDB, a query for `None` matches a field that is missing as
well as one stored as null, and each update_one is applied as one step.
"""
import copy
from types import SimpleNamespace

_MISSING = object()


def _get(doc, path):
    current = doc
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _set(doc, path, value):
    parts = path.split(".")
    current = doc
    for part in parts[:-1]:
        if not isinstance(current.get(part), dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _matches_condition(actual, condition):
    present = actual is not _MISSING
    value = actual if present else None
    if isinstance(condition, dict) and any(k.startswith("$") for k in condition):
        for op, operand in condition.items():
            if op == "$gt" and not (value is not None and value > operand):
                return False
            if op == "$gte" and not (value is not None and value >= operand):
                return False
            if op == "$lt" and not (value is not None and value < operand):
                return False
            if op == "$lte" and not (value is not None and value <= operand):
                return False
            if op == "$ne" and value == operand:
                return False
            if op == "$in" and value not in operand:
                return False
            if op == "$exists" and present != bool(operand):
                return False
        return True
    return value == condition


def _matches(doc, query):
    return all(_matches_condition(_get(doc, path), cond) for path, cond in (query or {}).items())


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, key, direction=1):
        keys = key if isinstance(key, list) else [(key, direction)]
        for field, order in reversed(keys):
            self._docs.sort(key=lambda d: (_get(d, field) is _MISSING, d.get(field)), reverse=(order == -1))
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    async def to_list(self, length=None):
        return self._docs[:length] if length else list(self._docs)

    async def __aiter__(self):
        for doc in self._docs:
            yield doc


class FakeCollection:
    def __init__(self):
        self.docs = []
        self.indexes = []

    def _find(self, query):
        return [d for d in self.docs if _matches(d, query)]

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def find_one(self, query=None, sort=None):
        found = self._find(query)
        if sort:
            found = FakeCursor(found).sort(sort)._docs
        return copy.deepcopy(found[0]) if found else None

    def find(self, query=None):
        return FakeCursor([copy.deepcopy(d) for d in self._find(query)])

    def _apply(self, doc, update):
        for path, value in update.get("$set", {}).items():
            _set(doc, path, copy.deepcopy(value))
        for path, amount in update.get("$inc", {}).items():
            current = _get(doc, path)
            if current is None:
                raise TypeError(f"Cannot apply $inc to a null value at {path}")
            _set(doc, path, (0 if current is _MISSING else current) + amount)
        for path, value in update.get("$push", {}).items():
            current = _get(doc, path)
            items = [] if current is _MISSING or current is None else list(current)
            items.append(copy.deepcopy(value))
            _set(doc, path, items)
        for path, value in update.get("$addToSet", {}).items():
            current = _get(doc, path)
            items = [] if current is _MISSING or current is None else list(current)
            if value not in items:
                items.append(copy.deepcopy(value))
            _set(doc, path, items)
        for path in update.get("$unset", {}):
            parts = path.split(".")
            node = doc
            for part in parts[:-1]:
                node = node.get(part) if isinstance(node, dict) else None
                if node is None:
                    break
            if isinstance(node, dict):
                node.pop(parts[-1], None)

    async def update_one(self, query, update, upsert=False):
        found = self._find(query)
        if not found:
            return SimpleNamespace(matched_count=0, modified_count=0)
        self._apply(found[0], update)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def find_one_and_update(self, query, update):
        found = self._find(query)
        if not found:
            return None
        before = copy.deepcopy(found[0])
        self._apply(found[0], update)
        return before

    async def delete_one(self, query):
        found = self._find(query)
        if found:
            self.docs.remove(found[0])
        return SimpleNamespace(deleted_count=1 if found else 0)

    async def delete_many(self, query):
        found = self._find(query)
        for doc in found:
            self.docs.remove(doc)
        return SimpleNamespace(deleted_count=len(found))

    async def count_documents(self, query=None):
        return len(self._find(query))

    async def create_index(self, keys, **kwargs):
        self.indexes.append(keys)
        return str(keys)


class FakeDB:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        return self._collections.setdefault(name, FakeCollection())
