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
