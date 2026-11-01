"""Self-service patient sign-up: the new patient immediately gets their OWN new record (no supervisor approval), and nothing else.
In-memory stand-in (logic only); see test_atlas_integration.py for the same flow on real Atlas."""
import json
import pytest
from fastapi.testclient import TestClient
from app import main
from tests.test_conflict_engine import w, new_patient  # noqa: F401


@pytest.fixture(autouse=True)
def _claim_enabled_for_these_tests(monkeypatch):
    monkeypatch.setattr(main.settings, "allow_record_claim", True)     # the ID + phone self-claim is OFF by default in the app


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


# ================================================================ connecting to a record a health worker already made (ID + phone)
def claim(name, email, pid, phone):
    return TestClient(main.app).post("/api/auth/register", json={"name": name, "email": email, "password": "Correct-Horse-9!", "role": "patient", "claim_patient_id": pid, "claim_phone": phone})


def record_with_phone(w, pid, phone):
    new_patient(w, pid)
    w.mongo.patients.find_one({"patient_id": pid})["phone"] = phone
    return pid


def test_matching_id_and_phone_connects_to_the_existing_record_without_a_supervisor(w):
    pid = record_with_phone(w, "SH-CLAIM1", "9876543210")
    before = len(w.mongo.patients.docs)
    r = claim("Real Owner", "owner@example.com", pid, "+91 98765-43210")                   # formatting differences do not matter
    assert r.status_code == 200 and r.json()["user"]["linked_patient_id"] == pid
    assert len(w.mongo.patients.docs) == before                                           # no extra record was made
    c = acting_as(w, "owner@example.com")
    assert c.get("/api/me/record").json()["patient_id"] == pid
    u = w.mongo.users.find_one({"email": "owner@example.com"})
    assert u["identity_check"]["verified"] is False and u["identity_check"]["method"] == "self_claim_id_and_phone" and u["linked_by"] == "self_claim"
    assert "PATIENT_SELF_LINKED" in [a["event_type"] for a in w.mongo.audit.docs]


@pytest.mark.parametrize("pid,phone", [("SH-CLAIM2", "9000000000"), ("SH-NOPE99", "9876543210"), ("SH-CLAIM2", ""), ("", "9876543210"), ("SH-CLAIM2", "123")])
def test_any_mismatch_is_refused_with_one_vague_message_and_creates_no_account(w, pid, phone):
    record_with_phone(w, "SH-CLAIM2", "9876543210")
    r = claim("Guesser", "guesser@example.com", pid, phone)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "record_not_matched"
    assert w.mongo.users.find_one({"email": "guesser@example.com"}) is None
    assert "wrong" not in r.text.lower() or "phone number your health worker recorded" in r.text   # never says which part failed


def test_the_error_is_identical_for_a_real_id_and_an_unknown_id(w):
    record_with_phone(w, "SH-CLAIM3", "9876543210")
    a = claim("Aaa", "a1@example.com", "SH-CLAIM3", "9000000001").json()
    b = claim("Bbb", "b1@example.com", "SH-UNKNOWN", "9000000001").json()
    assert a == b


def test_a_record_that_has_no_phone_cannot_be_claimed(w):
    new_patient(w, "SH-NOPHONE")
    assert claim("Xxx", "x1@example.com", "SH-NOPHONE", "9876543210").status_code == 400


def test_a_record_already_connected_to_an_account_cannot_be_claimed_again(w):
    pid = record_with_phone(w, "SH-CLAIM4", "9876543210")
    assert claim("First", "first@example.com", pid, "9876543210").status_code == 200
    assert claim("Second", "second@example.com", pid, "9876543210").status_code == 400
    assert w.mongo.users.find_one({"email": "second@example.com"}) is None


def test_guessing_the_phone_number_is_locked_after_five_wrong_tries(w):
    pid = record_with_phone(w, "SH-CLAIM5", "9876543210")
    codes = [claim("Ggg", f"g{i}@example.com", pid, f"90000000{i:02d}").status_code for i in range(5)]
    assert codes == [400] * 5
    r = claim("Owner", "late.owner@example.com", pid, "9876543210")                         # even the right phone is refused during the lock
    assert r.status_code == 429 and r.json()["detail"]["code"] == "too_many_attempts"
    failed = [a for a in w.mongo.audit.docs if a["event_type"] == "PATIENT_LINK_FAILED"]
    assert len(failed) == 5 and all("9000000" not in json.dumps(a.get("metadata", {})) for a in failed)   # the guessed phone is not stored


def test_signup_without_claim_still_creates_an_own_record(w):
    record_with_phone(w, "SH-CLAIM6", "9876543210")
    body, e = signup("Fresh", "fresh@example.com")
    assert body["user"]["linked_patient_id"] != "SH-CLAIM6"


def test_record_claim_is_refused_when_not_enabled_which_is_the_default(w, monkeypatch):
    monkeypatch.setattr(main.settings, "allow_record_claim", False)
    record_with_phone(w, "SH-CLAIM7", "9876543210")
    r = claim("Blocked", "blocked@example.com", "SH-CLAIM7", "9876543210")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "claim_disabled"
    assert w.mongo.users.find_one({"email": "blocked@example.com"}) is None


# ================================================================ staff login IDs
def provision(w, **body):
    from app import security
    sup = w.supervisor
    main.app.dependency_overrides[security.current_user] = lambda: sup
    return TestClient(main.app).post("/api/admin/users", json={"password": "Staff-Pass-2026!", **body})


def test_a_health_worker_gets_a_random_login_id_and_needs_no_email(w):
    r = provision(w, name="Field Worker", role="worker")
    assert r.status_code == 201 and r.json()["email"] is None
    lid = r.json()["login_id"]
    import re
    assert re.fullmatch(r"HW-[A-HJKMNP-Z2-9]{6}", lid)
    main.app.dependency_overrides.clear()
    ok = TestClient(main.app).post("/api/auth/login", json={"email": lid, "password": "Staff-Pass-2026!"})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "worker" and ok.json()["user"]["login_id"] == lid
    again = TestClient(main.app).post("/api/auth/login", json={"email": lid.lower(), "password": "Staff-Pass-2026!"})
    assert again.status_code == 200                                             # not case sensitive
    assert TestClient(main.app).post("/api/auth/login", json={"email": lid, "password": "wrong-password-1"}).status_code == 401


def test_each_new_staff_account_gets_a_different_id_and_an_optional_email_also_works(w):
    ids = {provision(w, name=f"Worker {i}", role="worker").json()["login_id"] for i in range(5)}
    assert len(ids) == 5
    r = provision(w, name="With Email", role="worker", email="with.email@example.com")
    assert r.status_code == 201 and r.json()["email"] == "with.email@example.com"
    main.app.dependency_overrides.clear()
    c = TestClient(main.app)
    assert c.post("/api/auth/login", json={"email": "with.email@example.com", "password": "Staff-Pass-2026!"}).status_code == 200
    assert c.post("/api/auth/login", json={"email": r.json()["login_id"], "password": "Staff-Pass-2026!"}).status_code == 200


def test_unknown_login_ids_get_the_same_generic_error_and_are_throttled(w):
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"email": "HW-ZZZZZZ", "password": "wrong-password-1"})
    assert r.status_code == 401 and r.json()["detail"] == "Incorrect email or password"
    codes = [c.post("/api/auth/login", json={"email": "HW-ZZZZZZ", "password": "wrong-password-1"}).status_code for _ in range(6)]
    assert codes[-1] == 429
