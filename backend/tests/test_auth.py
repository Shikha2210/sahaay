"""Authentication & authorization behaviour. Uses FakeDB for the users lookup (OUR logic only, not MongoDB)."""
import base64
import json
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from app import main, security
from app.config import settings
from app.security import create_token, hash_password, login_limiter
from tests.fakes import FakeDB, user_doc

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean():
    login_limiter._fails.clear()
    main.app.dependency_overrides.clear()
    yield
    main.app.dependency_overrides.clear()


def bearer(user):
    return {"Authorization": f"Bearer {create_token(user)}"}


def forge(payload, secret=None):
    return jwt.encode(payload, secret or settings.jwt_secret, algorithm="HS256")


# ---------- unauthorized requests ----------
@pytest.mark.parametrize("method,path", [
    ("get", "/api/auth/me"), ("get", "/api/patients"), ("get", "/api/audit"), ("get", "/api/conflicts"),
    ("get", "/api/sync/pull"), ("post", "/api/sync/batch"), ("post", "/api/chatbot/message"),
    ("post", "/api/admin/users"), ("get", "/api/dashboard/summary")])
def test_requests_without_token_are_401(method, path):
    assert getattr(client, method)(path).status_code == 401


def test_garbage_token_is_401_invalid():
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "token_invalid"


def test_expired_jwt_is_401_with_expired_code(monkeypatch):
    u = user_doc("worker")
    monkeypatch.setattr(security, "db", FakeDB(u))
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    tok = forge({"sub": str(u["_id"]), "role": "worker", "iat": past - timedelta(hours=1), "exp": past})
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "token_expired"


def test_token_signed_with_wrong_secret_rejected(monkeypatch):
    u = user_doc("worker")
    monkeypatch.setattr(security, "db", FakeDB(u))
    tok = forge({"sub": str(u["_id"]), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, secret="x" * 48)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_token_without_expiry_rejected(monkeypatch):
    u = user_doc("worker")
    monkeypatch.setattr(security, "db", FakeDB(u))
    tok = forge({"sub": str(u["_id"])})
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_alg_none_token_rejected(monkeypatch):
    u = user_doc("supervisor")
    monkeypatch.setattr(security, "db", FakeDB(u))

    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    tok = f"{b({'alg': 'none', 'typ': 'JWT'})}.{b({'sub': str(u['_id']), 'exp': 9999999999})}."
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_valid_token_for_deactivated_or_missing_user_is_401(monkeypatch):
    u = user_doc("worker")
    monkeypatch.setattr(security, "db", FakeDB())  # user no longer exists
    r = client.get("/api/auth/me", headers=bearer(u))
    assert r.status_code == 401 and r.json()["detail"]["code"] == "account_unavailable"


def test_token_lifetime_is_the_configured_short_value():
    p = jwt.decode(create_token(user_doc()), settings.jwt_secret, algorithms=["HS256"])
    assert p["exp"] - p["iat"] == settings.jwt_expire_minutes * 60 <= 240 * 60
    assert "email" not in p  # minimal claims


# ---------- role enforcement ----------
def test_worker_cannot_provision_users_or_read_audit(monkeypatch):
    u = user_doc("worker")
    monkeypatch.setattr(security, "db", FakeDB(u))
    body = {"name": "Xx", "email": "a@b.co", "password": "x" * 12, "role": "clinician"}
    assert client.post("/api/admin/users", headers=bearer(u), json=body).status_code == 403
    assert client.get("/api/audit", headers=bearer(u)).status_code == 403


def test_patient_cannot_use_staff_endpoints(monkeypatch):
    u = user_doc("patient")
    monkeypatch.setattr(security, "db", FakeDB(u))
    for path in ("/api/patients", "/api/sync/pull", "/api/conflicts", "/api/audit"):
        assert client.get(path, headers=bearer(u)).status_code == 403, path


def test_supervisor_cannot_review_evidence_clinician_only(monkeypatch):
    u = user_doc("supervisor")
    monkeypatch.setattr(security, "db", FakeDB(u))
    r = client.post("/api/conflicts/CF-1/evidence/REF/review?decision=approve&review_note=x", headers=bearer(u))
    assert r.status_code == 403


def test_public_registration_cannot_create_staff():
    r = client.post("/api/auth/register", json={"name": "Eve Staff", "email": "eve@example.com", "password": "x" * 12, "role": "supervisor"})
    assert r.status_code == 403


# ---------- login ----------
def test_invalid_login_unknown_user_is_generic_401(monkeypatch):
    monkeypatch.setattr(main, "db", FakeDB())
    r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever-12345"})
    assert r.status_code == 401 and r.json()["detail"] == "Incorrect email or password"


def test_invalid_login_wrong_password_same_message(monkeypatch):
    u = user_doc("worker", password_hash=hash_password("Correct-Horse-9!"))
    monkeypatch.setattr(main, "db", FakeDB(u))
    r = client.post("/api/auth/login", json={"email": u["email"], "password": "wrong-password-1"})
    assert r.status_code == 401 and r.json()["detail"] == "Incorrect email or password"


def test_valid_login_returns_token_and_public_user_only(monkeypatch):
    u = user_doc("worker", password_hash=hash_password("Correct-Horse-9!"))
    monkeypatch.setattr(main, "db", FakeDB(u))
    r = client.post("/api/auth/login", json={"email": u["email"], "password": "Correct-Horse-9!"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["id"] == str(u["_id"]) and body["user"]["role"] == "worker"
    assert "password_hash" not in str(body) and "$2b$" not in str(body)


def test_login_is_rate_limited_after_repeated_failures(monkeypatch):
    monkeypatch.setattr(main, "db", FakeDB())
    codes = [client.post("/api/auth/login", json={"email": "x@example.com", "password": "bad-password-1"}).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


# ---------- headers & input validation ----------
def test_security_headers_present():
    r = client.get("/api/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"


def test_cors_does_not_allow_unlisted_origin():
    r = client.options("/api/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in r.headers


def test_identity_attempt_rejects_extra_fields_like_raw_aadhaar_data(as_role):
    r = as_role("worker").post("/api/identity/SH-1/attempt", json={"consent": True, "method": "AADHAAR_SECURE_QR", "qr_payload": "RAW"})
    assert r.status_code == 422


def test_identity_attempt_rejects_unknown_method(as_role):
    assert as_role("worker").post("/api/identity/SH-1/attempt", json={"consent": True, "method": "whatever"}).status_code == 422


def test_chat_blank_or_oversized_message_rejected(as_role):
    c = as_role("patient")
    assert c.post("/api/chatbot/message", json={"message": "   "}).status_code == 422
    assert c.post("/api/chatbot/message", json={"message": "x" * 2001}).status_code == 422


@pytest.mark.parametrize("changes", [{"age": "forty"}, {"name": "x" * 500}, {"phone": {"$gt": ""}}, {"name": ["a"]}])
def test_sync_invalid_values_rejected_per_operation_not_whole_batch(as_role, changes):
    r = as_role("worker").post("/api/sync/batch", json={"operations": [
        {"operation_id": "op-valid-0001", "patient_id": "SH-1234", "device_id": "dev-1", "base_version": 1, "changes": changes}]})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["status"] == "rejected" and res["reason"].startswith("invalid_value")


def test_sync_bad_ids_rejected(as_role):
    ops = [
        {"operation_id": "x", "patient_id": "SH-1234", "device_id": "dev-1", "base_version": 1, "changes": {"phone": "1"}},
        {"operation_id": "op-valid-0002", "patient_id": "../etc", "device_id": "dev-1", "base_version": 1, "changes": {"phone": "1"}}]
    r = as_role("worker").post("/api/sync/batch", json={"operations": ops})
    assert [x["reason"] for x in r.json()["results"]] == ["invalid_operation_id", "invalid_patient_id"]
