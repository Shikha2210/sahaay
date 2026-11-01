"""PHASE 4c - conflict resolution workflows per category over real HTTP, verified in the real Atlas database.
Run AFTER phase4a (creates the conflicts) and phase4b (UI check, which must leave them pending). Synthetic data only."""
import secrets
from lib import ENV, SYNTH, Report, auth, http, load_state, login, mongo, save_state

R = Report("phase4c_conflicts_resolve")
S = load_state(); U = S["users"]; pid = S["c_patient"]; ids = S["c_ids"]
c, db = mongo()
h = http()
tok = {k: login(h, U[k]["email"], U[k]["password"]).json()["access_token"] for k in ("worker1", "worker2", "clinician1", "patient1")}
tok["supervisor"] = login(h, ENV["BOOTSTRAP_SUPERVISOR_EMAIL"], ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"]).json()["access_token"]
run = secrets.token_hex(3)


def resolve(who, cid, **body): return h.post(f"/api/conflicts/{cid}/resolve", headers=auth(tok[who]), json=body)
def evidence(who, cid, ref): return h.post(f"/api/conflicts/{cid}/evidence", headers=auth(tok[who]), params={"reference": ref, "description": f"{SYNTH} supporting document description"})
def review(who, cid, ref, decision): return h.post(f"/api/conflicts/{cid}/evidence/{ref}/review", headers=auth(tok[who]), params={"decision": decision, "review_note": "synthetic review"})
def patient(): return db.patients.find_one({"patient_id": pid})
def conflict(field): return db.conflicts.find_one({"conflict_id": ids[field]})
def audit_n(event, **kw): return db.audit.count_documents({"patient_id": pid, "event_type": event, **kw})


print("== A. DOCUMENT_VERIFIABLE (age): evidence -> clinician review -> resolution")
v0 = patient()["version"]
r = resolve("supervisor", ids["age"], resolution="accept_b")
R.check("A: resolving WITHOUT approved evidence is refused (400)", r.status_code == 400 and "evidence" in r.text.lower(), r.text[:90])
r = evidence("worker1", ids["age"], f"SYN-DOC-{run}")
R.check("A: worker submits an evidence REFERENCE -> pending_review, response warns it is not authenticated", r.status_code == 200 and r.json()["status"] == "pending_review" and "not authenticate" in r.json()["warning"], r.text[:100])
R.check("A: resolving with evidence still pending review is refused", resolve("clinician1", ids["age"], resolution="accept_b", evidence_reference=f"SYN-DOC-{run}").status_code == 400)
R.check("A: supervisor cannot review evidence (403)", review("supervisor", ids["age"], f"SYN-DOC-{run}", "approve").status_code == 403)
R.check("A: worker cannot review evidence (403)", review("worker1", ids["age"], f"SYN-DOC-{run}", "approve").status_code == 403)
r = review("clinician1", ids["age"], f"SYN-DOC-{run}", "approve")
R.check("A: clinician approves the evidence", r.status_code == 200 and r.json()["status"] == "approved" and "not cryptographic" in r.json()["note"])
ev = db.evidence.find_one({"conflict_id": ids["age"]})
R.check("Atlas: evidence record stores reviewer + note + timestamp", ev["status"] == "approved" and ev["reviewed_by"] == U["clinician1"]["id"] and ev["reviewed_at"])
R.note("FAIL" if "approved_value" not in ev else "PASS", "A: the clinician records the EXACT approved value on the evidence", "GAP C3: evidence review stores approve/reject only; nothing binds the resolution value to what the evidence shows")
r = resolve("clinician1", ids["age"], resolution="accept_b", evidence_reference=f"SYN-DOC-{run}")
R.check("A: clinician resolves with the approved evidence -> 200", r.status_code == 200 and r.json()["selected_value"] == 41, r.text[:100])
p = patient(); cf = conflict("age")
R.check("Atlas: patient.age = 41, version +1; conflict resolved with actor, role, time, evidence ref kept (history preserved)", p["age"] == 41 and p["version"] == v0 + 1 and cf["status"] == "resolved" and cf["resolved_by"] == U["clinician1"]["id"] and cf["resolved_role"] == "clinician" and cf["resolved_at"] and cf["evidence_reference"] == f"SYN-DOC-{run}")
R.check("Atlas: audit CONFLICT_RESOLVED + EVIDENCE_SUBMITTED + EVIDENCE_REVIEWED written", audit_n("CONFLICT_RESOLVED", **{"metadata.field": "age"}) == 1 and audit_n("EVIDENCE_SUBMITTED") >= 1 and audit_n("EVIDENCE_REVIEWED") >= 1)
R.check("Atlas: resolution is in the change feed so other devices converge", db.changes.count_documents({"operation_id": "resolution:" + ids["age"]}) == 1)
R.check("A: resolving the same conflict again is refused (no duplicate resolution)", resolve("clinician1", ids["age"], resolution="accept_a", evidence_reference=f"SYN-DOC-{run}").status_code == 404)
R.check("Atlas: second attempt changed nothing", patient()["age"] == 41 and patient()["version"] == v0 + 1)

print("== A2. DOCUMENT_VERIFIABLE (name): rejection, resubmission, binding of the value")
ref1 = f"SYN-DOC2-{run}"
R.check("A2: worker submits evidence", evidence("worker1", ids["name"], ref1).status_code == 200)
R.check("A2: clinician REJECTS it", review("clinician1", ids["name"], ref1, "reject").json()["status"] == "rejected")
R.check("A2: resolving with rejected evidence is refused", resolve("clinician1", ids["name"], resolution="accept_b", evidence_reference=ref1).status_code == 400)
r = evidence("worker1", ids["name"], ref1)
R.check("A2: re-submitting the same reference after rejection gives a clean 4xx, not a crash", r.status_code in (400, 409), f"HTTP {r.status_code} {r.text[:40]}  (bug C6 = 500 Internal Server Error)")
ref2 = f"SYN-DOC3-{run}"
evidence("worker1", ids["name"], ref2); review("clinician1", ids["name"], ref2, "approve")
r = resolve("supervisor", ids["name"], resolution="custom", selected_value=f"{SYNTH} Arbitrary Unrelated Name", evidence_reference=ref2)
R.check("A2: a SUPERVISOR cannot resolve a document conflict with a value unrelated to the approved evidence", r.status_code in (400, 403), f"HTTP {r.status_code} (gaps C3/C4: any value is accepted and a supervisor may decide)")

print("== B. PATIENT_VERIFIABLE (phone): patient confirmation")
cfb = ids["phone"]
r = resolve("clinician1", cfb, resolution="accept_b")
R.check("B: resolving without a patient confirmation is refused (400)", r.status_code == 400 and "confirmation" in r.text.lower(), r.text[:90])
R.check("B: a fabricated confirmation id is refused (400)", resolve("clinician1", cfb, resolution="accept_b", patient_confirmation_id="FAKE-123").status_code == 400)
R.check("B: worker cannot link a patient account (403)", h.post(f"/api/admin/patients/{pid}/link-account", headers=auth(tok["worker1"]), params={"email": U["patient1"]["email"]}).status_code == 403)
R.check("B: linking an unknown account -> 404", h.post(f"/api/admin/patients/{pid}/link-account", headers=auth(tok["supervisor"]), params={"email": "nobody.synthetic@example.com"}).status_code == 404)
R.check("B: linking to an unknown patient -> 404", h.post("/api/admin/patients/SH-NOPE/link-account", headers=auth(tok["supervisor"]), params={"email": U["patient1"]["email"]}).status_code == 404)
r = h.post(f"/api/admin/patients/{pid}/link-account", headers=auth(tok["supervisor"]), params={"email": U["patient1"]["email"]})
R.check("B: supervisor links the patient's account to the record -> 200", r.status_code == 200)
R.check("Atlas: users.linked_patient_id set with linked_by + linked_at; audit PATIENT_ACCOUNT_LINKED", (u := db.users.find_one({"email": U["patient1"]["email"]}))["linked_patient_id"] == pid and u["linked_by"] and audit_n("PATIENT_ACCOUNT_LINKED") == 1)
me = h.get("/api/auth/me", headers=auth(tok["patient1"])).json()
R.check("B: patient's /me reports the linked record id", me.get("linked_patient_id") == pid)
p2 = h.post("/api/auth/register", json={"name": f"{SYNTH} Patient Two", "email": f"synthetic.patient2.{run}@example.com", "password": "Syn-Strong-Pass-77!x", "role": "patient"})
r = h.post(f"/api/admin/patients/{pid}/link-account", headers=auth(tok["supervisor"]), params={"email": f"synthetic.patient2.{run}@example.com"})
R.check("B: the same record cannot be linked to a second account (409)", r.status_code == 409)
r = h.post(f"/api/patients/{pid}/confirm-field", headers=auth(tok["patient1"]), params={"conflict_id": cfb, "confirmed_value": "9111111111"})
R.check("B: the linked patient CONFIRMS the correct value", r.status_code == 200, f"HTTP {r.status_code}: endpoint is documented but not implemented (bug C1) -> the PATIENT_VERIFIABLE workflow is BLOCKED")
R.check("B: patient can read their own linked record", h.get(f"/api/patients/{pid}", headers=auth(tok["patient1"])).status_code == 200, "HTTP 403 today (gap X8)")
R.check("Atlas: no patient_confirmations exist; conflict still pending", db.patient_confirmations.count_documents({}) == 0 and conflict("phone")["status"] == "pending")

print("== C. CRITICAL_CLINICAL (condition): clinician only")
v1 = patient()["version"]
for who in ("supervisor", "worker1", "patient1"):
    r = resolve(who, ids["condition"], resolution="accept_a")
    R.check(f"C: {who} cannot resolve a clinical conflict (403)", r.status_code == 403, f"HTTP {r.status_code}")
R.check("Atlas: still pending, patient value and version unchanged after the denied attempts", conflict("condition")["status"] == "pending" and patient()["condition"] == "Hypertension" and patient()["version"] == v1)
r = resolve("clinician1", ids["condition"], resolution="custom", selected_value="Hypertension stage 1")
R.check("C: clinician resolves with an explicit value -> 200", r.status_code == 200, r.text[:90])
cf = conflict("condition")
R.check("Atlas: patient.condition = the clinician's value; conflict resolved_by clinician; audit CONFLICT_RESOLVED(CRITICAL_CLINICAL)", patient()["condition"] == "Hypertension stage 1" and cf["resolved_by"] == U["clinician1"]["id"] and audit_n("CONFLICT_RESOLVED", **{"metadata.category": "CRITICAL_CLINICAL"}) >= 1)

print("== C2. CRITICAL_CLINICAL (dosage): a newer legitimate edit arrives after the conflict was raised")
sync = h.post("/api/sync/batch", headers=auth(tok["worker1"]), json={"operations": [{"operation_id": "synth-" + secrets.token_hex(8), "patient_id": pid, "device_id": "device-A", "base_version": 1, "changes": {"dosage": "15 mg"}}]}).json()["results"][0]
R.check("C2: device A records a newer dosage '15 mg'", sync["status"] == "applied" and patient()["dosage"] == "15 mg")
r = resolve("clinician1", ids["dosage"], resolution="accept_a")   # accept_a = the value that was current WHEN the conflict was raised ('10 mg')
R.check("C2: resolving the OLD conflict must not silently overwrite the newer '15 mg' dosage", patient()["dosage"] == "15 mg" or r.status_code in (409, 400), f"HTTP {r.status_code}; dosage is now '{patient()['dosage']}'  (bug C5: stale conflict overwrote newer clinical data with no warning)")

print("== D. REQUIRES_REVIEW (last_visit): clinician only")
R.check("D: supervisor cannot resolve an unknown/unclassified conflict (403)", resolve("supervisor", ids["last_visit"], resolution="accept_a").status_code == 403)
R.check("D: still pending afterwards (never auto-resolved)", conflict("last_visit")["status"] == "pending")
r = resolve("clinician1", ids["last_visit"], resolution="accept_b")
R.check("D: clinician resolves it -> 200 and value applied", r.status_code == 200 and patient()["last_visit"] == "2026-10-25")

print("== audit & history")
aud = h.get("/api/audit?limit=500", headers=auth(tok["clinician1"])).json()
types = {a["event_type"] for a in aud}
R.check("audit API (clinician) exposes the workflow events", {"CONFLICT_RESOLVED", "EVIDENCE_SUBMITTED", "EVIDENCE_REVIEWED", "PATIENT_ACCOUNT_LINKED", "SYNC_OPERATION", "USER_PROVISIONED"} <= types)
R.check("audit rows never contain passwords or tokens", not any(("password" in str(a).lower() and "password_hash" in str(a).lower()) or "eyJ" in str(a) for a in aud))
R.check("Atlas: resolved conflicts are kept (history preserved), none deleted", db.conflicts.count_documents({"patient_id": pid}) == 6 and db.conflicts.count_documents({"patient_id": pid, "status": "resolved"}) >= 3)
R.note("FAIL", "login events / failed logins / conflict-creation are in the audit trail", "GAP X5: logins are not audited")
c.close(); R.finish()
