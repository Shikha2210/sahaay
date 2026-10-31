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
import pytest
from bson import ObjectId
from dotenv import dotenv_values
from fastapi.testclient import TestClient

ENV_PATH = pathlib.Path(__file__).resolve().parents[1] / ".env"
URI = (dotenv_values(ENV_PATH).get("MONGODB_URI") if ENV_PATH.exists() else None) or ""
ENABLED = os.environ.get("SAHAAY_ATLAS_TESTS") == "1" and URI.startswith("mongodb")

pytestmark = [pytest.mark.needs_atlas,
              pytest.mark.skipif(not ENABLED, reason="Atlas tests are opt-in: need backend/.env MONGODB_URI and SAHAAY_ATLAS_TESTS=1")]

from app import main, security  # noqa: E402
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
    assert stored["field_writers"]["name"] == "device-A"


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
def _make_clinical_conflict(database, c):
    pid = new_pid(); sync(c, create(pid, "device-A"))
    sync(c, op("op-" + secrets.token_hex(6), pid, "device-A", 1, condition="Hypertension"))
    sync(c, op("op-" + secrets.token_hex(6), pid, "device-B", 1, condition="Diabetes"))
    return pid, database.conflicts.find_one({"patient_id": pid})["conflict_id"]


def test_concurrent_conflict_resolution_applies_exactly_once(real, api):
    _, database = real
    c, _ = api("worker")
    pid, cid = _make_clinical_conflict(database, c)
    clinician = user("clinician")
    main.app.dependency_overrides[security.current_user] = lambda: clinician
    before = database.patients.find_one({"patient_id": pid})["version"]

    def attempt(choice):
        return TestClient(main.app).post(f"/api/conflicts/{cid}/resolve", json={"resolution": choice})

    with cf.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["accept_a", "accept_b"]))
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1, [(r.status_code, r.text) for r in results]       # exactly one winner
    assert all(code in (200, 404, 409, 503) for code in codes)                       # the loser failed safely
    conflict = database.conflicts.find_one({"conflict_id": cid})
    patient = database.patients.find_one({"patient_id": pid})
    assert conflict["status"] == "resolved" and patient["version"] == before + 1     # applied once, not twice
    assert patient["condition"] == conflict["selected_value"]
    assert database.audit.count_documents({"patient_id": pid, "event_type": "CONFLICT_RESOLVED"}) == 1


def test_supervisor_cannot_resolve_clinical_conflict_in_atlas(real, api):
    _, database = real
    c, _ = api("worker")
    pid, cid = _make_clinical_conflict(database, c)
    sup, _ = api("supervisor")
    r = sup.post(f"/api/conflicts/{cid}/resolve", json={"resolution": "accept_a"})
    assert r.status_code == 403
    assert database.conflicts.find_one({"conflict_id": cid})["status"] == "pending"


# 6 ------------------------------------------------------------------------------------------------------------
def test_audit_records_persist_with_actor_and_time(real, api):
    _, database = real
    c, u = api("worker")
    pid = new_pid(); sync(c, create(pid))
    rec = database.audit.find_one({"patient_id": pid, "event_type": "PATIENT_CREATED_SYNC"})
    assert rec["actor_id"] == str(u["_id"]) and rec["actor_role"] == "worker" and rec["created_at"]
