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


class FakeDB:
    def __init__(self, *users):
        self.users = FakeUsers(*users)


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


class MemCollection:
    def __init__(self, unique=None):
        self.docs, self.unique = [], unique

    def find_one(self, query=None, **kw):
        for d in self.docs:
            if all(d.get(k) == v for k, v in (query or {}).items()):
                return d
        return None

    def insert_one(self, doc, **kw):
        if self.unique and any(d.get(self.unique) == doc.get(self.unique) for d in self.docs):
            raise DuplicateKeyError(f"duplicate {self.unique}")
        self.docs.append(doc)

    def update_one(self, flt, update, **kw):
        d = self.find_one(flt)
        if d is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for key, val in update.get("$set", {}).items():
            target = d
            parts = key.split(".")
            for p in parts[:-1]:
                target = target.setdefault(p, {})
            target[parts[-1]] = val
        return SimpleNamespace(matched_count=1, modified_count=1)

    def find_one_and_update(self, flt, update, **kw):
        d = self.find_one(flt)
        for key, val in update.get("$inc", {}).items():
            d[key] = d.get(key, 0) + val
        return d

    def count_documents(self, query=None, **kw):
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in (query or {}).items()))


class MemMongo:
    """Holds the collections the sync code touches and plays the role of both `db` and `client`."""
    names = ("patients", "operations", "changes", "audit", "conflicts", "counters", "users")

    def __init__(self):
        self.patients = MemCollection("patient_id")
        self.operations = MemCollection("operation_id")
        self.changes, self.audit, self.conflicts, self.users = (MemCollection() for _ in range(4))
        self.counters = MemCollection()
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
