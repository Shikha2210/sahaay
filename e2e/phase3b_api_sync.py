"""PHASE 3 (part B) - sync semantics over real HTTP with the real Atlas `sahaay` database. Synthetic data only."""
import secrets
from lib import SYNTH, Report, auth, http, load_state, login, mongo

R = Report("phase3b_api_sync")
S = load_state()
c, db = mongo()
h = http()
tok = {k: login(h, S["users"][k]["email"], S["users"][k]["password"]).json()["access_token"] for k in ("worker1", "worker2", "clinician1")}
run = secrets.token_hex(3).upper()


def sync(token, *ops):
    r = h.post("/api/sync/batch", headers=auth(token), json={"operations": list(ops)})
    assert r.status_code == 200, r.text
    return r.json()["results"]


def op(pid, device="device-synth-A", base=1, op_id=None, **changes):
    return {"operation_id": op_id or "synth-" + secrets.token_hex(8), "patient_id": pid, "device_id": device, "base_version": base, "changes": changes}


def create(pid, device="device-synth-A", **extra):
    f = {"name": f"{SYNTH} API Patient {pid}", "phone": "9000000001", "village": "Synthetic Village", "condition": "Routine care", "identity_status": "UNVERIFIED", "__create__": True}
    f.update(extra)
    return op(pid, device, 0, **f)


# create (as the frontend sends it) -> stored
pid = f"SH-Z{run}"
o = create(pid)
res = sync(tok["worker1"], o)[0]
R.check("S1: create op with identity_status=UNVERIFIED -> applied (real Atlas)", res["status"] == "applied", str(res.get("reason", "")))
p = db.patients.find_one({"patient_id": pid})
R.check("Atlas: patient persisted, UNVERIFIED, version 1, created_by worker1", p and p["identity_status"] == "UNVERIFIED" and p["version"] == 1 and p["created_by"] == S["users"]["worker1"]["id"])
R.check("Atlas: operation receipt, change-feed row and audit row exist", db.operations.count_documents({"operation_id": o["operation_id"]}) == 1 and db.changes.count_documents({"operation_id": o["operation_id"]}) == 1 and db.audit.count_documents({"patient_id": pid, "event_type": "PATIENT_CREATED_SYNC"}) == 1)

# duplicate replay
dup = sync(tok["worker1"], o)[0]
R.check("duplicate operation_id -> status 'duplicate'", dup["status"] == "duplicate")
R.check("Atlas: still exactly 1 patient / 1 receipt / 1 audit after the replay", db.patients.count_documents({"patient_id": pid}) == 1 and db.operations.count_documents({"operation_id": o["operation_id"]}) == 1 and db.audit.count_documents({"patient_id": pid, "event_type": "PATIENT_CREATED_SYNC"}) == 1)
two_in_one = sync(tok["worker1"], o, o)
R.check("same operation twice in ONE batch -> already-applied op sent twice -> both duplicate", [r["status"] for r in two_in_one] == ["duplicate", "duplicate"])
fresh = op(pid, base=1, village="Fresh-double")
fd = sync(tok["worker1"], fresh, fresh)
R.check("FRESH operation sent twice in one batch -> applied once, then duplicate", [r["status"] for r in fd] == ["applied", "duplicate"], str([r["status"] for r in fd]))
R.check("Atlas: exactly one receipt and one SYNC_OPERATION audit row for it", db.operations.count_documents({"operation_id": fresh["operation_id"]}) == 1 and db.audit.count_documents({"description": {"$regex": "^" + fresh["operation_id"]}}) == 1)

# S1 safety: VERIFIED can never be set through sync
pv = f"SH-Z{run}V"
r1 = sync(tok["worker1"], create(pv, identity_status="VERIFIED"))[0]
R.check("create with identity_status=VERIFIED -> rejected", r1["status"] == "rejected" and r1["reason"] == "identity_status_not_allowed")
R.check("Atlas: that patient was NOT created", db.patients.count_documents({"patient_id": pv}) == 0)
r2 = sync(tok["worker1"], op(pid, base=1, identity_status="VERIFIED"))[0]
R.check("update that sets identity_status -> rejected (cannot self-verify)", r2["status"] == "rejected")
R.check("Atlas: identity_status still UNVERIFIED", db.patients.find_one({"patient_id": pid})["identity_status"] == "UNVERIFIED")

# validation & rejection paths
bad = sync(tok["worker1"], op(pid, phone={"$gt": ""}), op(pid, age="forty"), op(pid, name="x" * 500))
R.check("injection-shaped / wrongly typed / oversized values -> each rejected individually", all(b["status"] == "rejected" and b["reason"].startswith("invalid_value") for b in bad), str([b["status"] for b in bad]))
unk = sync(tok["worker1"], op(pid, favourite_colour="blue"))[0]
R.check("unknown field name -> rejected unsupported_fields", unk["status"] == "rejected" and unk["reason"] == "unsupported_fields")
nf_op = op("SH-ZNOPE" + run, village="x")
nf = sync(tok["worker1"], nf_op)[0]
R.check("update for a patient that does not exist -> rejected patient_not_found", nf["status"] == "rejected" and nf["reason"] == "patient_not_found")
R.check("Atlas: no permanent receipt stored for patient_not_found", db.operations.count_documents({"operation_id": nf_op["operation_id"]}) == 0)

# ordering inside one batch: create then dependent updates
po = f"SH-Z{run}O"
batch = sync(tok["worker1"], create(po), op(po, base=1, village="Khera"), op(po, base=1, phone="9111111111"))
R.check("create + 2 dependent updates in one batch -> all applied in order", [b["status"] for b in batch] == ["applied"] * 3, str([b["status"] for b in batch]))
q = db.patients.find_one({"patient_id": po})
R.check("Atlas: final state reflects all three, version 3, no conflicts", q["village"] == "Khera" and q["phone"] == "9111111111" and q["version"] == 3 and db.conflicts.count_documents({"patient_id": po}) == 0)

# S3 against the real database: same device sequential edits (stale shared base) do not conflict
sa = sync(tok["worker1"], op(po, "device-synth-A", 1, village="Edit-1"), op(po, "device-synth-A", 1, village="Edit-2"))
R.check("S3: two sequential edits from one device with the same base -> no false conflict", [b["status"] for b in sa] == ["applied", "applied"])
# another device editing a clinical field from a stale base -> conflict, never overwritten
c1 = sync(tok["worker1"], op(po, "device-synth-A", 1, condition="Hypertension"))[0]
c2 = sync(tok["worker2"], op(po, "device-synth-B", 1, condition="Diabetes"))[0]
R.check("clinical field edited by a 2nd device from a stale base -> conflict", c1["status"] == "applied" and c2["status"] == "conflict" and c2["conflicts"][0]["category"] == "CRITICAL_CLINICAL")
R.check("Atlas: patient keeps the first value; conflict stored with both values (no last-write-wins)", db.patients.find_one({"patient_id": po})["condition"] == "Hypertension" and (cf := db.conflicts.find_one({"patient_id": po}))["value_a"] == "Hypertension" and cf["value_b"] == "Diabetes" and cf["status"] == "pending")

# pull feed
since = db.changes.find_one(sort=[("seq", -1)])["seq"]
pull = h.get(f"/api/sync/pull?since_seq={since - 3}&limit=200", headers=auth(tok["worker2"])).json()
R.check("pull feed returns the latest changes in seq order with a cursor", len(pull["changes"]) >= 3 and [x["seq"] for x in pull["changes"]] == sorted(x["seq"] for x in pull["changes"]) and pull["next_seq"] == since)
R.check("pull with cursor at the end returns nothing", h.get(f"/api/sync/pull?since_seq={since}", headers=auth(tok["worker2"])).json()["changes"] == [])

# change-feed sequence integrity
seqs = [d["seq"] for d in db.changes.find({}, {"seq": 1}).sort("seq", 1)]
R.check("Atlas: change-feed sequence numbers are unique and gap-free", seqs == list(range(seqs[0], seqs[0] + len(seqs))), f"{len(seqs)} rows")
save_pid = {"api_patient": pid, "ordered_patient": po, "conflict_patient": po}
from lib import save_state; save_state(save_pid)
c.close(); R.finish()
