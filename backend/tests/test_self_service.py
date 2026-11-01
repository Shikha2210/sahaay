"""Self-service patient sign-up: the new patient immediately gets their OWN new record (no supervisor approval), and nothing else.
In-memory stand-in (logic only); see test_atlas_integration.py for the same flow on real Atlas."""
import pytest
from fastapi.testclient import TestClient
from app import main
from tests.test_conflict_engine import w, new_patient  # noqa: F401


def signup(name, email):
    r = TestClient(main.app).post("/api/auth/register", json={"name": name, "email": email, "password": "Correct-Horse-9!", "role": "patient"})
    assert r.status_code == 200, r.text
    return r.json(), email


def acting_as(w, email):
    """Make following requests come from the stored account (the token path is covered in test_auth.py)."""
    from app import security
    u = w.mongo.users.find_one({"email": email})
    main.app.dependency_overrides[security.current_user] = lambda: u
    return TestClient(main.app)


def test_signup_gives_the_patient_their_own_linked_record_at_once(w):
    body, e = signup("Self Patient", "self.patient@example.com")
    pid = body["user"]["linked_patient_id"]
    assert pid and pid.startswith("SH-")
    rec = acting_as(w, e).get("/api/me/record").json()
    assert rec["linked"] is True and rec["patient_id"] == pid and rec["record"]["name"] == "Self Patient"
    stored = w.mongo.patients.find_one({"patient_id": pid})
    assert stored["self_registered"] is True and stored["identity_status"] == "UNVERIFIED" and set(stored["field_versions"]) == {"name"}
    ev = [a["event_type"] for a in w.mongo.audit.docs]
    assert "PATIENT_SELF_REGISTERED" in ev and "ACCOUNT_REGISTERED" in ev
    user = w.mongo.users.find_one({"email": "self.patient@example.com"})
    assert user["identity_check"]["verified"] is False and user["identity_check"]["method"] == "self_registered"   # honest: nobody checked who they are


def test_each_new_patient_sees_only_their_own_record(w):
    a, ea = signup("Patient A", "a.patient@example.com"); b, eb = signup("Patient B", "b.patient@example.com")
    assert a["user"]["linked_patient_id"] != b["user"]["linked_patient_id"]
    ca = acting_as(w, ea)
    assert ca.get("/api/me/record").json()["record"]["name"] == "Patient A"
    assert ca.get("/api/patients").status_code == 403                                 # still cannot browse the registry
    assert ca.get(f"/api/patients/{b['user']['linked_patient_id']}").status_code == 403   # nor open someone else's record
    assert acting_as(w, eb).get("/api/me/record").json()["record"]["name"] == "Patient B"


def test_self_registration_cannot_claim_a_record_staff_already_created(w):
    existing = new_patient(w, "SH-EXIST1")
    body, e = signup("Claimer", "claimer@example.com")
    assert body["user"]["linked_patient_id"] != existing
    # the supervisor linking path still refuses to move an already-linked account onto an existing record
    sup = w.user("supervisor")
    from app import security
    main.app.dependency_overrides[security.current_user] = lambda: sup
    r = TestClient(main.app).post(f"/api/admin/patients/{existing}/link-account", json={"email": "claimer@example.com", "identity_verified": True, "identity_check_method": "in_person_document", "identity_check_note": "checked at clinic"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "account_already_linked"


def test_the_earlier_supervisor_link_mode_can_be_switched_back_on(w, monkeypatch):
    monkeypatch.setattr(main.settings, "self_service_patient_records", False)
    body, e = signup("Needs Approval", "approval@example.com")
    assert body["user"]["linked_patient_id"] is None
    assert acting_as(w, e).get("/api/me/record").json()["linked"] is False


def test_a_failure_creating_the_record_never_breaks_sign_up(w, monkeypatch):
    from pymongo.errors import PyMongoError
    def boom(*a, **k): raise PyMongoError("simulated")
    monkeypatch.setattr(w.mongo.patients, "insert_one", boom)
    body, e = signup("Still Signs Up", "still@example.com")
    assert body["access_token"] and body["user"]["linked_patient_id"] is None
