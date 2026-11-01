"""Tiny stand-ins for the users collection. They test OUR auth/validation logic only.
They say nothing about MongoDB behaviour (transactions, indexes, Atlas) - those tests are BLOCKED until Atlas exists."""
from bson import ObjectId


class FakeUsers:
    def __init__(self, *users):
        self.users = list(users)

    def find_one(self, query, **kw):
        for u in self.users:
            if all(u.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return u
        return None


class _AuditSink:
    """Collects audit rows written by login (the real database has an `audit` collection)."""
    def __init__(self): self.docs = []
    def insert_one(self, doc, **kw): self.docs.append(doc)


class FakeDB:
    def __init__(self, *users):
        self.users = FakeUsers(*users)
        self.audit = _AuditSink()


def user_doc(role="worker", email=None, password_hash=None, active=True):
    return {"_id": ObjectId(), "role": role, "email": email or f"{role}@example.com", "name": role.title(),
            "password_hash": password_hash, "active": active}


# ---------------------------------------------------------------------------------------------------------------
# STAND-IN for MongoDB used to regression-test the sync LOGIC before Atlas is available.
# It is NOT MongoDB: it has no real transactions, isolation, indexes (except two unique keys) or concurrency.
# "Rollback" here is a snapshot restore. Real transaction/rollback/concurrency behaviour is verified only by the
# tests marked needs_atlas, which run against a real Atlas deployment.
# ---------------------------------------------------------------------------------------------------------------
import copy
from types import SimpleNamespace
from pymongo.errors import DuplicateKeyError, OperationFailure


def _get(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if isinstance(cur, list):
            try: cur = cur[int(part)]
            except (ValueError, IndexError): return _MISSING
        elif isinstance(cur, dict) and part in cur: cur = cur[part]
        else: return _MISSING
    return cur


_MISSING = object()


def _match(doc, query):
    """Tiny subset of Mongo query matching: equality, dotted keys, $in, $ne, $exists, $regex-free."""
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(_match(doc, q) for q in cond): return False
            continue
        val = _get(doc, key)
        if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
            for op, arg in cond.items():
                if op == "$in" and not (val is not _MISSING and val in arg): return False
                if op == "$ne" and val == arg: return False
                if op == "$exists" and (val is not _MISSING) != bool(arg): return False
                if op == "$gte" and not (val is not _MISSING and val >= arg): return False
                if op == "$lt" and not (val is not _MISSING and val < arg): return False
                if op == "$nin" and val is not _MISSING and val in arg: return False
                if op == "$type" and not (val is not _MISSING and isinstance(val, str)): return False
                if op == "$elemMatch" and not (isinstance(val, list) and any(isinstance(i, dict) and _match(i, arg) for i in val)): return False
        elif val is _MISSING or val != cond:
            return False
    return True


class _Cursor(list):
    def sort(self, key, direction=1):
        keys = key if isinstance(key, list) else [(key, direction)]
        for k, d in reversed(keys):      # stable multi-key sort, like MongoDB's compound sort
            super().sort(key=lambda doc, k=k: (doc.get(k) is None, str(doc.get(k)) if not isinstance(doc.get(k), (int, float)) else doc.get(k)), reverse=(d == -1))
        return self
    def skip(self, n): return _Cursor(self[n:])
    def limit(self, n): return _Cursor(self[:n])


class MemCollection:
    def __init__(self, unique=None):
        self.docs, self.unique = [], unique

    def find_one(self, query=None, **kw):
        for d in self.docs:
            if _match(d, query): return d
        return None

    def find(self, query=None, *a, **kw):
        return _Cursor(d for d in self.docs if _match(d, query))

    def insert_one(self, doc, **kw):
        if self.unique and any(d.get(self.unique) == doc.get(self.unique) for d in self.docs):
            raise DuplicateKeyError(f"duplicate {self.unique}")
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    def update_one(self, flt, update, **kw):
        d = self.find_one(flt)
        if d is None and kw.get("upsert") and "$setOnInsert" in update:
            self.docs.append(dict(update["$setOnInsert"])); return SimpleNamespace(matched_count=0, modified_count=0)
        if d is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for key, val in update.get("$set", {}).items():
            target = d
            parts = key.split(".")
            for p in parts[:-1]:
                target = target.setdefault(p, {})
            target[parts[-1]] = val
        for key, val in update.get("$push", {}).items():
            d.setdefault(key, []).append(val)
        return SimpleNamespace(matched_count=1, modified_count=1)

    def find_one_and_update(self, flt, update, **kw):
        d = self.find_one(flt)
        for key, val in update.get("$inc", {}).items():
            d[key] = d.get(key, 0) + val
        return d

    def count_documents(self, query=None, **kw):
        return sum(1 for d in self.docs if _match(d, query))


class _UniqueTuple(MemCollection):
    """Collection with a composite unique key (mirrors the real unique indexes on evidence and patient_confirmations)."""
    def __init__(self, keyfn): super().__init__(); self.keyfn = keyfn
    def insert_one(self, doc, **kw):
        if any(self.keyfn(d) == self.keyfn(doc) for d in self.docs): raise DuplicateKeyError("duplicate key")
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)


class MemMongo:
    """Holds the collections the sync code touches and plays the role of both `db` and `client`."""
    names = ("patients", "operations", "changes", "audit", "conflicts", "counters", "users", "evidence", "patient_confirmations", "identity_attempts")

    def __init__(self):
        self.patients = MemCollection("patient_id")
        self.operations = MemCollection("operation_id")
        self.changes, self.audit, self.conflicts, self.users = (MemCollection() for _ in range(4))
        self.counters = MemCollection()
        self.identity_attempts = MemCollection()
        self.evidence = _UniqueTuple(lambda d: (d.get('conflict_id'), d.get('evidence_reference')))
        self.patient_confirmations = _UniqueTuple(lambda d: (d.get('conflict_id'), d.get('user_id')))
        self.counters.docs.append({"_id": "global_change_seq", "value": 0})
        self.fail_commit = False
        self._writes = 0

    def snapshot(self):
        return {n: copy.deepcopy(getattr(self, n).docs) for n in self.names}

    def restore(self, snap):
        for n, docs in snap.items():
            getattr(self, n).docs = docs

    # `client` API
    def start_session(self):
        return _MemSession(self)


class _MemSession:
    def __init__(self, mongo): self.mongo = mongo
    def __enter__(self): return self
    def __exit__(self, *a): return False

    def with_transaction(self, callback):
        m = self.mongo
        snap, m._writes = m.snapshot(), 0
        try:
            result = callback(self)
        except BaseException:
            m.restore(snap)
            raise
        if m.fail_commit:
            m.restore(snap)
            raise OperationFailure("simulated commit failure")
        return result


def install_mem_mongo(monkeypatch):
    """Patch app.main so db/client are the in-memory stand-in. Returns it."""
    from app import main
    mongo = MemMongo()
    monkeypatch.setattr(main, "db", mongo)
    monkeypatch.setattr(main, "client", mongo)
    return mongo
