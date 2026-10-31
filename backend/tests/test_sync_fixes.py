"""Regression tests for Milestone 2b: S1, S2 (server side), S3, S4 + idempotency.

These run against tests.fakes.MemMongo, an IN-MEMORY STAND-IN. They verify our sync LOGIC. They do NOT prove
anything about real MongoDB transactions, isolation or concurrency (see tests/test_atlas_integration.py for those).
"""
import re
import pytest
from app import main
from app.main import detect_field_conflicts
from tests.fakes import install_mem_mongo


@pytest.fixture
def mongo(monkeypatch):
    return install_mem_mongo(monkeypatch)


def op(op_id, patient="SH-1001", device="device-A", base=1, **changes):
    return {"operation_id": op_id, "patient_id": patient, "device_id": device, "base_version": base, "changes": changes}


def post(client, *ops):
    r = client.post("/api/sync/batch", json={"operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def create(op_id, patient="SH-1001", device="device-A", **extra):
    fields = {"name": "Test Person", "phone": "9000000001", "village": "Rampur", "condition": "New registration",
              "identity_status": "UNVERIFIED", "__create__": True}
    fields.update(extra)
    return op(op_id, patient, device, 0, **fields)


# ------------------------------------------------------------------ S1
def frontend_create_payload(src):
    m = re.search(r"operation_type:'create',changes:\{([^}]*)\}", src)
    assert m, "could not find the create payload in App.jsx"
    return dict(re.findall(r"(\w+):'([^']*)'", m.group(1)))


def test_S1_create_exactly_as_the_frontend_sends_it_is_applied(as_role, mongo, app_jsx_source):
    sent = frontend_create_payload(app_jsx_source)
    assert sent.get("identity_status") == "UNVERIFIED"  # the field that used to cause the rejection
    sent.update(name="Real Name", __create__=True)
    res = post(as_role("worker"), op("op-s1-0001", "SH-4001", "device-A", 0, **sent))
    assert res[0]["status"] == "applied", res
    p = mongo.patients.find_one({"patient_id": "SH-4001"})
    assert p["identity_status"] == "UNVERIFIED" and p["version"] == 1 and p["name"] == "Real Name"


def test_S1_sync_can_never_create_a_VERIFIED_patient(as_role, mongo):
    res = post(as_role("worker"), create("op-s1-0002", identity_status="VERIFIED"))
    assert res[0]["status"] == "rejected" and res[0]["reason"] == "identity_status_not_allowed"
    assert mongo.patients.docs == [] and mongo.operations.docs == []


def test_S1_identity_status_cannot_be_changed_by_an_update_operation(as_role, mongo):
    c = as_role("worker")
    post(c, create("op-s1-0003"))
    res = post(c, op("op-s1-0004", base=1, identity_status="UNVERIFIED"))
    assert res[0]["status"] == "rejected" and res[0]["reason"] == "unsupported_fields"
    res = post(c, op("op-s1-0005", base=1, identity_status="VERIFIED"))
    assert res[0]["status"] == "rejected"
    assert mongo.patients.find_one({"patient_id": "SH-1001"})["identity_status"] == "UNVERIFIED"


def test_S1_create_without_identity_status_defaults_to_UNVERIFIED(as_role, mongo):
    body = create("op-s1-0006"); del body["changes"]["identity_status"]
    post(as_role("worker"), body)
    assert mongo.patients.find_one({"patient_id": "SH-1001"})["identity_status"] == "UNVERIFIED"


# ------------------------------------------------------------------ S2 (server side: strict order, no out-of-order apply)
def test_S2_create_then_dependent_updates_in_one_batch_are_applied_in_order(as_role, mongo):
    res = post(as_role("worker"), create("op-s2-0001"), op("op-s2-0002", base=1, village="Khera"), op("op-s2-0003", base=1, phone="9111111111"))
    assert [r["status"] for r in res] == ["applied", "applied", "applied"]
    p = mongo.patients.find_one({"patient_id": "SH-1001"})
    assert p["village"] == "Khera" and p["phone"] == "9111111111" and p["version"] == 3


def test_S2_update_before_its_create_is_not_stored_as_a_permanent_rejection(as_role, mongo):
    c = as_role("worker")
    first = post(c, op("op-s2-0004", base=1, village="Khera"))
    assert first[0]["status"] == "rejected" and first[0]["reason"] == "patient_not_found"
    assert mongo.operations.docs == []  # no poisoned receipt
    post(c, create("op-s2-0005"))
    again = post(c, op("op-s2-0004", base=1, village="Khera"))  # same operation_id, now valid
    assert again[0]["status"] == "applied"
    assert mongo.patients.find_one({"patient_id": "SH-1001"})["village"] == "Khera"


def test_S2_later_operations_for_a_patient_are_held_when_an_earlier_one_fails(as_role, mongo):
    res = post(as_role("worker"),
               create("op-s2-0006", identity_status="VERIFIED"),            # rejected create
               op("op-s2-0007", base=1, village="Khera"),                    # depends on it -> held
               create("op-s2-0008", patient="SH-2002"))                      # other patient -> unaffected
    assert [r["status"] for r in res] == ["rejected", "retryable_error", "applied"]
    assert res[1]["reason"] == "blocked_by_earlier_operation"
    assert mongo.patients.find_one({"patient_id": "SH-1001"}) is None


def test_S2_failure_of_one_patients_operation_does_not_block_other_patients(as_role, mongo):
    c = as_role("worker")
    post(c, create("op-s2-0009", patient="SH-2002"))
    mongo.fail_commit = True
    res = post(c, op("op-s2-0010", patient="SH-2002", base=1, village="X"), op("op-s2-0011", patient="SH-2002", base=1, phone="1"))
    assert [r["status"] for r in res] == ["retryable_error", "retryable_error"]
    assert res[1]["reason"] == "blocked_by_earlier_operation"


# ------------------------------------------------------------------ S3
def patient_doc(**kw):
    base = {"patient_id": "P", "village": "A", "condition": "Hypertension", "version": 2,
            "field_versions": {"village": 2, "condition": 1}, "field_writers": {"village": "device-A"}}
    base.update(kw); return base


def test_S3_own_earlier_write_is_not_a_conflict():
    updates, conflicts = detect_field_conflicts(patient_doc(), {"village": "B"}, 1, "device-A")
    assert updates == {"village": "B"} and conflicts == {}


def test_S3_another_devices_newer_write_IS_a_conflict_and_nothing_is_overwritten():
    updates, conflicts = detect_field_conflicts(patient_doc(), {"village": "B"}, 1, "device-B")
    assert updates == {} and conflicts == {"village": "A"}


def test_S3_field_with_no_recorded_writer_still_conflicts():
    p = patient_doc(); p["field_writers"] = {}
    assert detect_field_conflicts(p, {"village": "B"}, 1, "device-A")[1] == {"village": "A"}


def test_S3_unchanged_value_is_not_a_change():
    assert detect_field_conflicts(patient_doc(), {"village": "A"}, 1, "device-B") == ({}, {})


def test_S3_two_sequential_edits_from_one_device_with_the_same_base_do_not_conflict(as_role, mongo):
    c = as_role("worker")
    post(c, create("op-s3-0001"))
    res = post(c, op("op-s3-0002", base=1, village="Khera"), op("op-s3-0003", base=1, village="Rampur-2"))
    assert [r["status"] for r in res] == ["applied", "applied"] and all(r["conflicts"] == [] for r in res)
    assert mongo.patients.find_one({"patient_id": "SH-1001"})["village"] == "Rampur-2"
    assert mongo.conflicts.docs == []


def test_S3_clinical_field_changed_by_another_device_creates_a_conflict_not_last_write_wins(as_role, mongo):
    c = as_role("worker")
    post(c, create("op-s3-0004", device="device-A"))
    post(c, op("op-s3-0005", device="device-A", base=1, condition="Hypertension stage 1"))
    res = post(c, op("op-s3-0006", device="device-B", base=1, condition="Diabetes"))
    assert res[0]["status"] == "conflict" and res[0]["conflicts"][0]["category"] == "CRITICAL_CLINICAL"
    assert mongo.patients.find_one({"patient_id": "SH-1001"})["condition"] == "Hypertension stage 1"   # unchanged
    cf = mongo.conflicts.docs[0]
    assert cf["value_a"] == "Hypertension stage 1" and cf["value_b"] == "Diabetes" and cf["status"] == "pending"


def test_S3_non_conflicting_fields_from_a_stale_device_still_merge(as_role, mongo):
    c = as_role("worker")
    post(c, create("op-s3-0007", device="device-A"))
    post(c, op("op-s3-0008", device="device-A", base=1, condition="Changed"))
    res = post(c, op("op-s3-0009", device="device-B", base=1, village="Khera"))
    assert res[0]["status"] == "applied"


# ------------------------------------------------------------------ S4
def test_S4_failed_commit_never_reports_applied_and_leaves_no_trace(as_role, mongo):
    mongo.fail_commit = True
    res = post(as_role("worker"), create("op-s4-0001"))
    assert [r["status"] for r in res] == ["retryable_error"]          # exactly one result, never 'applied'
    assert mongo.patients.docs == [] and mongo.operations.docs == [] and mongo.audit.docs == [] and mongo.changes.docs == []


def test_S4_the_same_operation_succeeds_exactly_once_after_the_failure(as_role, mongo):
    c = as_role("worker")
    mongo.fail_commit = True
    post(c, create("op-s4-0002"))
    mongo.fail_commit = False
    first = post(c, create("op-s4-0002"))
    second = post(c, create("op-s4-0002"))
    assert first[0]["status"] == "applied" and second[0]["status"] == "duplicate"
    assert len(mongo.patients.docs) == 1 and len(mongo.operations.docs) == 1 and len(mongo.audit.docs) == 1


def test_S4_error_in_the_middle_of_the_transaction_rolls_back_everything(as_role, mongo, monkeypatch):
    def boom(*a, **k): raise main.PyMongoError("simulated failure while writing audit")
    monkeypatch.setattr(mongo.audit, "insert_one", boom)
    res = post(as_role("worker"), create("op-s4-0003"))
    assert [r["status"] for r in res] == ["retryable_error"]
    assert mongo.patients.docs == [] and mongo.operations.docs == [] and mongo.changes.docs == []


# ------------------------------------------------------------------ idempotency preserved
def test_replaying_an_applied_operation_returns_duplicate_and_changes_nothing(as_role, mongo):
    c = as_role("worker")
    post(c, create("op-id-0001"))
    a = post(c, op("op-id-0002", base=1, village="Khera"))
    version = mongo.patients.find_one({"patient_id": "SH-1001"})["version"]
    b = post(c, op("op-id-0002", base=1, village="Khera"))
    assert a[0]["status"] == "applied" and b[0]["status"] == "duplicate"
    assert mongo.patients.find_one({"patient_id": "SH-1001"})["version"] == version
    assert sum(1 for x in mongo.audit.docs if x["event_type"] == "SYNC_OPERATION") == 1


def test_replaying_a_conflicting_operation_does_not_create_a_second_conflict(as_role, mongo):
    c = as_role("worker")
    post(c, create("op-id-0003", device="device-A"))
    post(c, op("op-id-0004", device="device-A", base=1, condition="X"))
    post(c, op("op-id-0005", device="device-B", base=1, condition="Y"))
    again = post(c, op("op-id-0005", device="device-B", base=1, condition="Y"))
    assert again[0]["status"] == "duplicate" and len(mongo.conflicts.docs) == 1
