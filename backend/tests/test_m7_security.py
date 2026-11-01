"""Milestone 7 - final security audit as executable checks (authorization sweep, production configuration, headers, abuse limits).
These test OUR rules, not MongoDB (see test_atlas_integration.py for real-database behaviour)."""
import base64
import json
import os
import pathlib
import secrets
import subprocess
import sys
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
import jwt
from pydantic import ValidationError
from app import main, security
from app.config import Settings
from tests.conftest import make_user
from tests.test_conflict_engine import w  # noqa: F401

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PUBLIC = {("GET", "/health"), ("POST", "/api/auth/register"), ("POST", "/api/auth/login")}
ROLES = ("patient", "worker", "clinician", "supervisor")


def routes():
    for r in main.app.routes:
        if isinstance(r, APIRoute):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                yield m, r.path, r


def declared_roles(route):
    found = []

    def walk(dep):
        for d in dep.dependencies:
            if "require_roles" in getattr(d.call, "__qualname__", ""):
                found.append(set(d.call.__closure__[0].cell_contents))
            walk(d)
    walk(route.dependant)
    return found[0] if found else None


def concrete(path):
    return path.replace("{patient_id}", "SH-T1").replace("{conflict_id}", "CF-1").replace("{evidence_reference}", "REG-1")


@pytest.fixture
def anon():
    main.app.dependency_overrides.clear()
    yield TestClient(main.app, raise_server_exceptions=False)
    main.app.dependency_overrides.clear()


def test_every_non_public_route_rejects_a_missing_or_garbage_token(anon):
    checked = 0
    for method, path, _ in routes():
        if (method, path) in PUBLIC:
            continue
        for headers in ({}, {"Authorization": "Bearer not-a-real-token"}):
            r = anon.request(method, concrete(path), headers=headers, json={} if method == "POST" else None)
            assert r.status_code == 401, (method, path, r.status_code)
        checked += 1
    assert checked >= 25


def test_every_role_restricted_route_refuses_roles_outside_its_declaration(anon):
    refused = 0
    for method, path, route in routes():
        allowed = declared_roles(route)
        if allowed is None:
            continue
        for role in set(ROLES) - allowed:
            u = make_user(role)
            main.app.dependency_overrides[security.current_user] = lambda u=u: u
            r = anon.request(method, concrete(path), json={} if method == "POST" else None)
            assert r.status_code == 403, (role, method, path, r.status_code)
            refused += 1
    assert refused >= 40


def test_routes_without_a_role_list_are_only_the_ones_meant_for_any_signed_in_user():
    open_to_any = {(m, p) for m, p, r in routes() if declared_roles(r) is None} - PUBLIC
    assert open_to_any == {("POST", "/api/auth/logout"), ("GET", "/api/auth/me"), ("GET", "/api/ai/status"), ("POST", "/api/chatbot/message")}


def test_security_headers_are_on_responses_and_the_api_is_never_cached(anon):
    for r in (anon.get("/health"), anon.get("/api/patients")):
        h = r.headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "camera=()" in h["permissions-policy"]
    assert anon.get("/api/patients").headers["cache-control"] == "no-store"


def test_cors_allows_only_configured_origins(anon):
    bad = anon.options("/api/patients", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
    good = anon.options("/api/patients", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert good.headers.get("access-control-allow-origin") == "http://localhost:5173" and "access-control-allow-credentials" not in good.headers


def test_tokens_signed_with_another_key_or_alg_none_are_rejected(anon):
    u = make_user("supervisor")
    forged = jwt.encode({"sub": str(u["_id"]), "role": "supervisor", "exp": 9999999999}, "x" * 48, algorithm="HS256")
    assert anon.get("/api/audit", headers={"Authorization": "Bearer " + forged}).status_code == 401

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    none_tok = b64({"alg": "none", "typ": "JWT"}) + "." + b64({"sub": str(u["_id"]), "exp": 9999999999}) + "."
    assert anon.get("/api/audit", headers={"Authorization": "Bearer " + none_tok}).status_code == 401


def test_password_spraying_from_one_address_is_throttled_across_accounts(w, anon):
    codes = [anon.post("/api/auth/login", json={"email": f"nobody{i}@example.com", "password": "wrong-password-1"}).status_code for i in range(36)]
    assert codes[:30] == [401] * 30 and set(codes[30:]) == {429}


def test_self_registration_is_limited_per_address(w, anon):
    codes = [anon.post("/api/auth/register", json={"name": "Synthetic Person", "email": f"synthetic{i}-{secrets.token_hex(3)}@example.com",
                                                    "password": "Correct-Horse-9!", "role": "patient"}).status_code for i in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


# ------------------------------------------------------------------------------------------------ production configuration
def prod(monkeypatch, **env):
    for k in ("JWT_SECRET", "SEED_DEMO_DATA", "CORS_ORIGINS", "ENVIRONMENT", "BOOTSTRAP_SUPERVISOR_EMAIL", "BOOTSTRAP_SUPERVISOR_PASSWORD", "MONGODB_DATABASE", "GROQ_API_URL"):
        monkeypatch.delenv(k, raising=False)
    base = {"MONGODB_URI": "mongodb+srv://u:p@cluster.example.mongodb.net/", "JWT_SECRET": secrets.token_urlsafe(48),
            "ENVIRONMENT": "production", "CORS_ORIGINS": "https://app.sahaay.example"}
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_a_correct_production_configuration_is_accepted(monkeypatch):
    s = prod(monkeypatch)
    assert s.environment == "production" and not s.seed_demo_data


@pytest.mark.parametrize("bad", [
    {"SEED_DEMO_DATA": "true"}, {"CORS_ORIGINS": "http://localhost:5173"}, {"CORS_ORIGINS": "https://ok.example,http://insecure.example"},
    {"MONGODB_URI": "mongodb://host:27017/"}, {"MONGODB_DATABASE": "sahaay_test_abc"}, {"GROQ_API_URL": "http://127.0.0.1:8765/x"},
    {"BOOTSTRAP_SUPERVISOR_EMAIL": "boss@example.com", "BOOTSTRAP_SUPERVISOR_PASSWORD": "Str0ng!Passphrase#77"}, {"ENVIRONMENT": "staging"}])
def test_unsafe_production_settings_refuse_to_start(monkeypatch, bad):
    with pytest.raises(ValidationError):
        prod(monkeypatch, **bad)


def test_api_documentation_is_switched_off_in_production():
    env = {**os.environ, "ENVIRONMENT": "production", "CORS_ORIGINS": "https://app.sahaay.example", "MONGODB_URI": "mongodb://127.0.0.1:1/?tls=true&serverSelectionTimeoutMS=300",
           "JWT_SECRET": secrets.token_urlsafe(48), "SEED_DEMO_DATA": "false"}
    code = ("from fastapi.testclient import TestClient\nfrom app.main import app\nc=TestClient(app)\n"
            "print([c.get(p).status_code for p in ('/docs','/redoc','/openapi.json')], 'strict-transport-security' in c.get('/nonexistent').headers)")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60)
    assert "[404, 404, 404] True" in out.stdout, out.stdout + out.stderr[-300:]


def test_development_mode_still_serves_the_documentation(anon):
    assert anon.get("/openapi.json").status_code == 200


def test_a_rejected_secret_is_never_printed_in_the_startup_error(monkeypatch):
    with pytest.raises(ValidationError) as e:
        prod(monkeypatch, JWT_SECRET="QqZzUniqueMarker77")
    assert "QqZzUniqueMarker77" not in str(e.value) and "mongodb+srv://u:p@" not in str(e.value)
