# CONFLICT_POLICY.md — who may review and resolve what (enforced by the backend)

Applies from Milestone 3. The frontend only *shows* what the server allows (`GET /api/conflicts/{id}` → `allowed`); every rule below is enforced in `backend/app/main.py` and returns a structured error `{"detail": {"code": "...", "message": "..."}}`.

## Principles
1. **Never last-write-wins for clinical data.** A competing edit becomes a *conflict*; the record keeps its current value until a human decision is recorded.
2. **A resolution value is never typed in by the resolver for evidence- or patient-backed categories.** It is copied from the clinician's *approved evidence value* or the *patient's confirmation*.
3. **A decision is valid only for the field version the reviewer looked at.** Every value-changing resolution carries `expected_field_version`; if the field changed in the meantime the server answers `409 stale_conflict` and changes nothing.
4. **One transaction, one outcome.** Patient update + change feed + conflict status/history + audit row commit together or not at all. Two simultaneous resolutions → exactly one succeeds.
5. **History is never deleted.** Conflicts end as `resolved` or `rejected`; evidence and confirmations are kept; each conflict has an append-only `history` list; refused attempts are audited.

## Who can do what

| Category (fields) | Submit evidence | Review evidence (records the exact approved value) | Patient confirms | Apply / resolve | Reject the incoming change |
|---|---|---|---|---|---|
| **DOCUMENT_VERIFIABLE** (`name`, `age`) | worker, supervisor, clinician | **clinician only**, and **not the person who submitted it** | — | **supervisor or clinician**, only `apply_evidence` with an *approved* evidence reference (value = approved value; any other value is refused) | clinician |
| **PATIENT_VERIFIABLE** (`phone`, `village`) | — | — | **the linked patient only**, for their own record, one of the two proposed values, once | **supervisor or clinician**, only `apply_confirmation` (value = the confirmed value; the confirming account must still be linked) | clinician |
| **CRITICAL_CLINICAL** (`condition`, `diagnosis`, `medications`, `allergies`, `treatment_plan`, `blood_pressure`, `blood_glucose`, `dosage`) | — | — | — | **clinician only**: `accept_current`, `accept_incoming` or `custom` (with a written reason) | clinician |
| **REQUIRES_REVIEW** (anything else, e.g. `last_visit`) — never auto-resolved | — | — | — | **clinician only**, same options | clinician |

Workers, patients (except confirming) and unauthenticated callers can never resolve or reject. Supervisors can never resolve clinical/unclassified conflicts, nor choose a value for document/patient conflicts.

## Resolution types (`POST /api/conflicts/{id}/resolve`)
| `resolution` | Value written | Needs |
|---|---|---|
| `apply_evidence` | clinician's `approved_value` | `evidence_reference` (approved), `expected_field_version` |
| `apply_confirmation` | patient's `confirmed_value` | `patient_confirmation_id`, `expected_field_version` |
| `accept_current` | none (keeps the current value, closes the conflict) | `expected_field_version` |
| `accept_incoming` | the proposed (incoming) value | `expected_field_version` |
| `custom` | `selected_value` (validated per field) | `note` ≥ 3 chars, `expected_field_version` |
| `reject_incoming` | none → status `rejected` | `note` ≥ 3 chars (clinician only) |

The old names `accept_a` / `accept_b` and free-text resolutions are refused with `400 unknown_resolution`.

## Structured error codes
`role_not_permitted` (403) · `not_your_record` (403) · `not_patient_verifiable` (403) · `separation_of_duties` (403) · `stale_conflict` (409, includes `current_value` and `current_field_version`) · `conflict_not_pending` (409) · `evidence_reference_already_used` (409) · `evidence_already_reviewed` (409) · `already_confirmed` (409) · `account_already_linked` / `patient_already_linked` (409) · `resolution_not_allowed` (400, lists the allowed ones) · `unknown_resolution` (400) · `approved_evidence_required` (400) · `patient_confirmation_required` (400) · `confirmation_no_longer_valid` (400) · `value_must_come_from_evidence` (400) · `synthetic_conflict` (400) · `expected_field_version_required` / `approved_value_required` / `note_required` / `invalid_value` / `value_not_proposed` / `identity_check_required` (422) · `conflict_not_found` / `evidence_not_found` / `patient_not_found` / `patient_account_not_found` (404) · `transaction_failed` (503, nothing changed, safe to retry).

Refusals with codes `role_not_permitted`, `stale_conflict`, `resolution_not_allowed`, `value_must_come_from_evidence`, `approved_evidence_required`, `patient_confirmation_required` and `confirmation_no_longer_valid` are written to the audit log as `CONFLICT_ACTION_DENIED` (who, which conflict, what was attempted, why refused).

## Other endpoints added or changed
`GET /api/conflicts?status=pending|resolved|rejected|all` (supervisor, clinician; includes `current_value`, `current_field_version`) · `GET /api/conflicts/{id}` (detail + evidence + confirmations + history + `allowed`) · `GET /api/me/conflicts` (the signed-in patient's own eligible conflicts) · `POST /api/patients/{id}/confirm-field` · `POST /api/conflicts/{id}/evidence` and `/evidence/{reference}/review` (JSON bodies; bounded and validated) · `POST /api/admin/patients/{id}/link-account` (JSON body; requires a recorded identity-check attestation; never moves an account to another patient).

## Caveats
* Evidence is a **reference** to a document held elsewhere; the system records a human decision and does not authenticate documents.
* The identity check behind account linking is the supervisor's attestation; the system cannot verify it.
* A patient can only choose between the two proposed values.

## Read-only endpoints added in Milestone 4 (policy unchanged)
`GET /api/me/record` (patient: own linked record only) · `GET /api/sync/conflicts` (worker/supervisor/clinician: conflicts raised by the caller's own synchronised changes, with evidence status and outcome) · `GET /api/evidence?status=` (clinician and supervisor; reviewing remains clinician-only) · `GET /api/admin/patient-accounts` (supervisor only). `GET /api/me/conflicts` accepts `?status=` and returns outcomes for the patient's own patient-verifiable conflicts.
