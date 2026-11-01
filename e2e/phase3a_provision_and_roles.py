"""PHASE 3 (part A) - supervisor provisions SYNTHETIC staff; role matrix over real HTTP; audit rows in Atlas.
Accounts are created in the real `sahaay` database and are clearly labelled ZZ-SYNTHETIC. Nothing is deleted."""
import secrets
from lib import ENV, SYNTH, Report, auth, http, login, mongo, save_state, strong_password

R = Report("phase3a_provision_and_roles")
c, db = mongo()
h = http()
run = secrets.token_hex(3)
sup = login(h, ENV["BOOTSTRAP_SUPERVISOR_EMAIL"], ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"]).json()["access_token"]

users = {}
for key, role, label in [("worker1", "worker", "Worker One"), ("worker2", "worker", "Worker Two"), ("clinician1", "clinician", "Clinician One")]:
    email = f"synthetic.{key}.{run}@example.com"
    pw = strong_password()
    r = h.post("/api/admin/users", headers=auth(sup), json={"name": f"{SYNTH} {label}", "email": email, "password": pw, "role": role})
    R.check(f"supervisor provisions {role} '{key}' -> 201", r.status_code == 201, r.text[:80] if r.status_code != 201 else "")
    users[key] = {"email": email, "password": pw, "role": role}
r = h.post("/api/auth/register", json={"name": f"{SYNTH} Patient One", "email": f"synthetic.patient1.{run}@example.com", "password": (pw := strong_password()), "role": "patient"})
R.check("patient self-registration -> 200 with patient role", r.status_code == 200 and r.json()["user"]["role"] == "patient")
users["patient1"] = {"email": f"synthetic.patient1.{run}@example.com", "password": pw, "role": "patient", "id": r.json()["user"]["id"]}
dup = h.post("/api/admin/users", headers=auth(sup), json={"name": "dup", "email": users["worker1"]["email"], "password": strong_password(), "role": "worker"})
R.check("duplicate email -> 409", dup.status_code == 409)
bad_role = h.post("/api/admin/users", headers=auth(sup), json={"name": "Bad Role", "email": f"synthetic.badrole.{run}@example.com", "password": strong_password(), "role": "patient"})
R.check("provisioning role 'patient' via admin endpoint -> 400", bad_role.status_code == 400)

tokens = {}
for key, u in users.items():
    r = login(h, u["email"], u["password"])
    R.check(f"{key} logs in via HTTP", r.status_code == 200 and r.json()["user"]["role"] == u["role"])
    tokens[key] = r.json()["access_token"]
    u["id"] = r.json()["user"]["id"]
save_state({"run": run, "users": users})

# role matrix (real HTTP, real DB)
def code(method, path, tok, **kw): return getattr(h, method)(path, headers=auth(tok), **kw).status_code
R.check("worker: GET /api/patients -> 200", code("get", "/api/patients", tokens["worker1"]) == 200)
R.check("worker: GET /api/audit -> 403", code("get", "/api/audit", tokens["worker1"]) == 403)
R.check("worker: GET /api/conflicts -> 403", code("get", "/api/conflicts", tokens["worker1"]) == 403)
R.check("worker: POST /api/admin/users -> 403", code("post", "/api/admin/users", tokens["worker1"], json={"name": "x y", "email": "x@example.com", "password": "Aa1!aaaaaaaa", "role": "worker"}) == 403)
R.check("patient: GET /api/patients -> 403", code("get", "/api/patients", tokens["patient1"]) == 403)
R.check("patient: GET /api/sync/pull -> 403", code("get", "/api/sync/pull", tokens["patient1"]) == 403)
R.check("patient: POST /api/sync/batch -> 403", code("post", "/api/sync/batch", tokens["patient1"], json={"operations": []}) == 403)
R.check("clinician: POST /api/admin/users -> 403", code("post", "/api/admin/users", tokens["clinician1"], json={"name": "x y", "email": "x@example.com", "password": "Aa1!aaaaaaaa", "role": "worker"}) == 403)
R.check("clinician: GET /api/audit -> 200", code("get", "/api/audit", tokens["clinician1"]) == 200)
R.check("supervisor: GET /api/conflicts -> 200", code("get", "/api/conflicts", sup) == 200)

# database state
for key, u in users.items():
    d = db.users.find_one({"email": u["email"]})
    R.check(f"Atlas: {key} stored with role={u['role']}, bcrypt hash, active", d and d["role"] == u["role"] and d["password_hash"].startswith("$2") and d["active"])
n = db.audit.count_documents({"event_type": "USER_PROVISIONED"})
R.check("Atlas audit: USER_PROVISIONED rows written by supervisor", n >= 3, f"rows={n}")
a = db.audit.find_one({"event_type": "USER_PROVISIONED"})
R.check("audit row has actor id/role + timestamp, and no password", a and a["actor_role"] == "supervisor" and a["created_at"] and "password" not in str(a).lower())
ev = [x for x in db.audit.find({"event_type": {"$in": ["LOGIN", "LOGIN_FAILED"]}})]
R.note("GAP", "login success/failure audit events", f"{len(ev)} rows - logins are NOT audited (known gap X5)")
c.close(); R.finish()
