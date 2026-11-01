"""Milestone 3 UI verification - SETUP. Refuses to run unless E2E_DATABASE is a disposable `sahaay_test_*` database, so it can
never write to the real `sahaay` data. Creates synthetic staff + one patient with one pending conflict per category,
a linked patient account with a confirmation, and submitted evidence, using the real HTTP API."""
import os
import secrets
import sys

from lib import ENV, SYNTH, auth, http, login, mongo, save_state, strong_password

dbname = os.environ.get("E2E_DATABASE", "")
if not dbname.startswith("sahaay_test_") or dbname == "sahaay":
    sys.exit("REFUSING to run: set E2E_DATABASE to a disposable sahaay_test_* database (the backend must be using it too).")
c, db = mongo()
h = http()
assert h.get("/health").json()["database"] == dbname, "the running backend is not using the disposable database"
sup = login(h, ENV["BOOTSTRAP_SUPERVISOR_EMAIL"], ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"]).json()["access_token"]
run = secrets.token_hex(3)
users = {}
for key, role in (("worker1", "worker"), ("worker2", "worker"), ("clinician1", "clinician"), ("clinician2", "clinician")):
    pw = strong_password(); email = f"synthetic.{key}.{run}@example.com"
    assert h.post("/api/admin/users", headers=auth(sup), json={"name": f"{SYNTH} {key}", "email": email, "password": pw, "role": role}).status_code == 201
    users[key] = {"email": email, "password": pw, "role": role}
pw = strong_password(); email = f"synthetic.patient1.{run}@example.com"
r = h.post("/api/auth/register", json={"name": f"{SYNTH} Patient", "email": email, "password": pw, "role": "patient"})
users["patient1"] = {"email": email, "password": pw, "role": "patient", "id": r.json()["user"]["id"]}
tok = {k: login(h, u["email"], u["password"]).json()["access_token"] for k, u in users.items()}
for k, u in users.items(): u["id"] = login(h, u["email"], u["password"]).json()["user"]["id"]

pid = f"SH-M3{run.upper()}"


def sync(token, *ops): return h.post("/api/sync/batch", headers=auth(token), json={"operations": list(ops)}).json()["results"]
def op(device, base, **ch): return {"operation_id": "m3-" + secrets.token_hex(8), "patient_id": pid, "device_id": device, "base_version": base, "changes": ch}


base = {"name": f"{SYNTH} M3 Patient {run}", "age": 30, "phone": "9000000001", "village": "Synthetic Village", "condition": "Routine care", "dosage": "5 mg", "last_visit": "2026-10-01", "identity_status": "UNVERIFIED", "__create__": True}
assert sync(tok["worker1"], op("device-A", 0, **base))[0]["status"] == "applied"
ids = {}
for field, a, b in (("condition", "Hypertension", "Diabetes"), ("dosage", "10 mg", "20 mg"), ("age", 40, 41), ("phone", "9111111111", "9222222222"), ("last_visit", "2026-10-20", "2026-10-25")):
    assert sync(tok["worker1"], op("device-A", 1, **{field: a}))[0]["status"] == "applied"
    res = sync(tok["worker1"], op("device-B", 1, **{field: b}))[0]
    assert res["status"] == "conflict"; ids[field] = res["conflicts"][0]["conflict_id"]

# document conflict: a worker submits evidence claiming 41 (the clinician will approve a DIFFERENT value, 42, in the UI)
assert h.post(f"/api/conflicts/{ids['age']}/evidence", headers=auth(tok["worker1"]), json={"reference": f"SYN-DOC-{run}", "description": "synthetic passport page", "claimed_value": 41}).status_code == 200
# patient conflict: supervisor links the account after a recorded identity check, then the patient confirms
assert h.post(f"/api/admin/patients/{pid}/link-account", headers=auth(sup), json={"email": users["patient1"]["email"], "identity_verified": True, "identity_check_method": "in_person_document", "identity_check_note": "synthetic check"}).status_code == 200
tok["patient1"] = login(h, users["patient1"]["email"], users["patient1"]["password"]).json()["access_token"]
assert h.post(f"/api/patients/{pid}/confirm-field", headers=auth(tok["patient1"]), json={"conflict_id": ids["phone"], "confirmed_value": "9222222222"}).status_code == 200
save_state({"run": run, "users": users, "m3_patient": pid, "m3_ids": ids, "m3_db": dbname})
print(f"setup ok in {dbname}: patient {pid}, conflicts {sorted(ids)}")
c.close()
