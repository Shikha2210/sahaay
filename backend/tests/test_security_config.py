"""Startup/config security. No database involved (bootstrap tests use a fake collection: unit tests of OUR logic)."""
import secrets
import pytest
from pydantic import ValidationError
from app.config import Settings, validate_bootstrap_password

GOOD_SECRET = secrets.token_urlsafe(48)


def settings_from(monkeypatch, **env):
    for k in ("JWT_SECRET", "JWT_EXPIRE_MINUTES", "SEED_DEMO_DATA", "CORS_ORIGINS", "BOOTSTRAP_SUPERVISOR_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MONGODB_URI", "mongodb://127.0.0.1:1/")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_missing_jwt_secret_refuses_to_start(monkeypatch):
    with pytest.raises(ValidationError):
        settings_from(monkeypatch)


@pytest.mark.parametrize("bad", [
    "short", "a" * 40, "REPLACE_WITH_AT_LEAST_32_RANDOM_CHARACTERS", "my-super-secret-password-for-sahaay-app-123",
    "change_me_change_me_change_me_change_me", "x" * 31 + "y"])
def test_weak_jwt_secret_rejected(monkeypatch, bad):
    with pytest.raises(ValidationError):
        settings_from(monkeypatch, JWT_SECRET=bad)


def test_strong_jwt_secret_accepted(monkeypatch):
    assert settings_from(monkeypatch, JWT_SECRET=GOOD_SECRET).jwt_secret == GOOD_SECRET


def test_default_token_lifetime_is_short_and_capped(monkeypatch):
    assert settings_from(monkeypatch, JWT_SECRET=GOOD_SECRET).jwt_expire_minutes == 60
    with pytest.raises(ValidationError):
        settings_from(monkeypatch, JWT_SECRET=GOOD_SECRET, JWT_EXPIRE_MINUTES="720")


def test_demo_seeding_is_off_by_default(monkeypatch):
    assert settings_from(monkeypatch, JWT_SECRET=GOOD_SECRET).seed_demo_data is False


def test_wildcard_cors_rejected(monkeypatch):
    with pytest.raises(ValidationError):
        settings_from(monkeypatch, JWT_SECRET=GOOD_SECRET, CORS_ORIGINS="*")


def test_no_builtin_bootstrap_password(monkeypatch):
    assert settings_from(monkeypatch, JWT_SECRET=GOOD_SECRET).bootstrap_supervisor_password == ""


@pytest.mark.parametrize("bad", [
    "", "short1!A", "ChangeThisBeforeUse123!", "alllowercase12345!", "NoSymbolsHere1234", "Password-Strong-99!",
    "supervisorX-Strong#1ok"])
def test_bad_bootstrap_passwords_rejected(bad):
    with pytest.raises(ValueError):
        validate_bootstrap_password(bad, "supervisor@example.com")


def test_strong_bootstrap_password_accepted():
    pw = "Tr1cky-Lantern-Walks-42!"
    assert validate_bootstrap_password(pw, "supervisor@example.com") == pw


# --- bootstrap(): fake collections (unit test of OUR logic, not MongoDB) ---
class _Col:
    def __init__(self, find=None):
        self.find, self.inserted, self.counted = find, [], 0

    def find_one(self, *a, **k): return self.find
    def insert_one(self, d, **k): self.inserted.append(d)
    def count_documents(self, *a, **k): self.counted += 1; return 0
    def update_one(self, *a, **k): self.inserted.append("upsert")


class _DB:
    def __init__(self, supervisor_exists):
        self.users = _Col({"role": "supervisor"} if supervisor_exists else None)
        self.patients, self.conflicts = _Col(), _Col()


def test_bootstrap_without_password_fails_when_no_supervisor_exists(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "db", _DB(False))
    monkeypatch.setattr(main.settings, "bootstrap_supervisor_password", "")
    with pytest.raises(RuntimeError, match="bootstrap password"):
        main.bootstrap()


def test_bootstrap_with_old_default_password_fails(monkeypatch):
    from app import main
    monkeypatch.setattr(main, "db", _DB(False))
    monkeypatch.setattr(main.settings, "bootstrap_supervisor_password", "ChangeThisBeforeUse123!")
    with pytest.raises(RuntimeError):
        main.bootstrap()


def test_bootstrap_needs_no_password_once_a_supervisor_exists_and_seeds_nothing(monkeypatch):
    from app import main
    fake = _DB(True)
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main.settings, "bootstrap_supervisor_password", "")
    monkeypatch.setattr(main.settings, "seed_demo_data", False)
    main.bootstrap()
    assert fake.users.inserted == [] and fake.patients.inserted == [] and fake.patients.counted == 0


def test_bootstrap_creates_supervisor_with_hashed_password_and_no_seed(monkeypatch):
    from app import main
    fake = _DB(False)
    monkeypatch.setattr(main, "db", fake)
    monkeypatch.setattr(main.settings, "bootstrap_supervisor_password", "Tr1cky-Lantern-Walks-42!")
    monkeypatch.setattr(main.settings, "seed_demo_data", False)
    main.bootstrap()
    doc = fake.users.inserted[0]
    assert doc["role"] == "supervisor" and doc["password_hash"].startswith("$2") and "Lantern" not in str(doc)
    assert fake.patients.inserted == []


def test_default_bootstrap_email_is_one_the_login_form_accepts():
    """Regression: the old default supervisor@sahaay.local was rejected by login validation (HTTP 422)."""
    from app.config import validate_bootstrap_email, Settings
    from app.schemas import LoginIn
    assert validate_bootstrap_email(Settings.model_fields["bootstrap_supervisor_email"].default)
    with pytest.raises(ValueError):
        validate_bootstrap_email("supervisor@sahaay.local")
    LoginIn(email="supervisor@example.com", password="x")
