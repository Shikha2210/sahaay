> **SUPERSEDED in Milestone 3.** This describes the old workflow. See `CONFLICT_POLICY.md` for the current rules and API.

# SAHAAY conflict-resolution workflows — next implementation stage

This package extends the existing v3 project with category-specific workflow records.

## New workflows

### Document-verifiable
1. Staff submits an evidence reference and description using `POST /api/conflicts/{id}/evidence`.
2. A clinician reviews it using `POST /api/conflicts/{id}/evidence/{reference}/review?decision=approve&review_note=...`.
3. A reviewer resolves the conflict using `POST /api/conflicts/{id}/resolve` with `evidence_reference` and `resolution` (`accept_a`, `accept_b`, or `custom`).

This is a human evidence-review record, NOT document cryptographic authenticity verification. Do not represent it as OCR/signature validation.

### Patient-verifiable
1. A supervisor links a patient account after an out-of-band identity check: `POST /api/admin/patients/{patient_id}/link-account?email=...`.
2. The authenticated patient confirms one of the proposed values using `POST /api/patients/{patient_id}/confirm-field?conflict_id=...&confirmed_value=...`.
3. An authorized supervisor/clinician resolves the conflict using the returned `patient_confirmation_id`.

### Critical clinical
Only an account with role `clinician` may resolve a critical clinical conflict. Supervisors are explicitly denied.

## Important operational requirements
- MongoDB Atlas must support transactions for the atomic resolution route (Atlas replica-set deployment normally does).
- The supervisor must verify the patient's identity before linking an account. The API cannot perform that identity proof itself.
- A clinician's evidence approval is a recorded human review, not cryptographic document verification.
- Do not use real patient/Aadhaar information in the hackathon demo.

## Test manually
1. Create a synthetic conflict by syncing two competing updates to the same field.
2. Submit evidence / patient confirmation using the relevant workflow.
3. Try resolving a clinical conflict as supervisor (must return 403).
4. Resolve it as clinician (should succeed and create a change-feed event and audit record).
5. Pull changes on both devices and confirm they converge.

Automated integration tests against a disposable MongoDB replica set are still required before calling this production-safe.
