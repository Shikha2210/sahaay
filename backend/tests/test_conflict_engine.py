"""Milestone 3 - conflict engine regression tests (C1-C11).

These use tests.fakes.MemMongo, an IN-MEMORY STAND-IN: they verify our decision logic (policy, binding of values,
stale-version rejection, structured errors, history/audit content). They do NOT prove real MongoDB transaction
isolation, rollback or concurrency - those are covered by tests/test_atlas_integration.py against real Atlas.
"""
import json
import secrets
from types import SimpleNamespace
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from app import main, security
from tests.fakes import install_mem_mongo

BASE = {"name": "Test Person", "age": 30, "phone": "9000000001", "village": "Rampur", "condition": "Routine care",
        "dosage": "5 mg", "last_visit": "2026-10-01", "identity_status": "UNVERIFIED"}


@pytest.fixture
def w(monkeypatch):
    mongo = install_mem_mongo(monkeypatch)
    n = [0]

    def user(role, **extra):
        n[0] += 1
        d = {"_id": ObjectId(), "role": role, "email": f"{role}{n[0]}@example.com", "name": f"{role}{n[0]}", "active": True, **extra}
        mongo.users.docs.append(d)
        return d

    world = SimpleNamespace(mongo=mongo, user=user, worker=user("worker"), supervisor=user("supervisor"), clinician=user("clinician"), clinician2=user("clinician"))
    yield world
    main.app.dependency_overrides.clear()


def as_user(u):
    main.app.dependency_overrides[security.current_user] = lambda: u
    return TestClient(main.app)


def code(r): return r.json()["detail"]["code"]


def sync(w, *ops):
    r = as_user(w.worker).post("/api/sync/batch", json={"operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def op(pid, device, base, **changes): return {"operation_id": "op-" + secrets.token_hex(8), "patient_id": pid, "device_id": device, "base_version": base, "changes": changes}


def new_patient(w, pid="SH-1001"):
    assert sync(w, op(pid, "device-A", 0, **BASE, __create__=True))[0]["status"] == "applied"
    return pid


def raise_conflict(w, pid, field, a, b, device_b="device-B"):
    """Device A edits `field`, then another device edits the same field from the same stale base -> a pending conflict."""
    assert sync(w, op(pid, "device-A", 1, **{field: a}))[0]["status"] == "applied"
    res = sync(w, op(pid, device_b, 1, **{field: b}))[0]
    assert res["status"] == "conflict", res
    return res["conflicts"][0]["conflict_id"]


def detail(w, who, cid): return as_user(who).get(f"/api/conflicts/{cid}").json()
def resolve(w, who, cid, **body): return as_user(who).post(f"/api/conflicts/{cid}/resolve", json=body)
def patient(w, pid="SH-1001"): return w.mongo.patients.find_one({"patient_id": pid})
def conflict(w, cid): return w.mongo.conflicts.find_one({"conflict_id": cid})
def audit(w, event, pid="SH-1001"): return [a for a in w.mongo.audit.docs if a["event_type"] == event and a["patient_id"] == pid]
def evidence(w, who, cid, ref, claimed): return as_user(who).post(f"/api/conflicts/{cid}/evidence", json={"reference": ref, "description": "synthetic document", "claimed_value": claimed})
def review(w, who, cid, ref, decision="approve", value=None, note="checked"): return as_user(who).post(f"/api/conflicts/{cid}/evidence/{ref}/review", json={"decision": decision, "review_note": note, "approved_value": value})


def linked_patient(w, pid):
    return w.user("patient", linked_patient_id=pid)


def confirm(w, who, pid, cid, value): return as_user(who).post(f"/api/patients/{pid}/confirm-field", json={"conflict_id": cid, "confirmed_value": value})


# =============================================================================================================== C5 stale
def test_C5_stale_clinical_resolution_is_rejected_and_nothing_changes(w):
    pid = new_patient(w)
    cid = raise_conflict(w, pid, "dosage", "10 mg", "20 mg")
    seen = detail(w, w.clinician, cid)                           # the reviewer opens the conflict ...
    assert sync(w, op(pid, "device-A", 1, dosage="15 mg"))[0]["status"] == "applied"   # ... and a NEWER legitimate dosage arrives
    r = resolve(w, w.clinician, cid, resolution="accept_incoming", expected_field_version=seen["current_field_version"])
    assert r.status_code == 409 and code(r) == "stale_conflict"
    err = r.json()["detail"]
    assert err["current_value"] == "15 mg" and err["current_field_version"] > seen["current_field_version"]
    assert patient(w)["dosage"] == "15 mg"                        # newer clinical data NOT overwritten
    assert conflict(w, cid)["status"] == "pending"                # still open, needs a fresh review
    assert not audit(w, "CONFLICT_RESOLVED")
    assert audit(w, "CONFLICT_ACTION_DENIED")[0]["metadata"]["code"] == "stale_conflict"
    fresh = detail(w, w.clinician, cid)
    assert fresh["current_value"] == "15 mg" and fresh["changed_since_raised"] is True
    r = resolve(w, w.clinician, cid, resolution="accept_incoming", expected_field_version=fresh["current_field_version"])
    assert r.status_code == 200 and patient(w)["dosage"] == "20 mg"   # after a fresh review the decision is accepted


def test_C5_resolution_without_expected_field_version_is_refused(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "dosage", "10 mg", "20 mg")
    r = resolve(w, w.clinician, cid, resolution="accept_incoming")
    assert r.status_code == 422 and code(r) == "expected_field_version_required"
    assert patient(w)["dosage"] == "10 mg" and conflict(w, cid)["status"] == "pending"


def test_C5_any_write_through_the_field_version_check_even_by_another_resolution(w):
    pid = new_patient(w)
    c1 = raise_conflict(w, pid, "condition", "Hypertension", "Diabetes")
    c2 = sync(w, op(pid, "device-C", 1, condition="Asthma"))[0]["conflicts"][0]["conflict_id"]     # a second, different proposal for the same field
    v = detail(w, w.clinician, c1)["current_field_version"]
    assert resolve(w, w.clinician, c1, resolution="accept_incoming", expected_field_version=v).status_code == 200
    r = resolve(w, w.clinician, c2, resolution="accept_incoming", expected_field_version=v)         # decided on the pre-resolution view of the field
    assert r.status_code == 409 and code(r) == "stale_conflict" and patient(w)["condition"] == "Diabetes"


# ========================================================================================== simultaneous clinical updates
def test_two_competing_clinical_updates_never_lose_either_value(w):
    pid = new_patient(w)
    ra = sync(w, op(pid, "device-A", 1, condition="Hypertension"))[0]
    rb = sync(w, op(pid, "device-B", 1, condition="Diabetes"))[0]
    assert (ra["status"], rb["status"]) == ("applied", "conflict")
    assert patient(w)["condition"] == "Hypertension"
    cf = w.mongo.conflicts.docs[0]
    assert cf["value_a"] == "Hypertension" and cf["value_b"] == "Diabetes" and cf["category"] == "CRITICAL_CLINICAL"


# ================================================================================================= C3/C4 evidence binding
def test_C3_resolution_uses_the_clinicians_approved_value_not_the_submitted_claim(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    assert evidence(w, w.worker, cid, "DOC-1", 41).status_code == 200      # the submitter CLAIMS 41
    r = review(w, w.clinician, cid, "DOC-1", "approve", value=42)                          # the clinician certifies 42
    assert r.status_code == 200 and r.json()["approved_value"] == 42
    ev = w.mongo.evidence.docs[0]
    assert ev["claimed_value"] == 41 and ev["approved_value"] == 42 and ev["reviewed_by"] == str(w.clinician["_id"])
    v = detail(w, w.supervisor, cid)
    assert v["allowed"]["resolutions"] == ["apply_evidence"]
    r = resolve(w, w.supervisor, cid, resolution="apply_evidence", evidence_reference="DOC-1", expected_field_version=v["current_field_version"])
    assert r.status_code == 200 and r.json()["selected_value"] == 42
    assert patient(w)["age"] == 42                                                         # exactly the approved value (not 41, not value_b)
    assert conflict(w, cid)["evidence_reference"] == "DOC-1"


def test_C4_supervisor_cannot_resolve_a_document_conflict_with_an_arbitrary_value(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    v = detail(w, w.supervisor, cid)["current_field_version"]
    for body in ({"resolution": "custom", "selected_value": 99}, {"resolution": "accept_incoming"}, {"resolution": "accept_current"}):
        r = resolve(w, w.supervisor, cid, expected_field_version=v, note="because", **body)
        assert r.status_code == 400 and code(r) == "resolution_not_allowed", body
    r = resolve(w, w.supervisor, cid, resolution="apply_evidence", expected_field_version=v)
    assert code(r) == "approved_evidence_required"
    evidence(w, w.worker, cid, "DOC-2", 41)                                               # submitted but NOT yet approved
    assert code(resolve(w, w.supervisor, cid, resolution="apply_evidence", evidence_reference="DOC-2", expected_field_version=v)) == "approved_evidence_required"
    review(w, w.clinician, cid, "DOC-2", "approve", value=41)
    r = resolve(w, w.supervisor, cid, resolution="apply_evidence", evidence_reference="DOC-2", selected_value=99, expected_field_version=v)
    assert r.status_code == 400 and code(r) == "value_must_come_from_evidence" and r.json()["detail"]["approved_value"] == 41
    assert patient(w)["age"] == 40 and conflict(w, cid)["status"] == "pending"            # nothing changed by any refused attempt
    assert len(audit(w, "CONFLICT_ACTION_DENIED")) >= 4


def test_C4_even_a_clinician_must_go_through_evidence_for_document_conflicts(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "name", "Name A", "Name B")
    v = detail(w, w.clinician, cid)["current_field_version"]
    r = resolve(w, w.clinician, cid, resolution="custom", selected_value="Anything", expected_field_version=v, note="x y z")
    assert r.status_code == 400 and code(r) == "resolution_not_allowed"


def test_C3_approval_must_record_a_valid_exact_value(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    evidence(w, w.worker, cid, "DOC-3", 41)
    assert code(review(w, w.clinician, cid, "DOC-3", "approve", value=None)) == "approved_value_required"
    assert code(review(w, w.clinician, cid, "DOC-3", "approve", value="forty")) == "invalid_value"
    assert code(review(w, w.clinician, cid, "DOC-3", "approve", value=500)) == "invalid_value"
    assert review(w, w.clinician, cid, "DOC-3", "reject", value=None).json()["status"] == "rejected"


def test_C7_reviewer_cannot_approve_their_own_evidence(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    evidence(w, w.clinician, cid, "DOC-4", 41)
    r = review(w, w.clinician, cid, "DOC-4", "approve", value=41)
    assert r.status_code == 403 and code(r) == "separation_of_duties"
    assert review(w, w.clinician2, cid, "DOC-4", "approve", value=41).status_code == 200


def test_C7_evidence_input_is_validated_and_bounded(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    c = as_user(w.worker)
    assert c.post(f"/api/conflicts/{cid}/evidence", json={"reference": "x", "description": "ok ok", "claimed_value": 41}).status_code == 422
    assert c.post(f"/api/conflicts/{cid}/evidence", json={"reference": "A/B C", "description": "ok ok", "claimed_value": 41}).status_code == 422
    assert c.post(f"/api/conflicts/{cid}/evidence", json={"reference": "DOC-5", "description": "d" * 1001, "claimed_value": 41}).status_code == 422
    assert c.post(f"/api/conflicts/{cid}/evidence", json={"reference": "DOC-5", "description": "ok ok", "claimed_value": "x" * 600}).status_code == 422
    assert c.post(f"/api/conflicts/{cid}/evidence?reference=DOC-5&description=zzz").status_code == 422   # old query-string style no longer accepted


# ===================================================================================================== C6 resubmission
def test_C6_rejected_evidence_resubmission_is_a_clean_409_not_a_500(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    assert evidence(w, w.worker, cid, "DOC-6", 41).status_code == 200
    assert review(w, w.clinician, cid, "DOC-6", "reject", note="blurry scan").json()["status"] == "rejected"
    r = evidence(w, w.worker, cid, "DOC-6", 41)
    assert r.status_code == 409 and code(r) == "evidence_reference_already_used" and r.json()["detail"]["existing_status"] == "rejected"
    v = detail(w, w.clinician, cid)["current_field_version"]
    r = resolve(w, w.clinician, cid, resolution="apply_evidence", evidence_reference="DOC-6", expected_field_version=v)
    assert code(r) == "approved_evidence_required"                                       # rejected evidence can never be used
    assert evidence(w, w.worker, cid, "DOC-6B", 41).status_code == 200                    # a NEW reference is accepted
    assert code(review(w, w.clinician, cid, "DOC-6", "approve", value=41)) == "evidence_already_reviewed"
    assert len(w.mongo.evidence.docs) == 2                                                # original rejected record preserved


def test_C6_evidence_for_non_document_or_closed_conflicts_is_refused(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "A", "B")
    assert code(evidence(w, w.worker, cid, "DOC-7", "B")) == "not_document_verifiable"
    assert as_user(w.worker).post("/api/conflicts/CF-NOPE/evidence", json={"reference": "DOC-7", "description": "ok ok", "claimed_value": 1}).status_code == 404


# ===================================================================================================== C1 patient confirmation
def test_C1_patient_confirms_own_value_and_the_record_is_not_changed_until_applied(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "phone", "9111111111", "9222222222")
    p1 = linked_patient(w, pid)
    mine = as_user(p1).get("/api/me/conflicts").json()
    assert mine["linked"] and [x["conflict_id"] for x in mine["conflicts"]] == [cid]
    r = confirm(w, p1, pid, cid, "9222222222")
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert patient(w)["phone"] == "9111111111"                                             # confirmation does NOT modify the record
    conf = w.mongo.patient_confirmations.docs[0]
    assert conf["user_id"] == str(p1["_id"]) and conf["confirmed_value"] == "9222222222"
    v = detail(w, w.supervisor, cid)
    assert v["allowed"]["resolutions"] == ["apply_confirmation"] and v["confirmations"][0]["status"] == "confirmed"
    assert "user_id" not in v["confirmations"][0]
    r = resolve(w, w.supervisor, cid, resolution="apply_confirmation", patient_confirmation_id=conf["confirmation_id"], expected_field_version=v["current_field_version"])
    assert r.status_code == 200 and patient(w)["phone"] == "9222222222"
    assert conflict(w, cid)["patient_confirmation_id"] == conf["confirmation_id"]


def test_C1_a_patient_cannot_confirm_another_patients_information(w):
    p1id = new_patient(w, "SH-1001"); p2id = new_patient(w, "SH-2002")
    cid = raise_conflict(w, p1id, "phone", "9111111111", "9222222222")
    p2 = linked_patient(w, p2id); unlinked = w.user("patient")
    r = confirm(w, p2, p1id, cid, "9222222222")                    # patient 2 targets patient 1's record
    assert r.status_code == 403 and code(r) == "not_your_record"
    r = confirm(w, p2, p2id, cid, "9222222222")                    # patient 2 names their own record but patient 1's conflict
    assert r.status_code == 404 and code(r) == "conflict_not_found"
    assert code(confirm(w, unlinked, p1id, cid, "9222222222")) == "not_your_record"
    assert as_user(p2).get("/api/me/conflicts").json()["conflicts"] == []
    assert w.mongo.patient_confirmations.docs == []


def test_C1_confirmation_rules(w):
    pid = new_patient(w)
    phone = raise_conflict(w, pid, "phone", "9111111111", "9222222222")
    clinical = raise_conflict(w, pid, "condition", "A", "B")
    p1 = linked_patient(w, pid)
    assert code(confirm(w, p1, pid, clinical, "B")) == "not_patient_verifiable"            # only patient-verifiable info
    assert code(confirm(w, p1, pid, phone, "9999999999")) == "value_not_proposed"          # must be one of the two values
    assert code(confirm(w, p1, pid, phone, "")) == "invalid_value"
    assert confirm(w, p1, pid, phone, "9111111111").status_code == 200
    assert code(confirm(w, p1, pid, phone, "9222222222")) == "already_confirmed"           # one confirmation per patient per conflict
    for role_user in (w.worker, w.supervisor, w.clinician):
        assert as_user(role_user).post(f"/api/patients/{pid}/confirm-field", json={"conflict_id": phone, "confirmed_value": "9111111111"}).status_code == 403
    assert code(confirm(w, w.user("patient", linked_patient_id=pid), pid, "CF-NOPE", "x")) == "conflict_not_found"


def test_C1_patient_value_cannot_be_overridden_by_the_resolver_and_unlinking_invalidates_it(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "phone", "9111111111", "9222222222")
    p1 = linked_patient(w, pid); confirm(w, p1, pid, cid, "9222222222")
    conf = w.mongo.patient_confirmations.docs[0]["confirmation_id"]; v = detail(w, w.supervisor, cid)["current_field_version"]
    r = resolve(w, w.supervisor, cid, resolution="apply_confirmation", patient_confirmation_id=conf, selected_value="9000000000", expected_field_version=v)
    assert code(r) == "value_must_come_from_evidence"
    assert code(resolve(w, w.supervisor, cid, resolution="apply_confirmation", patient_confirmation_id="PC-FAKE", expected_field_version=v)) == "patient_confirmation_required"
    w.mongo.users.find_one({"_id": p1["_id"]}).pop("linked_patient_id")      # unlink in the stored record (the stand-in restores copies after aborted transactions)
    assert code(resolve(w, w.supervisor, cid, resolution="apply_confirmation", patient_confirmation_id=conf, expected_field_version=v)) == "confirmation_no_longer_valid"
    assert patient(w)["phone"] == "9111111111" and conflict(w, cid)["status"] == "pending"


# ================================================================================================= authorization matrix
@pytest.mark.parametrize("field,a,b", [("condition", "A", "B"), ("last_visit", "2026-10-02", "2026-10-03")])
def test_clinical_and_unclassified_conflicts_need_a_supervisor_or_clinician_never_a_worker_or_patient(w, field, a, b):
    pid = new_patient(w); cid = raise_conflict(w, pid, field, a, b)
    v = detail(w, w.clinician, cid)["current_field_version"]
    for who in (w.worker, linked_patient(w, pid)):
        for res in ("accept_current", "accept_incoming", "reject_incoming"):
            r = resolve(w, who, cid, resolution=res, expected_field_version=v, note="try it")
            assert r.status_code == 403, (who["role"], res)
    assert detail(w, w.clinician, cid)["status"] == "pending" and patient(w)[field] == a
    r = resolve(w, w.supervisor, cid, resolution="accept_incoming", expected_field_version=v)         # the supervisor approves (clinician sign-in was removed)
    assert r.status_code == 200 and patient(w)[field] == b


def test_worker_refusal_is_a_structured_role_error_naming_the_required_roles(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "A", "B")
    v = detail(w, w.clinician, cid)["current_field_version"]
    r = resolve(w, w.worker, cid, resolution="accept_incoming", expected_field_version=v)
    d = r.json()["detail"]
    assert r.status_code == 403 and d["code"] == "role_not_permitted" and d["required_roles"] == ["clinician", "supervisor"] and d["message"]


def test_other_endpoints_enforce_roles(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    evidence(w, w.worker, cid, "DOC-8", 41)
    assert review(w, w.worker, cid, "DOC-8", "approve", 41).status_code == 403
    assert evidence(w, linked_patient(w, pid), cid, "DOC-9", 41).status_code == 403
    assert as_user(w.clinician).post(f"/api/admin/patients/{pid}/link-account", json={"email": "a@example.com", "identity_verified": True, "identity_check_method": "other", "identity_check_note": "checked ok"}).status_code == 403
    assert as_user(linked_patient(w, pid)).get("/api/conflicts").status_code == 403
    assert as_user(w.worker).get(f"/api/conflicts/{cid}").status_code == 403


# ======================================================================================================= C8 reject + history
def test_C8_reject_outcome_keeps_current_value_and_full_history(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "Hypertension", "Diabetes")
    assert code(resolve(w, w.clinician, cid, resolution="reject_incoming")) == "note_required"
    r = resolve(w, w.clinician, cid, resolution="reject_incoming", note="Incoming value came from a mis-keyed visit")
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["resolved"] is False
    cf = conflict(w, cid)
    assert cf["status"] == "rejected" and patient(w)["condition"] == "Hypertension"
    assert [h["action"] for h in cf["history"]] == ["created", "rejected"] and cf["history"][-1]["actor_role"] == "clinician"
    assert audit(w, "CONFLICT_REJECTED") and not audit(w, "CONFLICT_RESOLVED")
    assert [c["conflict_id"] for c in as_user(w.supervisor).get("/api/conflicts?status=rejected").json()] == [cid]
    assert as_user(w.supervisor).get("/api/conflicts?status=pending").json() == []
    assert as_user(w.supervisor).get("/api/conflicts?status=bogus").status_code == 400


def test_accept_current_records_the_decision_without_changing_the_record(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "Hypertension", "Diabetes")
    v = detail(w, w.clinician, cid)["current_field_version"]
    r = resolve(w, w.clinician, cid, resolution="accept_current", expected_field_version=v)
    assert r.status_code == 200 and r.json()["selected_value"] == "Hypertension"
    assert patient(w)["condition"] == "Hypertension" and patient(w)["version"] == 2 and conflict(w, cid)["status"] == "resolved"


def test_custom_clinician_value_needs_a_note_and_a_valid_value(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "dosage", "10 mg", "20 mg")
    v = detail(w, w.clinician, cid)["current_field_version"]
    assert code(resolve(w, w.clinician, cid, resolution="custom", selected_value="12 mg", expected_field_version=v)) == "note_required"
    assert code(resolve(w, w.clinician, cid, resolution="custom", selected_value="", expected_field_version=v, note="reason here")) == "invalid_value"
    assert code(resolve(w, w.clinician, cid, resolution="custom", selected_value={"$gt": ""}, expected_field_version=v, note="reason here")) == "invalid_value"
    assert resolve(w, w.clinician, cid, resolution="custom", selected_value="12 mg", expected_field_version=v, note="titrated").status_code == 200
    assert patient(w)["dosage"] == "12 mg"


# ================================================================================================ duplicate / concurrent / rollback
def test_duplicate_resolution_requests_apply_once(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "dosage", "10 mg", "20 mg")
    v = detail(w, w.clinician, cid)["current_field_version"]
    first = resolve(w, w.clinician, cid, resolution="accept_incoming", expected_field_version=v)
    version = patient(w)["version"]
    second = resolve(w, w.clinician2, cid, resolution="accept_incoming", expected_field_version=v)
    assert first.status_code == 200 and second.status_code == 409 and code(second) == "conflict_not_pending"
    assert patient(w)["version"] == version and patient(w)["dosage"] == "20 mg"
    assert len(audit(w, "CONFLICT_RESOLVED")) == 1
    assert len([c for c in w.mongo.changes.docs if c["operation_id"] == "resolution:" + cid]) == 1


def test_transaction_rollback_when_a_write_fails_mid_resolution(w, monkeypatch):
    pid = new_patient(w); cid = raise_conflict(w, pid, "dosage", "10 mg", "20 mg")
    v = detail(w, w.clinician, cid)["current_field_version"]
    before = (patient(w)["version"], len(w.mongo.changes.docs), len(w.mongo.audit.docs))

    def boom(*a, **k): raise main.PyMongoError("injected failure at the last write")
    original = w.mongo.audit.insert_one
    monkeypatch.setattr(w.mongo.audit, "insert_one", boom)
    r = resolve(w, w.clinician, cid, resolution="accept_incoming", expected_field_version=v)
    assert r.status_code == 503 and code(r) == "transaction_failed"
    monkeypatch.setattr(w.mongo.audit, "insert_one", original)
    assert patient(w)["dosage"] == "10 mg" and conflict(w, cid)["status"] == "pending" and len(conflict(w, cid)["history"]) == 1
    assert (patient(w)["version"], len(w.mongo.changes.docs), len(w.mongo.audit.docs)) == before     # snapshot restore (stand-in)
    assert resolve(w, w.clinician, cid, resolution="accept_incoming", expected_field_version=v).status_code == 200


# ========================================================================================================= audit correctness
def test_audit_and_history_record_who_did_what_without_secrets(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    evidence(w, w.worker, cid, "DOC-10", 41)
    review(w, w.clinician, cid, "DOC-10", "approve", 42, note="passport page 2")
    v = detail(w, w.supervisor, cid)["current_field_version"]
    resolve(w, w.clinician2, cid, resolution="custom", expected_field_version=v)                  # refused (document conflicts need evidence)
    resolve(w, w.supervisor, cid, resolution="apply_evidence", evidence_reference="DOC-10", expected_field_version=v)
    events = [(a["event_type"], a["actor_role"]) for a in w.mongo.audit.docs if a["patient_id"] == pid and a["event_type"] != "SYNC_OPERATION" and a["event_type"] != "PATIENT_CREATED_SYNC"]
    assert events == [("EVIDENCE_SUBMITTED", "worker"), ("EVIDENCE_REVIEWED", "clinician"), ("CONFLICT_ACTION_DENIED", "clinician"), ("CONFLICT_RESOLVED", "supervisor")]
    rev = audit(w, "EVIDENCE_REVIEWED")[0]["metadata"]
    assert rev["claimed_value"] == 41 and rev["approved_value"] == 42 and rev["decision"] == "approve"
    res = audit(w, "CONFLICT_RESOLVED")[0]
    assert res["actor_id"] == str(w.supervisor["_id"]) and res["metadata"]["old_value"] == 40 and res["metadata"]["new_value"] == 42 and res["metadata"]["evidence_reference"] == "DOC-10"
    hist = conflict(w, cid)["history"]
    assert [(h["action"], h["actor_role"]) for h in hist] == [("created", "worker"), ("evidence_submitted", "worker"), ("evidence_approved", "clinician"), ("resolved", "supervisor")]
    blob = json.dumps(w.mongo.audit.docs, default=str).lower()
    assert "password" not in blob and "eyj" not in blob and "bearer" not in blob


# ============================================================================================================ C9 de-duplication
def test_C9_the_same_competing_value_does_not_create_a_second_pending_conflict(w):
    pid = new_patient(w)
    cid = raise_conflict(w, pid, "condition", "Hypertension", "Diabetes", device_b="device-B")
    again = sync(w, op(pid, "device-C", 1, condition="Diabetes"))[0]                 # another device proposes the same value
    assert again["status"] == "conflict" and again["conflicts"][0]["conflict_id"] == cid and again["conflicts"][0]["existing"] is True
    assert w.mongo.conflicts.count_documents({"patient_id": pid, "status": "pending"}) == 1
    assert [h["action"] for h in conflict(w, cid)["history"]] == ["created", "proposed_again"]
    other = sync(w, op(pid, "device-D", 1, condition="Asthma"))[0]                   # a DIFFERENT proposal is a different conflict
    assert other["conflicts"][0]["conflict_id"] != cid and w.mongo.conflicts.count_documents({"patient_id": pid, "status": "pending"}) == 2


# ============================================================================================================ C10 link account
LINK = {"identity_verified": True, "identity_check_method": "in_person_document", "identity_check_note": "Checked against a paper document"}


def link(w, who, pid, email, **over): return as_user(who).post(f"/api/admin/patients/{pid}/link-account", json={"email": email, **{**LINK, **over}})


def test_C10_linking_requires_a_recorded_identity_check_and_never_overwrites(w):
    p1 = new_patient(w, "SH-1001"); p2 = new_patient(w, "SH-2002")
    a1, a2 = w.user("patient"), w.user("patient")
    assert code(link(w, w.supervisor, p1, a1["email"], identity_verified=False)) == "identity_check_required"
    assert as_user(w.supervisor).post(f"/api/admin/patients/{p1}/link-account?email={a1['email']}").status_code == 422     # old query style refused
    assert link(w, w.supervisor, p1, a1["email"], identity_check_note="x").status_code == 422
    r = link(w, w.supervisor, p1, a1["email"])
    assert r.status_code == 200 and r.json()["already_linked"] is False
    u = w.mongo.users.find_one({"_id": a1["_id"]})
    assert u["linked_patient_id"] == p1 and u["identity_check"]["method"] == "in_person_document" and u["identity_check"]["checked_by"] == str(w.supervisor["_id"])
    assert link(w, w.supervisor, p1, a1["email"]).json()["already_linked"] is True                 # idempotent, not a second audit row
    assert len(audit(w, "PATIENT_ACCOUNT_LINKED", p1)) == 1
    assert code(link(w, w.supervisor, p2, a1["email"])) == "account_already_linked"                 # NOT silently moved to another patient
    assert w.mongo.users.find_one({"_id": a1["_id"]})["linked_patient_id"] == p1
    assert code(link(w, w.supervisor, p1, a2["email"])) == "patient_already_linked"
    assert code(link(w, w.supervisor, p2, "nobody@example.com")) == "patient_account_not_found"
    assert code(link(w, w.supervisor, "SH-NOPE", a2["email"])) == "patient_not_found"
    meta = json.dumps(audit(w, "PATIENT_ACCOUNT_LINKED", p1)[0]["metadata"])
    assert a1["email"] not in meta and "paper document" not in meta                                  # no PII / free text in the audit row


# ================================================================================================================ listing/detail
def test_detail_tells_each_role_what_it_may_do_and_shows_the_current_state(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "A", "B")
    sup, cli = detail(w, w.supervisor, cid), detail(w, w.clinician, cid)
    assert sup["allowed"]["resolutions"] == ["accept_current", "accept_incoming", "custom"] and sup["allowed"]["can_reject"] is True
    assert cli["allowed"]["resolutions"] == ["accept_current", "accept_incoming", "custom"] and cli["allowed"]["can_reject"] is True
    assert cli["current_value"] == "A" and cli["current_field_version"] == 2 and "dedupe_key" not in cli
    listed = as_user(w.supervisor).get("/api/conflicts").json()[0]
    assert listed["current_value"] == "A" and listed["current_field_version"] == 2


def test_unknown_and_legacy_resolution_names_get_a_clear_structured_400(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "A", "B")
    for legacy in ("accept_a", "accept_b", "Reviewed in SAHAAY supervisor interface"):
        r = resolve(w, w.clinician, cid, resolution=legacy, expected_field_version=2)
        d = r.json()["detail"]
        assert r.status_code == 400 and d["code"] == "unknown_resolution" and "apply_evidence" in d["allowed"] and d["message"]


def test_every_conflict_refusal_has_the_same_structured_shape(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "A", "B")
    attempts = [resolve(w, w.worker, cid, resolution="accept_current", expected_field_version=2), resolve(w, w.clinician, cid, resolution="nope"),
                resolve(w, w.clinician, "CF-NOPE", resolution="accept_current", expected_field_version=2), resolve(w, w.clinician, cid, resolution="accept_current"),
                as_user(w.worker).get("/api/conflicts")]
    for r in attempts:
        d = r.json()["detail"]
        assert r.status_code in (400, 403, 404, 422) and isinstance(d, dict) and d["code"] and d["message"], r.text


def test_synthetic_demo_conflicts_use_real_fields_and_can_never_be_resolved(w, monkeypatch):      # C11
    w.mongo.users.docs.append({"_id": ObjectId(), "role": "supervisor", "email": "s@example.com"})
    monkeypatch.setattr(main.settings, "seed_demo_data", True)
    for name in ("update_one",):
        pass
    main.bootstrap()
    demo = w.mongo.conflicts.docs
    assert len(demo) == 3 and all(c["synthetic"] and c["field"] in main.SAFE_FIELDS and c["category"] == main.category(c["field"]) for c in demo)
    pid = demo[0]["patient_id"]
    r = resolve(w, w.clinician, demo[2]["conflict_id"], resolution="accept_incoming", expected_field_version=1)
    assert r.status_code == 400 and code(r) == "synthetic_conflict"


# ---- two different people on the SAME device (a shared tablet) must still conflict, never silently overwrite each other
def test_two_workers_on_the_same_device_editing_the_same_illness_field_raise_a_conflict(w):
    pid = new_patient(w)
    assert sync(w, op(pid, "shared-tablet", 1, condition="Asthma"))[0]["status"] == "applied"               # worker 1 (w.worker), device shared-tablet
    other = w.user("worker")
    r = as_user(other).post("/api/sync/batch", json={"operations": [op(pid, "shared-tablet", 1, condition="Diabetes")]}).json()["results"][0]
    assert r["status"] == "conflict" and r["conflicts"][0]["field"] == "condition"
    assert patient(w)["condition"] == "Asthma"                                                                # the second worker did not overwrite


def test_the_same_person_editing_twice_on_their_own_device_is_still_not_a_conflict(w):
    pid = new_patient(w)
    assert sync(w, op(pid, "my-phone", 1, condition="Asthma"))[0]["status"] == "applied"
    assert sync(w, op(pid, "my-phone", 1, condition="Asthma, mild"))[0]["status"] == "applied"
