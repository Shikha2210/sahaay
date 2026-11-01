"""PHASE 4a - create one REAL conflict per category via two competing devices; verify classification and no last-write-wins.
Categories (by field): age/name = DOCUMENT_VERIFIABLE, phone = PATIENT_VERIFIABLE, condition/dosage = CRITICAL_CLINICAL,
last_visit = REQUIRES_REVIEW. Synthetic data only."""
import secrets
from lib import SYNTH, Report, auth, http, load_state, login, mongo, save_state

R = Report("phase4a_conflicts_create")
S = load_state()
c, db = mongo()
h = http()
tok = {k: login(h, S["users"][k]["email"], S["users"][k]["password"]).json()["access_token"] for k in ("worker1", "worker2")}
run = secrets.token_hex(3).upper()
pid = f"SH-Z{run}C"


def sync(token, *ops):
    r = h.post("/api/sync/batch", headers=auth(token), json={"operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def op(device, base, **changes): return {"operation_id": "synth-" + secrets.token_hex(8), "patient_id": pid, "device_id": device, "base_version": base, "changes": changes}


base = {"name": f"{SYNTH} Conflict Patient {run}", "age": 30, "phone": "9000000001", "village": "Synthetic Village", "condition": "Routine care", "dosage": "5 mg", "last_visit": "2026-10-01", "identity_status": "UNVERIFIED", "__create__": True}
R.check("synthetic patient created (device A)", sync(tok["worker1"], op("device-A", 0, **base))[0]["status"] == "applied")

cases = [("age", "DOCUMENT_VERIFIABLE", 40, 41), ("name", "DOCUMENT_VERIFIABLE", f"{SYNTH} Name-A {run}", f"{SYNTH} Name-B {run}"),
         ("phone", "PATIENT_VERIFIABLE", "9111111111", "9222222222"), ("condition", "CRITICAL_CLINICAL", "Hypertension", "Diabetes"),
         ("dosage", "CRITICAL_CLINICAL", "10 mg", "20 mg"), ("last_visit", "REQUIRES_REVIEW", "2026-10-20", "2026-10-25")]
ids = {}
for field, cat, a, b in cases:
    ra = sync(tok["worker1"], op("device-A", 1, **{field: a}))[0]          # device A edits first
    rb = sync(tok["worker2"], op("device-B", 1, **{field: b}))[0]          # device B edits the SAME field from the same stale base
    R.check(f"{cat} ({field}): device A applied, device B -> conflict", ra["status"] == "applied" and rb["status"] == "conflict", f"{ra['status']}/{rb['status']}")
    cf = db.conflicts.find_one({"patient_id": pid, "field": field})
    ok = cf and cf["category"] == cat and cf["status"] == "pending" and cf["value_a"] == a and cf["value_b"] == b
    R.check(f"Atlas: conflict stored as {cat}, pending, value_a/value_b recorded", bool(ok))
    ids[field] = cf["conflict_id"] if cf else None
p = db.patients.find_one({"patient_id": pid})
R.check("NO last-write-wins: every contested field still holds device A's value; B's values only exist in the conflicts",
        p["age"] == 40 and p["name"].endswith(f"Name-A {run}") and p["phone"] == "9111111111" and p["condition"] == "Hypertension" and p["dosage"] == "10 mg" and p["last_visit"] == "2026-10-20")
R.check("6 pending conflicts in Atlas for this patient", db.conflicts.count_documents({"patient_id": pid, "status": "pending"}) == 6)
R.check("each conflict was audited inside the sync transaction (SYNC_OPERATION with conflict metadata)", db.audit.count_documents({"patient_id": pid, "event_type": "SYNC_OPERATION", "metadata.conflicts.0": {"$exists": True}}) == 6)
sup = login(h, __import__("lib").ENV["BOOTSTRAP_SUPERVISOR_EMAIL"], __import__("lib").ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"]).json()["access_token"]
listed = {x["conflict_id"] for x in h.get("/api/conflicts?status=pending", headers=auth(sup)).json()}
R.check("API: supervisor's GET /api/conflicts lists all six", set(ids.values()) <= listed)
R.check("API: a worker cannot list conflicts (403) - workers get no conflict notification feed (gap S11)", h.get("/api/conflicts", headers=auth(tok["worker1"])).status_code == 403)
save_state({"c_patient": pid, "c_ids": ids})
c.close(); R.finish()
