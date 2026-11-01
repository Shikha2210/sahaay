"""PHASE 1 - backend, bootstrap, HTTP authentication (real server + real Atlas `sahaay` database)."""
import base64
import json
from lib import ENV, Report, auth, http, login, mongo, save_state

R = Report("phase1_backend")
c, db = mongo()
h = http()

# 1-2. server + MongoDB + indexes
r = h.get("/health")
R.check("GET /health -> 200 mongodb connected", r.status_code == 200 and r.json().get("mongodb") == "connected", f"database={r.json().get('database')}")
idx = {n: sorted(i for i in db[n].index_information() if i != "_id_") for n in ("users", "patients", "operations", "conflicts", "changes", "evidence", "patient_confirmations", "audit")}
expected_unique = {"users": "email_1", "patients": "patient_id_1", "operations": "operation_id_1", "conflicts": "conflict_id_1", "changes": "seq_1"}
for coll, name in expected_unique.items():
    info = db[coll].index_information().get(name, {})
    R.check(f"index {coll}.{name} exists and is unique", bool(info.get("unique")))
R.check("sahaay collections created by init_db", {"users", "patients", "operations", "conflicts", "changes", "counters", "audit"} <= set(db.list_collection_names()), ", ".join(sorted(db.list_collection_names())))
cnt = db.counters.find_one({"_id": "global_change_seq"})
R.check("change-sequence counter initialised", cnt is not None and cnt["value"] >= 0)

# 3. bootstrap in the real database
sup_email = ENV["BOOTSTRAP_SUPERVISOR_EMAIL"].lower()
sups = list(db.users.find({"role": "supervisor"}))
R.check("exactly one supervisor exists (bootstrap)", len(sups) == 1, f"count={len(sups)}")
s = sups[0]
R.check("supervisor email = BOOTSTRAP_SUPERVISOR_EMAIL, active", s["email"] == sup_email and s["active"] is True)
R.check("password stored as bcrypt hash (not plaintext)", s["password_hash"].startswith("$2") and ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"] not in json.dumps(s, default=str))
R.check("no demo patients seeded (SEED_DEMO_DATA=false)", db.patients.count_documents({}) == 0)
R.check("no demo conflicts seeded", db.conflicts.count_documents({}) == 0)

# 4. supervisor login through the real endpoint
r = login(h, sup_email, ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"])
ok = r.status_code == 200
R.check("supervisor login -> 200", ok)
body = r.json() if ok else {}
tok = body.get("access_token", "")
R.check("response user = supervisor with id; no password hash leaked", body.get("user", {}).get("role") == "supervisor" and body["user"].get("id") and "password" not in json.dumps(body).lower())
payload = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "==")) if tok else {}
R.check("token lifetime = 60 minutes, minimal claims (sub/role/iat/exp, no email)", payload.get("exp", 0) - payload.get("iat", 0) == 3600 and set(payload) == {"sub", "role", "iat", "exp"})
me = h.get("/api/auth/me", headers=auth(tok))
R.check("GET /api/auth/me with token -> supervisor", me.status_code == 200 and me.json()["role"] == "supervisor")
save_state({"supervisor_token_fetched": True})

# 5. invalid credentials + unauthorized
bad = login(h, sup_email, "Wrong-Password-123!")
unk = login(h, "nobody.synthetic@example.com", "Wrong-Password-123!")
R.check("wrong password -> 401 generic message", bad.status_code == 401 and bad.json()["detail"] == "Incorrect email or password")
R.check("unknown email -> identical 401 (no account enumeration)", unk.status_code == 401 and unk.json() == bad.json())
codes = [login(h, "lockout.synthetic@example.com", "Wrong-Password-123!").status_code for _ in range(7)]
R.check("rate limit: 5x401 then 429", codes[:5] == [401] * 5 and 429 in codes[5:], str(codes))
R.check("valid supervisor still not locked by other account's failures", login(h, sup_email, ENV["BOOTSTRAP_SUPERVISOR_PASSWORD"]).status_code == 200)
for method, path in [("get", "/api/patients"), ("get", "/api/audit"), ("get", "/api/conflicts"), ("post", "/api/admin/users"), ("post", "/api/sync/batch"), ("get", "/api/sync/pull")]:
    rr = getattr(h, method)(path)
    R.check(f"no token: {method.upper()} {path} -> 401", rr.status_code == 401)
rr = h.get("/api/patients", headers=auth("garbage.token.value"))
R.check("garbage token -> 401 token_invalid", rr.status_code == 401 and rr.json()["detail"]["code"] == "token_invalid")
rr = h.post("/api/auth/register", json={"name": "Eve Synthetic", "email": "eve.synthetic@example.com", "password": "Syn-Strong-Pass-9!", "role": "supervisor"})
R.check("public registration of a supervisor -> 403", rr.status_code == 403)
R.check("no eve account was created", db.users.count_documents({"email": "eve.synthetic@example.com"}) == 0)

# headers / CORS
rr = h.get("/api/auth/me")
R.check("security headers present", rr.headers.get("x-content-type-options") == "nosniff" and rr.headers.get("cache-control") == "no-store" and rr.headers.get("x-frame-options") == "DENY")
ok_origin = h.options("/api/auth/login", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
bad_origin = h.options("/api/auth/login", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
R.check("CORS preflight allowed for the Vite origin", ok_origin.headers.get("access-control-allow-origin") == "http://localhost:5173")
R.check("CORS preflight NOT allowed for another origin", "access-control-allow-origin" not in bad_origin.headers)
c.close()
R.finish()
