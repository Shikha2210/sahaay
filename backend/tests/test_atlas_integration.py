"""REAL MongoDB Atlas integration tests  (marker: needs_atlas).

These are the only tests that say anything about real transactions, rollback, persistence and concurrency.
They are SKIPPED unless BOTH hold:
  1. backend/.env contains a usable MONGODB_URI, and
  2. the environment variable SAHAAY_ATLAS_TESTS=1 is set (explicit opt-in so nobody hits Atlas by accident).

Safety:
  * Everything happens in a brand-new database named  sahaay_test_<random>  - NEVER in the real `sahaay` database.
  * At the end ONLY that throwaway database is dropped (guarded by its name prefix). No other data is touched.
  * Nothing is printed that contains the connection string.

Run:   $env:SAHAAY_ATLAS_TESTS='1'; .\\.venv\\Scripts\\python.exe -m pytest tests/test_atlas_integration.py -v -rs
"""
import os
import pathlib
import secrets
import concurrent.futures as cf
from pymongo.errors import DuplicateKeyError
import pytest
from bson import ObjectId
from dotenv import dotenv_values
from fastapi.testclient import TestClient

ENV_PATH = pathlib.Path(__file__).resolve().parents[1] / ".env"
URI = (dotenv_values(ENV_PATH).get("MONGODB_URI") if ENV_PATH.exists() else None) or ""
ENABLED = os.environ.get("SAHAAY_ATLAS_TESTS") == "1" and URI.startswith("mongodb")

pytestmark = [pytest.mark.needs_atlas,
              pytest.mark.skipif(not ENABLED, reason="Atlas tests are opt-in: need backend/.env MONGODB_URI and SAHAAY_ATLAS_TESTS=1")]

from app import main, security, ai  # noqa: E402
import app.db as dbmod  # noqa: E402


@pytest.fixture(scope="module")
def real():
    from pymongo import MongoClient
    from pymongo.server_api import ServerApi
    client = MongoClient(URI, server_api=ServerApi("1"), serverSelectionTimeoutMS=15000)
    name = "sahaay_test_" + secrets.token_hex(4)
    assert name.startswith("sahaay_test_") and name != "sahaay"
    database = client[name]
    mp = pytest.MonkeyPatch()
    for mod in (main, dbmod):
        mp.setattr(mod, "client", client); mp.setattr(mod, "db", database)
    mp.setattr(security, "db", database)
    dbmod.init_db()          # creates indexes in the THROWAWAY database only
    yield client, database
    mp.undo()
    if name.startswith("sahaay_test_"):
        try:
            client.drop_database(name)   # removes only the throwaway database created above
        except Exception:
            # Some Atlas database users may not hold the dropDatabase privilege; dropping the throwaway database's
            # collections one by one removes it just the same (an empty MongoDB database does not exist).
            for coll in database.list_collection_names():
                database.drop_collection(coll)
    client.close()


def user(role):
    return {"_id": ObjectId(), "role": role, "email": f"{role}@example.com", "name": role, "active": True}


@pytest.fixture
def api(real):
    def _as(role):
        u = user(role)
        main.app.dependency_overrides[security.current_user] = lambda: u
        return TestClient(main.app), u
    yield _as
    main.app.dependency_overrides.clear()


def new_pid():
    return "SH-T" + secrets.token_hex(3).upper()


def op(op_id, pid, device="device-A", base=1, **changes):
    return {"operation_id": op_id, "patient_id": pid, "device_id": device, "base_version": base, "changes": changes}


def create(pid, device="device-A"):
    return op("op-" + secrets.token_hex(6), pid, device, 0, name="Atlas Test", phone="9000000001", village="Rampur",
              condition="Routine", identity_status="UNVERIFIED", __create__=True)


def sync(client, *ops):
    r = client.post("/api/sync/batch", json={"operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


# 1 ------------------------------------------------------------------------------------------------------------
def test_atlas_connection_ping_and_replica_set(real):
    client, _ = real
    assert client.admin.command("ping")["ok"] == 1.0
    assert client.admin.command("hello").get("setName"), "transactions need a replica set (Atlas clusters are)"


# 2 ------------------------------------------------------------------------------------------------------------
def test_patient_persists_in_atlas(real, api):
    _, database = real
    c, u = api("worker")
    pid = new_pid()
    assert sync(c, create(pid))[0]["status"] == "applied"
    stored = database.patients.find_one({"patient_id": pid})
    assert stored and stored["name"] == "Atlas Test" and stored["identity_status"] == "UNVERIFIED" and stored["version"] == 1
    assert stored["field_writers"]["name"].startswith("device-A|")        # device + the person who wrote it


def test_unique_indexes_exist(real):
    _, database = real
    assert any(i.get("unique") for i in database.operations.index_information().values())
    assert any(i.get("unique") for i in database.patients.index_information().values())


# 3 ------------------------------------------------------------------------------------------------------------
def test_idempotent_replay_in_atlas(real, api):
    _, database = real
    c, _ = api("worker")
    pid = new_pid(); sync(c, create(pid))
    upd = op("op-" + secrets.token_hex(6), pid, base=1, village="Khera")
    first, second = sync(c, upd)[0], sync(c, upd)[0]
    assert first["status"] == "applied" and second["status"] == "duplicate"
    assert database.patients.find_one({"patient_id": pid})["version"] == 2
    assert database.operations.count_documents({"operation_id": upd["operation_id"]}) == 1
    assert database.audit.count_documents({"patient_id": pid, "event_type": "SYNC_OPERATION"}) == 1


# 4 ------------------------------------------------------------------------------------------------------------
def test_transaction_commit_writes_all_records_together(real, api):
    _, database = real
    c, _ = api("worker")
    pid = new_pid(); o = create(pid); sync(c, o)
    assert database.patients.count_documents({"patient_id": pid}) == 1
    assert database.operations.count_documents({"operation_id": o["operation_id"]}) == 1
    assert database.changes.count_documents({"operation_id": o["operation_id"]}) == 1
    assert database.audit.count_documents({"patient_id": pid, "event_type": "PATIENT_CREATED_SYNC"}) == 1


class _FailingAudit:
    def insert_one(self, *a, **k):
        raise main.PyMongoError("injected failure at the LAST write of the transaction")


class _DbWithFailingAudit:
    """Real database, except audit inserts fail - so Mongo itself must roll back the earlier writes."""
    def __init__(self, real_db): self._db = real_db
    def __getattr__(self, name): return _FailingAudit() if name == "audit" else getattr(self._db, name)


def test_transaction_rollback_leaves_nothing_behind_and_retry_succeeds(real, api, monkeypatch):
    _, database = real
    c, _ = api("worker")
    pid = new_pid(); o = create(pid)
    monkeypatch.setattr(main, "db", _DbWithFailingAudit(database))
    res = sync(c, o)
    assert [r["status"] for r in res] == ["retryable_error"]
    monkeypatch.setattr(main, "db", database)
    assert database.patients.count_documents({"patient_id": pid}) == 0        # rolled back by MongoDB
    assert database.operations.count_documents({"operation_id": o["operation_id"]}) == 0
    assert database.changes.count_documents({"operation_id": o["operation_id"]}) == 0
    assert sync(c, o)[0]["status"] == "applied"                               # same op succeeds afterwards, once
    assert database.patients.count_documents({"patient_id": pid}) == 1


def test_clinical_conflict_is_recorded_not_overwritten(real, api):
    _, database = real
    c, _ = api("worker")
    pid = new_pid(); sync(c, create(pid, "device-A"))
    sync(c, op("op-" + secrets.token_hex(6), pid, "device-A", 1, condition="Hypertension"))
    res = sync(c, op("op-" + secrets.token_hex(6), pid, "device-B", 1, condition="Diabetes"))[0]
    assert res["status"] == "conflict"
    assert database.patients.find_one({"patient_id": pid})["condition"] == "Hypertension"
    assert database.conflicts.count_documents({"patient_id": pid, "status": "pending"}) == 1


# 5 ------------------------------------------------------------------------------------------------------------
# Milestone 3: conflict engine on REAL Atlas (disposable database `sahaay_test_<random>` only).
def actor(database, role, **extra):
    """Create a real user document in the throwaway database and make requests act as that user."""
    u = {"_id": ObjectId(), "role": role, "email": f"{role}-{secrets.token_hex(3)}@example.com", "name": role, "active": True, **extra}
    database.users.insert_one(dict(u))
    main.app.dependency_overrides[security.current_user] = lambda: u
    return TestClient(main.app), u


def raise_conflict(database, field, a, b, pid=None):
    pid = pid or new_pid()
    w, _ = actor(database, "worker")
    sync(w, create(pid, "device-A"))
    assert sync(w, op("op-" + secrets.token_hex(6), pid, "device-A", 1, **{field: a}))[0]["status"] == "applied"
    r = sync(w, op("op-" + secrets.token_hex(6), pid, "device-B", 1, **{field: b}))[0]
    assert r["status"] == "conflict"
    return pid, r["conflicts"][0]["conflict_id"]


def fv(database, pid, field): return database.patients.find_one({"patient_id": pid})["field_versions"][field]
def code(r): return r.json()["detail"]["code"]


def test_partial_unique_indexes_for_pending_conflicts_and_account_links_exist(real):
    _, database = real
    names = set(database.conflicts.index_information()) | set(database.users.index_information())
    assert {"uniq_pending_conflict", "uniq_linked_patient"} <= names


def test_stale_clinical_resolution_is_rejected_on_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "dosage", "10 mg", "20 mg")
    seen = fv(database, pid, "dosage")
    w, _ = actor(database, "worker")
    assert sync(w, op("op-" + secrets.token_hex(6), pid, "device-A", seen, dosage="15 mg"))[0]["status"] == "applied"   # newer clinical value (made from the current version)
    cli, cu = actor(database, "clinician")
    r = cli.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "accept_incoming", "expected_field_version": seen})
    assert r.status_code == 409 and code(r) == "stale_conflict" and r.json()["detail"]["current_value"] == "15 mg"
    assert database.patients.find_one({"patient_id": pid})["dosage"] == "15 mg"                                       # not overwritten
    assert database.conflicts.find_one({"conflict_id": cid})["status"] == "pending"
    assert database.audit.count_documents({"patient_id": pid, "event_type": "CONFLICT_RESOLVED"}) == 0
    assert database.audit.count_documents({"patient_id": pid, "event_type": "CONFLICT_ACTION_DENIED"}) == 1
    ok = cli.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "accept_incoming", "expected_field_version": fv(database, pid, "dosage")})
    assert ok.status_code == 200 and database.patients.find_one({"patient_id": pid})["dosage"] == "20 mg"


def test_two_simultaneous_clinical_updates_keep_both_values_on_atlas(real, api):
    _, database = real
    pid = new_pid(); w, _ = actor(database, "worker"); sync(w, create(pid, "device-A"))
    ops = [op("op-" + secrets.token_hex(6), pid, "device-A", 1, condition="Hypertension"), op("op-" + secrets.token_hex(6), pid, "device-B", 1, condition="Diabetes")]
    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda o: sync(TestClient(main.app), o)[0], ops))
    assert sorted(r["status"] for r in results) == ["applied", "conflict"], results
    winner = database.patients.find_one({"patient_id": pid})["condition"]
    assert winner in ("Hypertension", "Diabetes")
    pending = list(database.conflicts.find({"patient_id": pid, "status": "pending"}))
    assert len(pending) == 1 and {pending[0]["value_a"], pending[0]["value_b"]} == {"Hypertension", "Diabetes"} and pending[0]["value_a"] == winner   # nothing lost


def test_concurrent_conflict_resolution_applies_exactly_once_on_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "condition", "Hypertension", "Diabetes")
    v = fv(database, pid, "condition")
    clinician = {"_id": ObjectId(), "role": "clinician", "email": "c@example.com", "name": "c", "active": True}
    main.app.dependency_overrides[security.current_user] = lambda: clinician
    before = database.patients.find_one({"patient_id": pid})["version"]

    def attempt(resolution):
        return TestClient(main.app).post(f"/api/conflicts/{cid}/resolve", json={"resolution": resolution, "expected_field_version": v})

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["accept_incoming", "accept_current"]))
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1, [(r.status_code, r.text) for r in results]
    loser = next(r for r in results if r.status_code != 200)
    assert loser.status_code in (409, 503) and (loser.status_code == 503 or code(loser) in ("conflict_not_pending", "stale_conflict"))
    cfd = database.conflicts.find_one({"conflict_id": cid}); p = database.patients.find_one({"patient_id": pid})
    assert cfd["status"] == "resolved" and p["condition"] == cfd["selected_value"]
    assert p["version"] in (before, before + 1)                                                 # at most ONE write happened
    assert database.audit.count_documents({"patient_id": pid, "event_type": "CONFLICT_RESOLVED"}) == 1
    assert database.changes.count_documents({"operation_id": "resolution:" + cid}) <= 1


def test_worker_cannot_resolve_clinical_conflict_but_supervisor_can_in_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "condition", "A", "B")
    wk, _ = actor(database, "worker")
    r = wk.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "accept_incoming", "expected_field_version": fv(database, pid, "condition")})
    assert r.status_code == 403 and code(r) == "role_not_permitted" and r.json()["detail"]["required_roles"] == ["clinician", "supervisor"]
    assert database.conflicts.find_one({"conflict_id": cid})["status"] == "pending"
    assert database.patients.find_one({"patient_id": pid})["condition"] == "A"


def test_evidence_workflow_binds_the_approved_value_on_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "age", 40, 41)
    wk, wu = actor(database, "worker")
    r = wk.post(f"/api/conflicts/{cid}/evidence", json={"reference": "DOC-A1", "description": "synthetic", "claimed_value": 41})
    assert r.status_code == 200
    cli, cu = actor(database, "clinician")
    bad = cli.post(f"/api/conflicts/{cid}/evidence/DOC-A1/review", json={"decision": "approve", "review_note": "ok ok", "approved_value": "forty"})
    assert bad.status_code == 422 and code(bad) == "invalid_value"
    assert cli.post(f"/api/conflicts/{cid}/evidence/DOC-A1/review", json={"decision": "approve", "review_note": "page 2 shows 42", "approved_value": 42}).status_code == 200
    ev = database.evidence.find_one({"conflict_id": cid})
    assert ev["claimed_value"] == 41 and ev["approved_value"] == 42 and ev["reviewed_by"] == str(cu["_id"])
    sup, _ = actor(database, "supervisor"); v = fv(database, pid, "age")
    arb = sup.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "custom", "selected_value": 99, "note": "because", "expected_field_version": v})
    assert arb.status_code == 400 and code(arb) == "resolution_not_allowed"
    mism = sup.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "apply_evidence", "evidence_reference": "DOC-A1", "selected_value": 99, "expected_field_version": v})
    assert mism.status_code == 400 and code(mism) == "value_must_come_from_evidence"
    assert database.patients.find_one({"patient_id": pid})["age"] == 40                           # nothing changed by refused attempts
    ok = sup.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "apply_evidence", "evidence_reference": "DOC-A1", "expected_field_version": v})
    assert ok.status_code == 200 and database.patients.find_one({"patient_id": pid})["age"] == 42
    hist = [h["action"] for h in database.conflicts.find_one({"conflict_id": cid})["history"]]
    assert hist == ["created", "evidence_submitted", "evidence_approved", "resolved"]


def test_rejected_evidence_resubmission_and_separation_of_duties_on_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "age", 40, 41)
    cli, cu = actor(database, "clinician")
    assert cli.post(f"/api/conflicts/{cid}/evidence", json={"reference": "DOC-B1", "description": "synthetic", "claimed_value": 41}).status_code == 200
    own = cli.post(f"/api/conflicts/{cid}/evidence/DOC-B1/review", json={"decision": "approve", "review_note": "mine", "approved_value": 41})
    assert own.status_code == 403 and code(own) == "separation_of_duties"
    other, _ = actor(database, "clinician")
    assert other.post(f"/api/conflicts/{cid}/evidence/DOC-B1/review", json={"decision": "reject", "review_note": "unreadable"}).status_code == 200
    again = other.post(f"/api/conflicts/{cid}/evidence", json={"reference": "DOC-B1", "description": "synthetic", "claimed_value": 41})
    assert again.status_code == 409 and code(again) == "evidence_reference_already_used"     # was HTTP 500 before Milestone 3
    assert database.evidence.count_documents({"conflict_id": cid}) == 1


def test_patient_confirmation_and_account_linking_on_atlas(real, api):
    _, database = real
    p1, cid = raise_conflict(database, "phone", "9111111111", "9222222222")
    p2 = new_pid(); w, _ = actor(database, "worker"); sync(w, create(p2))
    sup, su = actor(database, "supervisor")
    pat1 = {"_id": ObjectId(), "role": "patient", "email": f"p1-{secrets.token_hex(3)}@example.com", "name": "p1", "active": True}
    pat2 = {"_id": ObjectId(), "role": "patient", "email": f"p2-{secrets.token_hex(3)}@example.com", "name": "p2", "active": True}
    database.users.insert_many([dict(pat1), dict(pat2)])
    body = {"identity_verified": True, "identity_check_method": "in_person_document", "identity_check_note": "checked a paper document"}
    assert sup.post(f"/api/admin/patients/{p1}/link-account", json={"email": pat1["email"], **body}).status_code == 200
    assert sup.post(f"/api/admin/patients/{p2}/link-account", json={"email": pat2["email"], **body}).status_code == 200
    again = sup.post(f"/api/admin/patients/{p2}/link-account", json={"email": pat1["email"], **body})
    assert again.status_code == 409 and code(again) == "account_already_linked"
    assert database.users.find_one({"_id": pat1["_id"]})["linked_patient_id"] == p1
    with pytest.raises(DuplicateKeyError):                                            # the DATABASE refuses a second link, not only our code
        database.users.update_one({"_id": pat2["_id"]}, {"$set": {"linked_patient_id": p1}})
    pu1 = {**pat1, "linked_patient_id": p1}; pu2 = {**pat2, "linked_patient_id": p2}
    main.app.dependency_overrides[security.current_user] = lambda: pu2
    other = TestClient(main.app).post(f"/api/patients/{p1}/confirm-field", json={"conflict_id": cid, "confirmed_value": "9222222222"})
    assert other.status_code == 403 and code(other) == "not_your_record"                 # patient 2 cannot confirm patient 1's information
    main.app.dependency_overrides[security.current_user] = lambda: pu1
    mine = TestClient(main.app)
    assert [c["conflict_id"] for c in mine.get("/api/me/conflicts").json()["conflicts"]] == [cid]
    assert mine.post(f"/api/patients/{p1}/confirm-field", json={"conflict_id": cid, "confirmed_value": "9222222222"}).status_code == 200
    assert code(mine.post(f"/api/patients/{p1}/confirm-field", json={"conflict_id": cid, "confirmed_value": "9111111111"})) == "already_confirmed"
    assert database.patients.find_one({"patient_id": p1})["phone"] == "9111111111"          # confirmation alone changes nothing
    conf = database.patient_confirmations.find_one({"conflict_id": cid})
    main.app.dependency_overrides[security.current_user] = lambda: su
    ok = TestClient(main.app).post(f"/api/conflicts/{cid}/resolve", json={"resolution": "apply_confirmation", "patient_confirmation_id": conf["confirmation_id"], "expected_field_version": fv(database, p1, "phone")})
    assert ok.status_code == 200 and database.patients.find_one({"patient_id": p1})["phone"] == "9222222222"


def test_duplicate_pending_conflicts_are_prevented_by_the_database_on_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "condition", "A", "B")
    w, _ = actor(database, "worker")
    again = sync(w, op("op-" + secrets.token_hex(6), pid, "device-C", 1, condition="B"))[0]
    assert again["conflicts"][0]["conflict_id"] == cid and again["conflicts"][0]["existing"] is True
    assert database.conflicts.count_documents({"patient_id": pid, "status": "pending"}) == 1
    existing = database.conflicts.find_one({"conflict_id": cid})
    dup = {k: v for k, v in existing.items() if k != "_id"}; dup["conflict_id"] = "CF-DUP-" + secrets.token_hex(4)
    with pytest.raises(DuplicateKeyError):
        database.conflicts.insert_one(dup)                                              # even a racing writer cannot create a 2nd pending twin


class _FailingAudit:
    def insert_one(self, *a, **k): raise main.PyMongoError("injected failure at the last write of the resolution")


class _DbWithFailingAudit:
    def __init__(self, real_db): self._db = real_db
    def __getattr__(self, name): return _FailingAudit() if name == "audit" else getattr(self._db, name)


def test_resolution_transaction_rolls_back_completely_on_atlas(real, api, monkeypatch):
    _, database = real
    pid, cid = raise_conflict(database, "dosage", "10 mg", "20 mg")
    cli, _ = actor(database, "clinician"); v = fv(database, pid, "dosage")
    before = (database.patients.find_one({"patient_id": pid})["version"], database.changes.count_documents({"patient_id": pid}), len(database.conflicts.find_one({"conflict_id": cid})["history"]))
    monkeypatch.setattr(main, "db", _DbWithFailingAudit(database))
    r = cli.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "accept_incoming", "expected_field_version": v})
    monkeypatch.setattr(main, "db", database)
    assert r.status_code == 503 and code(r) == "transaction_failed"
    after = (database.patients.find_one({"patient_id": pid})["version"], database.changes.count_documents({"patient_id": pid}), len(database.conflicts.find_one({"conflict_id": cid})["history"]))
    assert after == before                                                              # MongoDB itself rolled back patient, feed and conflict updates
    assert database.patients.find_one({"patient_id": pid})["dosage"] == "10 mg" and database.conflicts.find_one({"conflict_id": cid})["status"] == "pending"
    assert cli.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "accept_incoming", "expected_field_version": v}).status_code == 200


def test_resolution_audit_and_history_persist_with_actor_and_values_on_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "condition", "Hypertension", "Diabetes")
    cli, cu = actor(database, "clinician")
    assert cli.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "custom", "selected_value": "Hypertension stage 1", "note": "reassessed", "expected_field_version": fv(database, pid, "condition")}).status_code == 200
    rec = database.audit.find_one({"patient_id": pid, "event_type": "CONFLICT_RESOLVED"})
    assert rec["actor_id"] == str(cu["_id"]) and rec["actor_role"] == "clinician" and rec["created_at"]
    assert rec["metadata"]["old_value"] == "Hypertension" and rec["metadata"]["new_value"] == "Hypertension stage 1" and rec["metadata"]["category"] == "CRITICAL_CLINICAL"
    h = database.conflicts.find_one({"conflict_id": cid})["history"]
    assert [x["action"] for x in h] == ["created", "resolved"] and h[-1]["actor_id"] == str(cu["_id"]) and h[-1]["new_value"] == "Hypertension stage 1"
    blob = str(list(database.audit.find({"patient_id": pid}))).lower()
    assert "password" not in blob and "eyj" not in blob


# 6 ------------------------------------------------------------------------------------------------------------
def test_audit_records_persist_with_actor_and_time(real, api):
    _, database = real
    c, u = api("worker")
    pid = new_pid(); sync(c, create(pid))
    rec = database.audit.find_one({"patient_id": pid, "event_type": "PATIENT_CREATED_SYNC"})
    assert rec["actor_id"] == str(u["_id"]) and rec["actor_role"] == "worker" and rec["created_at"]


# 7 ------------------------------------------------------------------------------------------------------------
# Milestone 4: the read endpoints behind the role workspaces, on REAL Atlas (query shapes such as $or/$elemMatch/$in).
def link_account(database, sup, pid, account, **over):
    body = {"email": account["email"], "identity_verified": True, "identity_check_method": "in_person_document", "identity_check_note": "checked a paper document", **over}
    return sup.post(f"/api/admin/patients/{pid}/link-account", json=body)


def make_patient_account(database, linked=None):
    u = {"_id": ObjectId(), "role": "patient", "email": f"pat-{secrets.token_hex(3)}@example.com", "name": "patient", "active": True, "created_at": "2026-11-01"}
    database.users.insert_one(dict(u))
    return u


def test_patient_sees_only_their_own_record_and_conflicts_on_atlas(real, api):
    _, database = real
    p1, c1 = raise_conflict(database, "phone", "9111111111", "9222222222")
    p2, c2 = raise_conflict(database, "phone", "9333333333", "9444444444")
    sup, _ = actor(database, "supervisor")
    a1, a2, a3 = make_patient_account(database), make_patient_account(database), make_patient_account(database)
    assert link_account(database, sup, p1, a1).status_code == 200 and link_account(database, sup, p2, a2).status_code == 200

    def as_patient(acct, pid):
        u = {**acct, **({"linked_patient_id": pid} if pid else {})}
        main.app.dependency_overrides[security.current_user] = lambda: u
        return TestClient(main.app)

    me1 = as_patient(a1, p1).get("/api/me/record").json()
    assert me1["patient_id"] == p1 and me1["record"]["name"] and "field_versions" not in me1["record"] and "created_by" not in me1["record"]
    assert [c["conflict_id"] for c in as_patient(a1, p1).get("/api/me/conflicts").json()["conflicts"]] == [c1]
    assert [c["conflict_id"] for c in as_patient(a2, p2).get("/api/me/conflicts").json()["conflicts"]] == [c2]
    assert as_patient(a3, None).get("/api/me/record").json() == {"linked": False}
    assert as_patient(a1, p1).post(f"/api/patients/{p2}/confirm-field", json={"conflict_id": c2, "confirmed_value": "9444444444"}).status_code == 403


def test_worker_conflict_notifications_use_real_mongo_queries_on_atlas(real, api):
    _, database = real
    w1, u1 = actor(database, "worker")
    pid = new_pid(); sync(w1, create(pid, "device-A"))
    assert sync(w1, op("op-" + secrets.token_hex(6), pid, "device-A", 1, age=40))[0]["status"] == "applied"
    r = sync(w1, op("op-" + secrets.token_hex(6), pid, "device-B", 1, age=41))[0]
    cid = r["conflicts"][0]["conflict_id"]
    mine = TestClient(main.app).get("/api/sync/conflicts").json()
    assert [c["conflict_id"] for c in mine["conflicts"]] == [cid] and mine["open_count"] == 1 and mine["conflicts"][0]["can_submit_evidence"] is True
    other, u2 = actor(database, "worker")                               # a different worker sees nothing of it
    assert other.get("/api/sync/conflicts").json()["conflicts"] == []
    again = sync(other, op("op-" + secrets.token_hex(6), pid, "device-C", 1, age=41))[0]       # ... until they propose the same value (history match)
    assert again["conflicts"][0]["conflict_id"] == cid
    assert [c["conflict_id"] for c in other.get("/api/sync/conflicts").json()["conflicts"]] == [cid]
    assert other.post(f"/api/conflicts/{cid}/evidence", json={"reference": "REG-1", "description": "register page", "claimed_value": 41}).status_code == 200
    item = other.get("/api/sync/conflicts").json()["conflicts"][0]
    assert item["evidence"][0]["status"] == "pending_review" and item["evidence"][0]["submitted_by_me"] is True
    cli, _ = actor(database, "clinician")
    assert cli.post(f"/api/conflicts/{cid}/evidence/REG-1/review", json={"decision": "approve", "review_note": "ok ok", "approved_value": 42}).status_code == 200
    sup, _ = actor(database, "supervisor")
    assert sup.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "apply_evidence", "evidence_reference": "REG-1", "expected_field_version": fv(database, pid, "age")}).status_code == 200
    main.app.dependency_overrides[security.current_user] = lambda: u2
    done = TestClient(main.app).get("/api/sync/conflicts?status=resolved").json()["conflicts"][0]
    assert done["status"] == "resolved" and done["selected_value"] == 42 and done["evidence"][0]["approved_value"] == 42 and done["can_submit_evidence"] is False


def test_clinician_evidence_queue_and_supervisor_account_list_on_atlas(real, api):
    _, database = real
    pid, cid = raise_conflict(database, "age", 40, 41)
    wk, _ = actor(database, "worker")
    assert wk.post(f"/api/conflicts/{cid}/evidence", json={"reference": "Q-1", "description": "register page", "claimed_value": 41}).status_code == 200
    cli, cu = actor(database, "clinician")
    q = [e for e in cli.get("/api/evidence").json() if e["conflict_id"] == cid]
    assert len(q) == 1 and q[0]["field"] == "age" and q[0]["claimed_value"] == 41 and q[0]["submitted_by_me"] is False
    assert cli.post(f"/api/conflicts/{cid}/evidence/Q-1/review", json={"decision": "reject", "review_note": "unreadable"}).status_code == 200
    assert [e for e in cli.get("/api/evidence").json() if e["conflict_id"] == cid] == []
    assert [e["status"] for e in cli.get("/api/evidence?status=rejected").json() if e["conflict_id"] == cid] == ["rejected"]
    wk2, _ = actor(database, "worker")
    assert wk2.get("/api/evidence").status_code == 403
    sup, _ = actor(database, "supervisor")
    acct = make_patient_account(database)
    rows = sup.get("/api/admin/patient-accounts").json()
    mine = next(r for r in rows if r["email"] == acct["email"])
    assert mine["linked_patient_id"] is None and "password" not in str(rows).lower()
    assert link_account(database, sup, pid, acct).status_code == 200
    assert next(r for r in sup.get("/api/admin/patient-accounts").json() if r["email"] == acct["email"])["linked_patient_id"] == pid
    w3, _ = actor(database, "worker")                                  # identity overrides are global: switch back to a worker explicitly
    assert w3.get("/api/admin/patient-accounts").status_code == 403


# 8 ------------------------------------------------------------------------------------------------------------
# Milestone 5: AI endpoints on REAL Atlas. Only the external AI service is a test double (no Groq key exists); roles, queries,
# audit and storage are real. We inspect exactly what would have been sent to the external service.
import copy as _copy
import json as _json


@pytest.fixture
def ai_double(monkeypatch):
    box = type("Box", (), {})(); box.calls, box.reply = [], "TEST-DOUBLE-REPLY-NEVER-STORE"
    def fake(messages, max_tokens=500):
        box.calls.append(_copy.deepcopy(messages)); return box.reply
    monkeypatch.setattr(ai, "groq_chat", fake)
    monkeypatch.setattr(main.settings, "groq_api_key", "test-double-key-not-real")
    monkeypatch.setattr(main.settings, "ai_rate_per_minute", 100); monkeypatch.setattr(main.settings, "ai_global_per_minute", 1000)
    ai.limiter.reset()
    return box


def all_documents_as_text(database):
    return _json.dumps({n: list(database[n].find({})) for n in database.list_collection_names()}, default=str)


def test_ai_summary_sends_minimal_data_audits_and_stores_nothing_on_atlas(real, api, ai_double):
    _, database = real
    pid = new_pid(); w, _ = actor(database, "worker")
    assert sync(w, create(pid, "device-A"))[0]["status"] == "applied"
    assert sync(w, op("op-" + secrets.token_hex(6), pid, "device-A", 1, dosage="5 mg", condition="Hypertension, call 9876543210"))[0]["status"] == "applied"
    sup, _ = actor(database, "supervisor"); acct = make_patient_account(database)
    assert link_account(database, sup, pid, acct).status_code == 200
    me = {**acct, "linked_patient_id": pid}
    main.app.dependency_overrides[security.current_user] = lambda: me
    c = TestClient(main.app)
    assert c.post("/api/ai/patient-summary", json={"acknowledge_external_ai": False}).status_code == 422 and ai_double.calls == []
    r = c.post("/api/ai/patient-summary", json={"acknowledge_external_ai": True, "language": "hi"})
    assert r.status_code == 200 and r.json()["summary"] == ai_double.reply
    sent = _json.dumps(ai_double.calls, ensure_ascii=False)
    for private in ("Atlas Test", "9000000001", "Rampur", pid, "9876543210", "UNVERIFIED"): assert private not in sent, private
    assert "Hypertension" in sent and "5 mg" in sent and "[phone number removed]" in sent and "Hindi" in ai_double.calls[0][0]["content"]
    rows = list(database.audit.find({"patient_id": pid, "event_type": "AI_ASSIST"}))
    assert len(rows) == 1 and rows[0]["actor_id"] == str(acct["_id"]) and rows[0]["metadata"]["mode"] == "patient_summary" and "condition" in rows[0]["metadata"]["fields_shared"]
    assert ai_double.reply not in all_documents_as_text(database)                                           # nothing the AI wrote is stored
    other = {**make_patient_account(database), "linked_patient_id": new_pid()}
    main.app.dependency_overrides[security.current_user] = lambda: other
    refused = TestClient(main.app).post("/api/ai/patient-summary", json={"patient_id": pid, "acknowledge_external_ai": True})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "not_your_record"
    assert len(ai_double.calls) == 1


def test_ai_conflict_explanation_visibility_and_no_changes_on_atlas(real, api, ai_double):
    _, database = real
    w1, u1 = actor(database, "worker")
    pid = new_pid(); sync(w1, create(pid, "device-A"))
    sync(w1, op("op-" + secrets.token_hex(6), pid, "device-A", 1, phone="9111111111"))
    cid = sync(w1, op("op-" + secrets.token_hex(6), pid, "device-B", 1, phone="9222222222"))[0]["conflicts"][0]["conflict_id"]
    before = {n: list(database[n].find({}).sort("_id", 1)) for n in ("patients", "conflicts", "evidence", "patient_confirmations", "changes")}
    explain = lambda: TestClient(main.app).post("/api/ai/conflict-explanation", json={"conflict_id": cid, "language": "ta"})
    mine = explain()                                                                           # the worker whose change raised it (real $or / history query data)
    assert mine.status_code == 200 and mine.json()["explanation"] == ai_double.reply
    sent = _json.dumps(ai_double.calls, ensure_ascii=False)
    for private in ("9111111111", "9222222222", "Atlas Test", pid, cid, "Rampur"): assert private not in sent, private
    assert "Do NOT approve, reject or resolve" in ai_double.calls[0][0]["content"] and "Tamil" in ai_double.calls[0][0]["content"]
    other, _ = actor(database, "worker")
    assert explain().json()["detail"]["code"] == "conflict_not_found"                         # another worker cannot see it
    patient_other = {**make_patient_account(database), "linked_patient_id": new_pid()}
    main.app.dependency_overrides[security.current_user] = lambda: patient_other
    assert explain().status_code == 404                                                        # another patient cannot see it
    actor(database, "clinician"); assert explain().status_code == 200                          # reviewers can
    after = {n: list(database[n].find({}).sort("_id", 1)) for n in before}
    assert after == before and database.conflicts.find_one({"conflict_id": cid})["status"] == "pending"   # the AI changed nothing
    assert database.audit.count_documents({"patient_id": pid, "event_type": "AI_ASSIST"}) == 2


def test_ai_chat_stores_nothing_and_removes_identifiers_on_atlas(real, api, ai_double):
    _, database = real
    c, u = actor(database, "worker")
    r = c.post("/api/chatbot/message", json={"message": "My Aadhaar 2345 6789 0123, phone 9876543210. ZZ-QUESTION-NEVER-STORED what is anemia?"})
    assert r.status_code == 200 and set(r.json()["redactions"]) == {"Aadhaar-like number", "phone number"}
    sent = _json.dumps(ai_double.calls)
    assert "2345" not in sent and "9876543210" not in sent and "ZZ-QUESTION-NEVER-STORED" in sent
    text = all_documents_as_text(database)
    assert "ZZ-QUESTION-NEVER-STORED" not in text and ai_double.reply not in text
    assert "chat_messages" not in database.list_collection_names() or database.chat_messages.count_documents({"user_id": str(u["_id"])}) == 0
    assert c.post("/api/chatbot/message", json={"message": "I have chest pain"}).json()["emergency"] is True and len(ai_double.calls) == 1


# ===================================================================================== Milestone 6: audit history on REAL Atlas
def _real_login_account(database, role="supervisor"):
    from app.security import hash_password
    email = f"m6-{secrets.token_hex(3)}@example.com"
    database.users.insert_one({"_id": ObjectId(), "role": role, "email": email, "name": "ZZ-SYNTHETIC M6", "active": True, "password_hash": hash_password("Correct-Horse-9!"), "created_at": "2026-11-01"})
    return email


def test_login_logout_and_failed_login_are_audited_on_atlas_and_filterable(real):
    _, database = real
    from app.security import login_limiter
    login_limiter._fails.clear(); main._blocked_logged.clear(); main.app.dependency_overrides.clear()
    sup_email = _real_login_account(database, "supervisor")
    anon = TestClient(main.app)
    assert anon.post("/api/auth/login", json={"email": sup_email, "password": "wrong-password-1"}).status_code == 401
    ok = anon.post("/api/auth/login", json={"email": sup_email, "password": "Correct-Horse-9!"})
    assert ok.status_code == 200
    h = {"Authorization": "Bearer " + ok.json()["access_token"]}
    assert anon.post("/api/auth/logout", headers=h).status_code == 200
    events = {d["event_type"] for d in database.audit.find({"event_type": {"$in": ["LOGIN_SUCCESS", "LOGIN_FAILED", "LOGOUT"]}})}
    assert {"LOGIN_SUCCESS", "LOGIN_FAILED", "LOGOUT"} <= events
    stored = _json.dumps(list(database.audit.find({}, {"_id": 0})), default=str)
    assert "wrong-password-1" not in stored and "Correct-Horse-9!" not in stored and ok.json()["access_token"] not in stored
    ok2 = anon.post("/api/auth/login", json={"email": sup_email, "password": "Correct-Horse-9!"})
    h2 = {"Authorization": "Bearer " + ok2.json()["access_token"]}
    r = anon.get("/api/audit?event_type=LOGIN_FAILED,LOGOUT&page_size=1", headers=h2).json()
    assert r["total"] >= 2 and len(r["items"]) == 1 and r["has_more"] is True and {i["event_type"] for i in r["items"]} <= {"LOGIN_FAILED", "LOGOUT"}
    page2 = anon.get("/api/audit?event_type=LOGIN_FAILED,LOGOUT&page_size=1&page=2", headers=h2).json()
    assert page2["items"] and page2["items"][0] != r["items"][0]


def test_audit_security_events_hidden_from_clinicians_and_workers_denied_on_atlas(real):
    _, database = real
    from app.security import login_limiter
    login_limiter._fails.clear(); main.app.dependency_overrides.clear()
    clin, work = _real_login_account(database, "clinician"), _real_login_account(database, "worker")
    c = TestClient(main.app)
    tc = c.post("/api/auth/login", json={"email": clin, "password": "Correct-Horse-9!"}).json()["access_token"]
    tw = c.post("/api/auth/login", json={"email": work, "password": "Correct-Horse-9!"}).json()["access_token"]
    items = c.get("/api/audit?page_size=100", headers={"Authorization": "Bearer " + tc}).json()["items"]
    assert not {i["event_type"] for i in items} & main.SECURITY_EVENTS
    assert c.get("/api/audit", headers={"Authorization": "Bearer " + tw}).status_code == 403


def test_sync_summary_counts_real_operations_and_devices_on_atlas(real, api):
    _, database = real
    c, _ = api("worker"); pid = new_pid()
    before = c.get("/api/sync/summary")
    assert before.status_code == 403                                           # workers cannot see the whole-system view
    sup, _ = actor(database, "supervisor")
    n0 = sup.get("/api/sync/summary").json()["operations_total"]
    main.app.dependency_overrides.clear(); worker, _ = actor(database, "worker")
    assert worker.post("/api/sync/batch", json={"operations": [op("m6-" + secrets.token_hex(6), pid, "device-M6", 0, name="ZZ-SYNTHETIC", age=30, phone="9000000000", village="V", condition="c", medications="m", dosage="d", last_visit="2026-10-01", identity_status="UNVERIFIED", __create__=True)]}).status_code == 200
    sup, _ = actor(database, "supervisor")
    s = sup.get("/api/sync/summary").json()
    assert s["operations_total"] == n0 + 1 and any(d["device_id"] == "device-M6" for d in s["devices"])
