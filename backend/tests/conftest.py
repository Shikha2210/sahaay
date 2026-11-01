"""Shared test setup.

These tests import the real FastAPI app. MongoClient connects lazily, so no database
is needed for tests that fail/finish before touching it. The values below are
throw-away TEST values, not real credentials.
"""
import os
import pathlib
import pytest
from bson import ObjectId

os.environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=300")
os.environ.setdefault("JWT_SECRET", "test-only-secret-not-used-anywhere-else-000000")
os.environ.setdefault("SEED_DEMO_DATA", "false")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.security import current_user  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
APP_JSX = REPO_ROOT / "frontend" / "src" / "App.jsx"


def make_user(role):
    return {"_id": ObjectId(), "role": role, "email": f"{role}@test.invalid", "name": role, "active": True}


@pytest.fixture
def as_role():
    """Return a TestClient that is authenticated as the given role (auth dependency overridden)."""
    def _client(role):
        app.dependency_overrides[current_user] = lambda: make_user(role)
        return TestClient(app)
    yield _client
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def app_jsx_source():
    return APP_JSX.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Failed-login / registration counters are per process; every test starts with clean ones."""
    from app import security
    for lim in (security.login_limiter, security.ip_login_limiter, security.register_limiter): lim._fails.clear()
    yield
