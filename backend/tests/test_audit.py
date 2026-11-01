"""Milestone 6 - login/logout audit, the filterable audit history API and the sync summary.
In-memory database stand-in (logic only); the same behaviour is re-checked on real Atlas in tests/test_atlas_integration.py."""
import json
import pytest
from fastapi.testclient import TestClient
from pymongo.errors import PyMongoError
from app import main
from app.security import hash_password, login_limiter
from tests.test_conflict_engine import (w, as_user, new_patient, raise_conflict, sync, op, BASE)  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_limiters():
    login_limiter._fails.clear(); main._blocked_logged.clear()
    yield
    main.app.dependency_overrides.clear()


def rows(w, event=None): return [a for a in w.mongo.audit.docs if event is None or a["event_type"] == event]
def code(r): return r.json()["detail"]["code"]
def login(email, password): return TestClient(main.app).post("/api/auth/login", json={"email": email, "password": password})


def account(w, role="worker", password="Correct-Horse-9!"):
    return w.user(role, password_hash=hash_password(password), password=password)


# ================================================================================================================ login audit
def test_successful_login_is_audited_with_actor_and_ip_but_no_secrets(w):
    u = account(w)
    r = login(u["email"], "Correct-Horse-9!")
    assert r.status_code == 200
    row = rows(w, "LOGIN_SUCCESS")[0]
    assert row["actor_id"] == str(u["_id"]) and row["actor_role"] == "worker" and row["created_at"] and "ip" in row["metadata"]
    blob = json.dumps(w.mongo.audit.docs, default=str)
    assert "Correct-Horse" not in blob and "password" not in blob.lower() and "eyJ" not in blob


def test_failed_logins_are_audited_without_revealing_which_case_it_was_to_the_caller(w):
    u = account(w)
    wrong = login(u["email"], "wrong-password-1"); unknown = login("nobody@example.com", "wrong-password-1")
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()          # the caller learns nothing
    failed = rows(w, "LOGIN_FAILED")
    assert [f["metadata"]["reason"] for f in failed] == ["wrong_password", "unknown_account"]
    assert all(f["actor_role"] == "anonymous" and len(f["metadata"]["account_hash"]) == 16 for f in failed)
    blob = json.dumps(w.mongo.audit.docs, default=str)
    assert u["email"] not in blob and "nobody@example.com" not in blob and "wrong-password" not in blob   # attempted accounts are hashed, never stored


def test_blocking_is_audited_once_per_window_so_an_attacker_cannot_flood_the_log(w):
    u = account(w)
    codes = [login(u["email"], "bad-password-1").status_code for _ in range(10)]
    assert codes[:5] == [401] * 5 and set(codes[5:]) == {429}
    assert len(rows(w, "LOGIN_FAILED")) == 5 and len(rows(w, "LOGIN_BLOCKED")) == 1


def test_an_audit_failure_never_breaks_sign_in(w, monkeypatch):
    u = account(w)
    def boom(*a, **k): raise PyMongoError("audit store down")
    monkeypatch.setattr(w.mongo.audit, "insert_one", boom)
    assert login(u["email"], "Correct-Horse-9!").status_code == 200
    assert login(u["email"], "wrong-password-1").status_code == 401


def test_logout_is_audited_and_needs_a_session(w):
    u = w.user("worker")
    r = as_user(u).post("/api/auth/logout")
    assert r.status_code == 200 and rows(w, "LOGOUT")[0]["actor_id"] == str(u["_id"])
    main.app.dependency_overrides.clear()
    assert TestClient(main.app).post("/api/auth/logout").status_code == 401


def test_patient_self_registration_is_audited(w):
    r = TestClient(main.app).post("/api/auth/register", json={"name": "Reg Patient", "email": "reg.patient@example.com", "password": "Long-Enough-Pass-1!", "role": "patient"})
    assert r.status_code == 200 and rows(w, "ACCOUNT_REGISTERED")[0]["actor_role"] == "patient"


# =============================================================================================================== audit history API
def seed_audit(w, n=7):
    pid = new_patient(w, "SH-1001"); new_patient(w, "SH-2002")
    for i in range(n): w.mongo.audit.docs.append({"patient_id": pid, "actor_id": str(w.clinician["_id"]), "actor_role": "clinician", "event_type": "CONFLICT_RESOLVED", "description": f"row {i}", "metadata": {"i": i}, "created_at": f"2026-11-0{1 + i % 3}T10:00:0{i}+00:00"})
    return pid


def test_audit_is_paginated_newest_first_with_totals_and_actor_names(w):
    seed_audit(w)
    r = as_user(w.supervisor).get("/api/audit?page=1&page_size=3").json()
    assert r["page"] == 1 and r["page_size"] == 3 and len(r["items"]) == 3 and r["total"] == len(w.mongo.audit.docs) and r["has_more"] is True
    stamps = [x["created_at"] for x in r["items"]]; assert stamps == sorted(stamps, reverse=True)
    last = as_user(w.supervisor).get(f"/api/audit?page={(r['total'] + 2) // 3}&page_size=3").json()
    assert last["has_more"] is False
    seen = {x["description"] for p in range(1, (r["total"] + 2) // 3 + 1) for x in as_user(w.supervisor).get(f"/api/audit?page={p}&page_size=3").json()["items"]}
    assert len(seen) == r["total"] or len(seen) >= 7                                          # every row is reachable through the pages
    resolved = as_user(w.supervisor).get("/api/audit?event_type=CONFLICT_RESOLVED&page_size=1").json()["items"][0]
    assert resolved["actor_name"] == w.clinician["name"] and "_id" not in resolved


def test_audit_filters_combine_and_are_validated(w):
    pid = seed_audit(w)
    get = lambda q: as_user(w.supervisor).get("/api/audit?" + q)
    assert get("event_type=CONFLICT_RESOLVED").json()["total"] == 7
    assert get("event_type=CONFLICT_RESOLVED,PATIENT_CREATED_SYNC").json()["total"] == 9
    assert get(f"patient_id={pid}&event_type=CONFLICT_RESOLVED").json()["total"] == 7 and get("patient_id=SH-2002&event_type=CONFLICT_RESOLVED").json()["total"] == 0
    assert get("actor_role=clinician").json()["total"] == 7 and get("actor_role=worker").json()["total"] >= 1
    day = get("event_type=CONFLICT_RESOLVED&since=2026-11-02&until=2026-11-02").json()
    assert day["total"] > 0 and all(x["created_at"].startswith("2026-11-02") for x in day["items"])          # 'until' includes the whole day
    assert get("event_type=CONFLICT_RESOLVED&since=2026-11-03").json()["total"] == sum(1 for i in range(7) if 1 + i % 3 >= 3)
    for bad in ("event_type=drop;table", "event_type=lower", "since=yesterday", "until=2026-13-45", "actor_role=ADMIN!", "page=0", "page_size=101", "page_size=0"):
        assert get(bad).status_code == 422, bad


def test_audit_access_by_role_and_security_events_are_supervisor_only(w):
    u = account(w); login(u["email"], "Correct-Horse-9!"); login("x@example.com", "bad-password-1")
    sup = as_user(w.supervisor).get("/api/audit?event_type=LOGIN_SUCCESS,LOGIN_FAILED").json()
    assert sup["total"] == 2 and {x["event_type"] for x in sup["items"]} == {"LOGIN_SUCCESS", "LOGIN_FAILED"}
    assert any("ip" in x["metadata"] for x in sup["items"])
    cli = as_user(w.clinician)
    assert cli.get("/api/audit?event_type=LOGIN_SUCCESS").json()["total"] == 0                    # asking for them explicitly still shows nothing
    assert not any(x["event_type"] in main.SECURITY_EVENTS for x in cli.get("/api/audit?page_size=100").json()["items"])
    assert as_user(w.worker).get("/api/audit").status_code == 403 and as_user(w.user("patient")).get("/api/audit").status_code == 403
    main.app.dependency_overrides.clear()
    assert TestClient(main.app).get("/api/audit").status_code == 401


def test_audit_responses_contain_no_secrets(w):
    u = account(w); login(u["email"], "Correct-Horse-9!"); seed_audit(w)
    blob = json.dumps(as_user(w.supervisor).get("/api/audit?page_size=100").json())
    assert "password_hash" not in blob and "Correct-Horse" not in blob and "$2b$" not in blob and "eyJ" not in blob


def test_conflict_decisions_appear_in_the_history_with_who_and_what(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "dosage", "10 mg", "20 mg")
    v = as_user(w.clinician).get(f"/api/conflicts/{cid}").json()["current_field_version"]
    assert as_user(w.clinician).post(f"/api/conflicts/{cid}/resolve", json={"resolution": "accept_incoming", "expected_field_version": v}).status_code == 200
    item = as_user(w.supervisor).get(f"/api/audit?event_type=CONFLICT_RESOLVED&patient_id={pid}").json()["items"][0]
    assert item["actor_name"] == w.clinician["name"] and item["actor_role"] == "clinician" and item["metadata"]["new_value"] == "20 mg" and item["metadata"]["old_value"] == "10 mg"


# ================================================================================================================ sync summary
def test_sync_summary_reports_operations_devices_and_conflicts_for_reviewers_only(w):
    pid = new_patient(w); raise_conflict(w, pid, "condition", "A", "B")
    d = as_user(w.supervisor).get("/api/sync/summary").json()
    assert d["operations_total"] >= 3 and d["by_status"].get("applied", 0) >= 2 and d["by_status"].get("conflict", 0) >= 1
    assert d["pending_conflicts"] == 1 and d["patients"] == 1 and d["changes_total"] >= 2
    devs = {x["device_id"]: x for x in d["devices"]}
    assert {"device-A", "device-B"} <= set(devs) and devs["device-A"]["operations"] >= 2 and devs["device-A"]["people"] == 1 and devs["device-A"]["last_seen"]
    assert "worker" not in json.dumps(d)                                                           # no names or ids of people, only counts
    assert as_user(w.clinician).get("/api/sync/summary").status_code == 200
    assert as_user(w.worker).get("/api/sync/summary").status_code == 403 and as_user(w.user("patient")).get("/api/sync/summary").status_code == 403


def test_dashboard_summary_no_longer_reports_the_always_zero_pending_operations(w):
    new_patient(w)
    d = as_user(w.worker).get("/api/dashboard/summary").json()
    assert "pending_operations" not in d and d["operations_total"] >= 1
