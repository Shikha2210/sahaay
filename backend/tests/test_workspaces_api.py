"""Milestone 4 - the read endpoints behind the patient, worker, clinician and supervisor workspaces.
In-memory STAND-IN tests (logic only); the MongoDB query shapes are re-checked on real Atlas in tests/test_atlas_integration.py."""
import json
import pytest
from tests.test_conflict_engine import (w, as_user, new_patient, raise_conflict, evidence, review, resolve, detail, confirm, linked_patient, sync, op, link, BASE)  # noqa: F401  (fixtures/helpers reused)


def get(who, path): return as_user(who).get(path)
def code(r): return r.json()["detail"]["code"]


# --------------------------------------------------------------------------------------------- patient: /api/me/record
def test_patient_sees_only_their_own_record_without_internal_fields(w):
    p1 = new_patient(w, "SH-1001"); p2 = new_patient(w, "SH-2002")
    a = linked_patient(w, p1); b = linked_patient(w, p2)
    r = get(a, "/api/me/record").json()
    assert r["linked"] is True and r["patient_id"] == p1 and r["record"]["name"] == BASE["name"] and r["record"]["phone"] == BASE["phone"]
    assert not {"field_versions", "field_writers", "created_by", "_id"} & set(r["record"])
    assert get(b, "/api/me/record").json()["patient_id"] == p2


def test_unlinked_patient_gets_an_honest_empty_state_not_demo_data(w):
    new_patient(w)
    r = get(w.user("patient"), "/api/me/record").json()
    assert r == {"linked": False}
    assert get(w.user("patient"), "/api/me/conflicts").json() == {"linked": False, "conflicts": []}


@pytest.mark.parametrize("path", ["/api/me/record", "/api/me/conflicts"])
def test_patient_endpoints_are_patient_only(w, path):
    assert get(w.worker, path).status_code == 403 and get(w.supervisor, path).status_code == 403 and get(w.clinician, path).status_code == 403
    from fastapi.testclient import TestClient
    from app import main
    main.app.dependency_overrides.clear()
    assert TestClient(main.app).get(path).status_code == 401


def test_patient_conflicts_are_own_and_patient_verifiable_only_with_outcomes(w):
    p1 = new_patient(w, "SH-1001"); p2 = new_patient(w, "SH-2002")
    mine = raise_conflict(w, p1, "phone", "9111111111", "9222222222")
    raise_conflict(w, p1, "condition", "A", "B")                      # clinical: not shown to the patient
    raise_conflict(w, p2, "phone", "9333333333", "9444444444")         # someone else's
    a = linked_patient(w, p1)
    pending = get(a, "/api/me/conflicts").json()["conflicts"]
    assert [c["conflict_id"] for c in pending] == [mine] and pending[0]["my_confirmation"] is None
    confirm(w, a, p1, mine, "9222222222")
    v = detail(w, w.supervisor, mine)["current_field_version"]
    resolve(w, w.supervisor, mine, resolution="apply_confirmation", patient_confirmation_id=w.mongo.patient_confirmations.docs[0]["confirmation_id"], expected_field_version=v)
    assert get(a, "/api/me/conflicts").json()["conflicts"] == []
    done = get(a, "/api/me/conflicts?status=resolved").json()["conflicts"]
    assert len(done) == 1 and done[0]["selected_value"] == "9222222222" and done[0]["my_confirmation"]["confirmed_value"] == "9222222222"
    assert "resolved_by" not in json.dumps(done)                       # no staff identities leak to the patient
    assert get(a, "/api/me/conflicts?status=bogus").status_code == 400


# ---------------------------------------------------------------------------------- worker: /api/sync/conflicts (notifications)
def test_worker_sees_conflicts_from_their_own_sync_only_with_outcomes(w):
    other = w.user("worker")
    pid = new_patient(w)
    mine = raise_conflict(w, pid, "age", 40, 41)                       # raised by w.worker's operations
    as_other = as_user(other)
    theirs = as_other.post("/api/sync/batch", json={"operations": [op("SH-9009", "device-X", 0, **BASE, __create__=True)]}).json()["results"][0]
    assert theirs["status"] == "applied"
    as_other.post("/api/sync/batch", json={"operations": [op("SH-9009", "device-X", 1, condition="A")]})
    cid2 = as_other.post("/api/sync/batch", json={"operations": [op("SH-9009", "device-Y", 1, condition="B")]}).json()["results"][0]["conflicts"][0]["conflict_id"]
    mine_list = get(w.worker, "/api/sync/conflicts").json()
    assert [c["conflict_id"] for c in mine_list["conflicts"]] == [mine] and mine_list["open_count"] == 1
    assert mine_list["conflicts"][0]["can_submit_evidence"] is True and mine_list["conflicts"][0]["evidence"] == []
    assert [c["conflict_id"] for c in get(other, "/api/sync/conflicts").json()["conflicts"]] == [cid2]
    assert evidence(w, w.worker, mine, "DOC-1", 41).status_code == 200
    item = get(w.worker, "/api/sync/conflicts").json()["conflicts"][0]
    assert item["evidence"][0]["status"] == "pending_review" and item["evidence"][0]["submitted_by_me"] is True
    review(w, w.clinician, mine, "DOC-1", "approve", 42)
    v = detail(w, w.supervisor, mine)["current_field_version"]
    resolve(w, w.supervisor, mine, resolution="apply_evidence", evidence_reference="DOC-1", expected_field_version=v)
    done = get(w.worker, "/api/sync/conflicts").json()
    assert done["open_count"] == 0 and done["conflicts"][0]["status"] == "resolved" and done["conflicts"][0]["selected_value"] == 42
    assert done["conflicts"][0]["evidence"][0]["approved_value"] == 42 and done["conflicts"][0]["can_submit_evidence"] is False


def test_worker_notification_includes_conflicts_they_proposed_again_and_filters_by_status(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "condition", "A", "B")
    second = w.user("worker")
    again = as_user(second).post("/api/sync/batch", json={"operations": [op(pid, "device-C", 1, condition="B")]}).json()["results"][0]
    assert again["conflicts"][0]["conflict_id"] == cid
    assert [c["conflict_id"] for c in get(second, "/api/sync/conflicts").json()["conflicts"]] == [cid]
    assert get(second, "/api/sync/conflicts?status=resolved").json()["conflicts"] == []
    assert get(second, "/api/sync/conflicts?status=bogus").status_code == 400


def test_sync_conflicts_endpoint_rejects_patients_and_anonymous(w):
    assert get(linked_patient(w, "SH-1001"), "/api/sync/conflicts").status_code == 403


# ------------------------------------------------------------------------------------------- clinician: /api/evidence
def test_clinician_evidence_queue_lists_pending_items_with_context_and_ownership(w):
    pid = new_patient(w); cid = raise_conflict(w, pid, "age", 40, 41)
    evidence(w, w.worker, cid, "DOC-A", 41); evidence(w, w.clinician, cid, "DOC-B", 41)
    q = get(w.clinician, "/api/evidence").json()
    assert {e["evidence_reference"] for e in q} == {"DOC-A", "DOC-B"}
    a = next(e for e in q if e["evidence_reference"] == "DOC-A"); b = next(e for e in q if e["evidence_reference"] == "DOC-B")
    assert a["field"] == "age" and a["patient_id"] == pid and a["claimed_value"] == 41 and a["submitted_by_me"] is False and b["submitted_by_me"] is True
    review(w, w.clinician2, cid, "DOC-B", "reject")
    assert {e["evidence_reference"] for e in get(w.clinician, "/api/evidence").json()} == {"DOC-A"}
    assert [e["status"] for e in get(w.clinician, "/api/evidence?status=rejected").json()] == ["rejected"]
    assert len(get(w.supervisor, "/api/evidence?status=all").json()) == 2             # supervisors may monitor ...
    assert review(w, w.supervisor, cid, "DOC-A", "approve", 41).status_code == 403    # ... but never review
    assert get(w.clinician, "/api/evidence?status=bogus").status_code == 400


def test_evidence_queue_is_closed_to_workers_and_patients(w):
    assert get(w.worker, "/api/evidence").status_code == 403 and get(w.user("patient"), "/api/evidence").status_code == 403


# ----------------------------------------------------------------------------------- supervisor: /api/admin/patient-accounts
def test_patient_account_list_is_supervisor_only_and_has_no_secrets(w):
    pid = new_patient(w); a1 = w.user("patient", password_hash="$2b$12$SECRETHASH", created_at="2026-11-01"); a2 = w.user("patient", created_at="2026-11-02")
    assert link(w, w.supervisor, pid, a1["email"]).status_code == 200
    rows = get(w.supervisor, "/api/admin/patient-accounts").json()
    by = {r["email"]: r for r in rows}
    assert by[a1["email"]]["linked_patient_id"] == pid and by[a1["email"]]["identity_check_method"] == "in_person_document"
    assert by[a2["email"]]["linked_patient_id"] is None
    assert "SECRETHASH" not in json.dumps(rows) and "password" not in json.dumps(rows).lower()
    for who in (w.worker, w.clinician, linked_patient(w, pid)):
        assert get(who, "/api/admin/patient-accounts").status_code == 403


def test_linking_from_the_ui_contract_still_refuses_without_the_identity_attestation(w):
    pid = new_patient(w); acct = w.user("patient")
    r = link(w, w.supervisor, pid, acct["email"], identity_verified=False)
    assert r.status_code == 422 and code(r) == "identity_check_required"
    assert w.mongo.users.find_one({"_id": acct["_id"]}).get("linked_patient_id") is None
