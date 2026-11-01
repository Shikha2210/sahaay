"""Read-mostly database/API probe used by the browser scripts. Prints ONE JSON line. Never prints secrets.
Commands:
  patients_named <exact name>        -> list of {patient_id,name,version,identity_status,created_by,village}
  audit <patient_id>                 -> list of audit event types for the patient
  receipts <patient_id>              -> number of operation receipts stored for the patient
  remote_edit <patient_id> <field> <value>   -> worker2 (a different device) edits via the real API
  conflicts_for <patient_id>         -> pending/resolved conflicts
  counts                             -> document counts of the main collections"""
import json
import secrets
import sys

from lib import auth, http, load_state, login, mongo

cmd, args = sys.argv[1], sys.argv[2:]
S = load_state()
c, db = mongo()


def clean(d):
    d = dict(d); d.pop("_id", None)
    return {k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)) for k, v in d.items()}


if cmd == "patients_named":
    out = [clean(p) for p in db.patients.find({"name": args[0]})]
    print(json.dumps([{k: p.get(k) for k in ("patient_id", "name", "version", "identity_status", "created_by", "village", "phone")} for p in out]))
elif cmd == "audit":
    print(json.dumps([a["event_type"] for a in db.audit.find({"patient_id": args[0]}).sort("created_at", 1)]))
elif cmd == "receipts":
    print(json.dumps(db.operations.count_documents({"patient_id": args[0]})))
elif cmd == "conflicts_for":
    print(json.dumps([{k: x.get(k) for k in ("conflict_id", "field", "category", "status", "value_a", "value_b", "selected_value", "resolved_by")} for x in db.conflicts.find({"patient_id": args[0]})]))
elif cmd == "remote_edit":
    h = http()
    tok = login(h, S["users"]["worker2"]["email"], S["users"]["worker2"]["password"]).json()["access_token"]
    pid, field, value = args
    ver = db.patients.find_one({"patient_id": pid})["version"]
    r = h.post("/api/sync/batch", headers=auth(tok), json={"operations": [{"operation_id": "synth-" + secrets.token_hex(8), "patient_id": pid, "device_id": "device-synth-API2", "base_version": ver, "changes": {field: value}}]})
    print(json.dumps(r.json()["results"][0]["status"]))
elif cmd == "patient_fields":
    p = db.patients.find_one({"patient_id": args[0]}) or {}
    print(json.dumps({k: p.get(k) for k in ("age", "name", "phone", "condition", "dosage", "last_visit", "version")}))
elif cmd == "evidence_rows":
    print(json.dumps([{k: e.get(k) for k in ("evidence_reference", "status", "claimed_value", "approved_value", "submitted_role", "reviewed_by")} | {"submitted_by_worker1": e.get("submitted_by") == S["users"]["worker1"]["id"]} for e in db.evidence.find({"patient_id": args[0]})]))
elif cmd == "confirmations":
    print(json.dumps([{k: x.get(k) for k in ("field", "confirmed_value", "status")} | {"by_patient1": x.get("user_id") == S["users"]["patient1"]["id"]} for x in db.patient_confirmations.find({"patient_id": args[0]})]))
elif cmd == "account":
    u = db.users.find_one({"email": args[0]}) or {}
    ic = u.get("identity_check") or {}
    print(json.dumps({"linked_patient_id": u.get("linked_patient_id"), "method": ic.get("method"), "checked_by_supervisor": bool(ic.get("checked_by")), "has_note": bool(ic.get("note")), "linked_by": bool(u.get("linked_by"))}))
elif cmd == "counts":
    print(json.dumps({n: db[n].count_documents({}) for n in ("users", "patients", "operations", "changes", "conflicts", "audit", "evidence", "patient_confirmations")}))
c.close()
