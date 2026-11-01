"""Milestone 4 browser verification - SETUP. Runs ONLY against a disposable `sahaay_test_*` database (refuses otherwise).
Creates synthetic staff and patients through the real HTTP API:
  * P1 (patient one): conflicts on age (document, from worker1), phone (patient-verifiable, worker1), condition (clinical, worker1)
  * P2 (patient two): a phone conflict raised by worker2, and patient2's account ALREADY linked to P2
  * patient1's account is registered but deliberately NOT linked (the supervisor links it in the browser)"""
import os
import secrets
import sys

from lib import ENV, SYNTH, auth, http, login, mongo, save_state, strong_password

dbname = os.environ.get("E2E_DATABASE", "")
if not dbname.startswith("sahaay_test_") or dbname == "sahaay":
    sys.exit("REFUSING to run: set E2E_DATABASE to a disposable sahaay_test_* database (the backend must be using it too).")
c, db = mongo(); h = http()
assert h.get("/health").json()["database"] == dbname, "the running backend is not using the disposable database"
sup = login(h, ENV["BOOTSTRAP_SUPERVISOR_EMAIL"], ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"]).json()["access_token"]
run = secrets.token_hex(3)
users = {}
for key, role in (("worker1", "worker"), ("worker2", "worker"), ("clinician1", "clinician"), ("clinician2", "clinician")):
    pw = strong_password(); email = f"synthetic.{key}.{run}@example.com"
    assert h.post("/api/admin/users", headers=auth(sup), json={"name": f"{SYNTH} {key}", "email": email, "password": pw, "role": role}).status_code == 201
    users[key] = {"email": email, "password": pw, "role": role}
for key in ("patient1", "patient2"):
    pw = strong_password(); email = f"synthetic.{key}.{run}@example.com"
    r = h.post("/api/auth/register", json={"name": f"{SYNTH} Account {key[-1]}", "email": email, "password": pw, "role": "patient"})
    assert r.status_code == 200
    users[key] = {"email": email, "password": pw, "role": "patient", "name": f"{SYNTH} Account {key[-1]}"}
tok = {}
for k, u in users.items():
    r = login(h, u["email"], u["password"]).json(); tok[k] = r["access_token"]; u["id"] = r["user"]["id"]


def sync(token, *ops): return h.post("/api/sync/batch", headers=auth(token), json={"operations": list(ops)}).json()["results"]
def op(pid, device, base, **ch): return {"operation_id": "m4-" + secrets.token_hex(8), "patient_id": pid, "device_id": device, "base_version": base, "changes": ch}


P1, P2 = f"SH-M4A{run.upper()}", f"SH-M4B{run.upper()}"
names = {P1: f"{SYNTH} Patient One {run}", P2: f"{SYNTH} Patient Two {run}"}
base = lambda pid, phone: {"name": names[pid], "age": 30, "phone": phone, "village": "Synthetic Village", "condition": "Routine care", "last_visit": "2026-10-01", "identity_status": "UNVERIFIED", "__create__": True}
assert sync(tok["worker1"], op(P1, "device-A", 0, **base(P1, "9000000001")))[0]["status"] == "applied"
assert sync(tok["worker2"], op(P2, "device-W2", 0, **base(P2, "9000000002")))[0]["status"] == "applied"
ids = {}
for field, a, b in (("age", 40, 41), ("phone", "9111111111", "9222222222"), ("condition", "Hypertension", "Diabetes")):
    assert sync(tok["worker1"], op(P1, "device-A", 1, **{field: a}))[0]["status"] == "applied"
    r = sync(tok["worker1"], op(P1, "device-B", 1, **{field: b}))[0]; assert r["status"] == "conflict"; ids[field] = r["conflicts"][0]["conflict_id"]
assert sync(tok["worker2"], op(P2, "device-W2", 1, phone="9555555555"))[0]["status"] == "applied"
r = sync(tok["worker2"], op(P2, "device-W2b", 1, phone="9666666666"))[0]; assert r["status"] == "conflict"; ids["p2_phone"] = r["conflicts"][0]["conflict_id"]
body = {"identity_verified": True, "identity_check_method": "in_person_document", "identity_check_note": "synthetic setup check"}
assert h.post(f"/api/admin/patients/{P2}/link-account", headers=auth(sup), json={"email": users["patient2"]["email"], **body}).status_code == 200
save_state({"run": run, "users": users, "P1": P1, "P2": P2, "names": names, "ids": ids, "db": dbname})
print(f"m4 setup ok in {dbname}: {P1} (patient1 NOT linked), {P2} (patient2 linked); conflicts {sorted(ids)}")
c.close()
