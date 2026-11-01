"""Milestone 4 - authorization is enforced by the BACKEND, not the UI. Real HTTP calls with each role's token against the live
server and the disposable database (refuses to run otherwise). Run AFTER phase_m4_workspaces.mjs so the data exists."""
import os
import sys

from lib import ENV, Report, auth, http, load_state, login, mongo

if not os.environ.get("E2E_DATABASE", "").startswith("sahaay_test_"):
    sys.exit("REFUSING to run: E2E_DATABASE must be a disposable sahaay_test_* database")
R = Report("m4_api_checks")
S = load_state(); U = S["users"]; P1, P2, ids = S["P1"], S["P2"], S["ids"]
h = http()
tok = {k: login(h, U[k]["email"], U[k]["password"]).json()["access_token"] for k in ("worker1", "worker2", "clinician1", "patient1", "patient2")}
tok["supervisor"] = login(h, ENV["BOOTSTRAP_SUPERVISOR_EMAIL"], ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"]).json()["access_token"]
def code(r): d = r.json().get("detail"); return d.get("code") if isinstance(d, dict) else d
def get(who, path): return h.get(path, headers=auth(tok[who]))
def post(who, path, **kw): return h.post(path, headers=auth(tok[who]), **kw)

NEW_ENDPOINTS = ["/api/me/record", "/api/me/conflicts", "/api/sync/conflicts", "/api/evidence", "/api/admin/patient-accounts"]
for p in NEW_ENDPOINTS:
    R.check(f"no token: GET {p} -> 401", h.get(p).status_code == 401)

# patients: own data only
a = get("patient1", "/api/me/record").json(); b = get("patient2", "/api/me/record").json()
R.check("patient1 /api/me/record is patient one's record only", a["patient_id"] == P1 and a["record"]["name"] == S["names"][P1])
R.check("patient2 /api/me/record is patient two's record only", b["patient_id"] == P2 and b["record"]["name"] == S["names"][P2])
R.check("patient record has no internal bookkeeping fields", not {"field_versions", "field_writers", "created_by", "_id"} & set(a["record"]))
for path in ("/api/patients", f"/api/patients/{P2}", f"/api/patients/{P1}", "/api/conflicts", "/api/evidence", "/api/sync/conflicts", "/api/admin/patient-accounts", "/api/sync/pull", "/api/audit"):
    R.check(f"patient: GET {path} -> 403", get("patient1", path).status_code == 403)
r = post("patient1", f"/api/patients/{P2}/confirm-field", json={"conflict_id": ids["p2_phone"], "confirmed_value": "9555555555"})
R.check("patient one cannot confirm patient two's information (403 not_your_record)", r.status_code == 403 and code(r) == "not_your_record")
r = post("patient1", f"/api/patients/{P1}/confirm-field", json={"conflict_id": ids["p2_phone"], "confirmed_value": "9555555555"})
R.check("...nor name another patient's conflict on their own record (404)", r.status_code == 404)
R.check("patient2 /api/me/conflicts never contains patient one's conflicts", all(c["patient_id"] == P2 for c in get("patient2", "/api/me/conflicts?status=all").json()["conflicts"]))

# workers
for path in ("/api/admin/patient-accounts", "/api/evidence", "/api/conflicts", "/api/audit", "/api/me/record", "/api/me/conflicts"):
    R.check(f"worker: GET {path} -> 403", get("worker1", path).status_code == 403)
w1 = get("worker1", "/api/sync/conflicts?status=all").json()["conflicts"]; w2 = get("worker2", "/api/sync/conflicts?status=all").json()["conflicts"]
R.check("worker one's notifications contain only conflicts on patient one", w1 and all(c["patient_id"] == P1 for c in w1))
R.check("worker two's notifications contain only conflicts on patient two", w2 and all(c["patient_id"] == P2 for c in w2))
R.check("worker cannot review evidence (403)", post("worker1", f"/api/conflicts/{ids['age']}/evidence/REG-M4-1/review", json={"decision": "approve", "review_note": "nope", "approved_value": 41}).status_code == 403)
R.check("worker cannot resolve a conflict (403)", post("worker1", f"/api/conflicts/{ids['p2_phone']}/resolve", json={"resolution": "accept_current", "expected_field_version": 1}).status_code == 403)
R.check("worker cannot link an account (403)", post("worker1", f"/api/admin/patients/{P1}/link-account", json={"email": U["patient1"]["email"], "identity_verified": True, "identity_check_method": "other", "identity_check_note": "worker tries"}).status_code == 403)

# clinicians and supervisors
R.check("clinician cannot link an account (403)", post("clinician1", f"/api/admin/patients/{P1}/link-account", json={"email": U["patient1"]["email"], "identity_verified": True, "identity_check_method": "other", "identity_check_note": "clinician tries"}).status_code == 403)
R.check("clinician cannot list patient accounts (403)", get("clinician1", "/api/admin/patient-accounts").status_code == 403)
R.check("clinician cannot confirm as a patient (403)", post("clinician1", f"/api/patients/{P2}/confirm-field", json={"conflict_id": ids["p2_phone"], "confirmed_value": "9555555555"}).status_code == 403)
R.check("supervisor can list evidence read-only but cannot review it (403)", get("supervisor", "/api/evidence?status=all").status_code == 200 and post("supervisor", f"/api/conflicts/{ids['age']}/evidence/REG-M4-1/review", json={"decision": "approve", "review_note": "nope", "approved_value": 41}).status_code == 403)
r = post("supervisor", f"/api/admin/patients/{P1}/link-account", json={"email": U["patient2"]["email"], "identity_verified": True, "identity_check_method": "other", "identity_check_note": "attempt to move"})
R.check("supervisor cannot re-link an already-linked account or record (409; links are never moved)", r.status_code == 409 and code(r) in ("patient_already_linked", "account_already_linked"), f"{r.status_code} {code(r)}")
r = post("supervisor", f"/api/admin/patients/{P1}/link-account", json={"email": U["patient1"]["email"], "identity_verified": False, "identity_check_method": "other", "identity_check_note": "no attestation"})
R.check("linking without the identity attestation is refused (422)", r.status_code == 422)
rows = get("supervisor", "/api/admin/patient-accounts").json()
R.check("patient-account list exposes no password data", "password" not in str(rows).lower())
c, db = mongo()
R.check("database: audit trail has the link, the evidence review, and the resolutions - each with an actor", all(db.audit.count_documents({"event_type": t, "actor_id": {"$exists": True}}) >= 1 for t in ("PATIENT_ACCOUNT_LINKED", "EVIDENCE_SUBMITTED", "EVIDENCE_REVIEWED", "CONFLICT_RESOLVED", "PATIENT_CONFIRMATION")))
c.close(); R.finish()
