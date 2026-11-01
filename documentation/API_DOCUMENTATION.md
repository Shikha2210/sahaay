# API_DOCUMENTATION.md — SAHAAY backend as currently implemented

Base URL (dev): `http://127.0.0.1:8000` · Interactive docs: `/docs` · Auth: `Authorization: Bearer <JWT>` (HS256, 720 min default).
Status column: ✔ works as coded (unverified by execution) · ⚠ has a defect (see BUGS_AND_GAPS ID) · ✖ missing.
Roles: P=patient, W=worker, S=supervisor, C=clinician.

| Method & path | Roles | Input | Output | Status / notes |
|---|---|---|---|---|
| `GET /health` | none | – | `{status, mongodb, database}` or 503 | ✔ Only a liveness+Mongo ping; no `/ready`. |
| `POST /api/auth/register` | public | JSON `{name, email, password(≥10), role:'patient'}` | `{access_token, token_type, user}` | ✔ Forces patient. 409 duplicate email. No rate limit (X5). |
| `POST /api/auth/login` | public | `{email, password}` | token + user | ✔ Generic 401 message. No lockout/audit (X5). |
| `GET /api/auth/me` | any | – | `{name,email,role}` | ✔ Does not return `linked_patient_id`. |
| `POST /api/admin/users` | S | `{name,email,password,role∈worker/clinician/supervisor}` | `{created,email,role}` 201 | ✔ Audited. |
| `GET /api/patients?q=` | W,S,C (P → 403) | query | list ≤500 | ✔ Unpaginated; all patients visible to all staff (X7); full phone returned. |
| `POST /api/patients` | W,S | `PatientIn` | patient 201 | ✔ Not used by the frontend (frontend creates via sync). |
| `GET /api/patients/{id}` | W,S,C | – | patient | ✔ Patient role excluded (X8). |
| `POST /api/sync/batch` | W,S,C | `{operations:[{operation_id, patient_id, device_id, base_version, changes{}, created_at?, schema_version}]}` ≤100 | `{results:[{operation_id,status,server_version,applied_fields,conflicts[]}], server_time}` | ⚠ S1,S2,S3,S4,S15. Statuses: `applied`, `conflict`, `duplicate`, `rejected` (`unsupported_fields`,`empty_changes`,`patient_not_found`), `retryable_error`. Idempotent on `operation_id` (unique index). `changes.__create__:true` creates. Allowed fields: name, age, phone, village, condition, diagnosis, medications, allergies, treatment_plan, blood_pressure, blood_glucose, dosage, last_visit. |
| `GET /api/sync/pull?since_seq&limit≤500` | W,S,C | – | `{changes[{seq,patient_id,version,changes,operation_id,created_at}], next_seq, has_more}` | ✔ Cursor feed; not scoped (S12). |
| `GET /api/conflicts?status=pending` | S,C | – | list ≤500 | ✔ Workers/patients cannot read (S11). `status` unvalidated. |
| `POST /api/conflicts/{id}/resolve` | S,C | `{resolution∈accept_a/accept_b/custom, selected_value?, evidence_reference?, patient_confirmation_id?}` | `{resolved,conflict_id,patient_version,selected_value}` | ⚠ C3,C4,C5. In a transaction: conflict→resolved, patient field+version, change feed, audit. CRITICAL_CLINICAL & REQUIRES_REVIEW → clinician only (✔ enforced server-side). 409 on concurrent resolution. |
| `POST /api/conflicts/{id}/evidence?reference&description` | W,S,C | **query params** | `{evidence_reference,status:'pending_review',warning}` | ⚠ C6,C7. Records a reference only; no attachment; no authenticity check (stated). |
| `POST /api/conflicts/{id}/evidence/{ref}/review?decision&review_note` | C | **query params** | `{status, note}` | ⚠ C3. No approved value captured. |
| `POST /api/admin/patients/{pid}/link-account?email` | S | query | `{linked,patient_id,email}` | ⚠ C10. Audited. |
| `POST /api/patients/{pid}/confirm-field` | P | – | – | ✖ **Documented in CONFLICT_WORKFLOWS.md but not implemented (C1).** |
| `POST /api/identity/{pid}/attempt` | W,S | `{consent:true, method}` (raw dict) | `{status:'PENDING_AUTHENTIC_VERIFICATION', note}` | ⚠ X6,I1,I2. Never claims verification (✔). |
| `GET /api/audit?limit≤500` | S,C | – | list | ✔ Only listing; no filters/pagination/export. |
| `GET /api/dashboard/summary` | W,S,C | – | counts | ⚠ S14. |
| `POST /api/chatbot/message` | any authed | `{message ≤2000}` | `{answer}` | ⚠ AI1,AI2. 503 if no key, 502 on upstream failure, 30 s timeout. |

## Error format
FastAPI default `{"detail": "..."}`; validation errors return 422 with a list. The frontend `api()` reads `detail` only when it is a string (a 422 list would show "[object Object]").

## Frontend ↔ backend contract check
| Frontend call | Backend | Match? |
|---|---|---|
| `POST /api/auth/login`, `/register`, `GET /me` | ✔ | ✔ |
| `POST /api/sync/batch` payload | ✔ shape matches | ⚠ create op contains `identity_status` → rejected (S1) |
| `GET /api/sync/pull` | ✔ | ✔ |
| `GET /api/patients`, `GET /api/conflicts?status=pending` | ✔ | ✔ (conflicts call only for S/C) |
| `POST /api/conflicts/{id}/resolve` | ✔ | ✖ wrong `resolution` value (C2) |
| `POST /api/identity/{pid}/attempt` | ✔ | ✔ |
| `POST /api/chatbot/message` | ✔ | ✔ |
| Evidence / link-account / confirm-field / audit list / dashboard summary | – | Frontend never calls them |

## Data model (Mongo)
`users{email*, name, password_hash, role, active, linked_patient_id?, linked_by?, linked_at?}` ·
`patients{patient_id*, name, age, phone, village, condition, …, identity_status, version, field_versions{}, updated_at}` ·
`operations{operation_id*, patient_id, device_id, actor_id, base_version, changes, status, response}` ·
`changes{seq*, patient_id, version, changes, operation_id}` · `counters{_id:'global_change_seq'}` ·
`conflicts{conflict_id*, patient_id, field, category, value_a, value_b, status, resolution…, synthetic?}` ·
`evidence{(evidence_reference,conflict_id)*, status, reviewed_by…}` · `patient_confirmations{confirmation_id*, (conflict_id,user_id)*}` (never written) ·
`audit{patient_id, actor_id, actor_role, event_type, description, metadata, created_at}` · `identity_attempts` · `chat_messages`. (`*` unique index.)

---
# Conflict API — replaced in Milestone 3
The conflict, evidence, confirmation and account-linking rows in the table above describe the OLD contract. The current contract (JSON bodies, structured errors, `expected_field_version`, new resolution names, `GET /api/conflicts/{id}`, `GET /api/me/conflicts`, `POST /api/patients/{id}/confirm-field`, `?status=` filter) is documented in `CONFLICT_POLICY.md`.
