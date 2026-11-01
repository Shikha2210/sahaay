# MILESTONE_3_REPORT.md — Conflict engine safety and correctness (2026-11-02)

Branch `milestone-3-conflict-engine` (from `milestone-2c-e2e`). Policy table: `CONFLICT_POLICY.md`.
Scope kept: conflict engine + the supervisor/clinician Resolve screen only. No M4 work, no AI, no dashboard redesign.

## 1. Result at a glance
| Check | Result |
|---|---|
| Backend tests (default run) | **116 passed**, 0 failed, 19 skipped (the real-Atlas suite is opt-in), 0 expected-failures (was 2) |
| Real MongoDB Atlas integration tests (disposable `sahaay_test_*` database) | **19 passed**, 0 failed |
| Frontend unit tests | **40 passed** |
| Frontend production build | **passed** (1586 modules, PWA service worker generated) |
| Real browser run of the new Resolve workflow (Edge, real backend, real Atlas, **disposable database**) | **23 passed**, 0 failed; 0 browser dialogs; backend log: 0 tracebacks, 0 HTTP 500 |
| Real `sahaay` database | **untouched**: collection counts identical before/after; disposable databases removed afterwards |
| `npm audit` | unchanged: 9 (2 low, 7 moderate), build-time only |

## 2. Defect-by-defect status (C1–C11)
| ID | Status | Root cause | Fix | Evidence (tests) |
|---|---|---|---|---|
| **C5** stale resolution overwrote newer clinical data (**High**) | **FIXED** | The resolve code only guarded against changes *during* the transaction, never against changes *since the reviewer looked*. | Every value-changing resolution must send `expected_field_version`. Inside the transaction the server compares it with the field's current version and the patient update is a conditional write (`version` and `field_versions.<field>` must still match). Mismatch → `409 stale_conflict` with the current value; nothing changes; refusal audited; the dialog reloads the fresh state. | stand-in: 3 tests; **real Atlas**: stale refusal + fresh retry succeeds; **browser**: newer dosage "15 mg" arrives while a clinician has the dialog open → decision refused, 15 mg kept; message stays on screen |
| **C3** evidence did not record the approved value | **FIXED** | Review stored approve/reject only. | `approved_value` is required to approve, validated per field, stored with reviewer/note/time; the *claimed* value is stored separately. `apply_evidence` copies the **approved** value (never the claimed one, never the caller's). | claimed 41 vs approved 42 → patient gets 42 (stand-in, **real Atlas**, **browser**) |
| **C4** supervisors could resolve document/patient conflicts with any value | **FIXED** | One shared `custom`/`accept_*` path for all categories. | Per-category policy table: document → only `apply_evidence`, patient → only `apply_confirmation`, clinical/unclassified → clinician only. Supervisors can apply only an approved value; supplying a different one → `value_must_come_from_evidence`. Even a clinician must go through evidence for document conflicts. | stand-in + **real Atlas** + browser (supervisor sees only "Apply approved value") |
| **C6** resubmitting rejected evidence → HTTP 500 | **FIXED** | `DuplicateKeyError` from the unique index was uncaught. | Caught → `409 evidence_reference_already_used` (with the earlier status); a **new** reference is accepted; rejected records are preserved and can never be used to resolve; double review → `evidence_already_reviewed`. | stand-in, **real Atlas** |
| **C1** no patient-confirmation endpoint | **FIXED (API)** | Documented, never built. | `POST /api/patients/{id}/confirm-field` (patient role only; must be linked to that record; conflict must belong to that record, be patient-verifiable and pending; value must be one of the two proposed; once per patient; records a confirmation, **never edits the record**) + `GET /api/me/conflicts` (own eligible conflicts only). A supervisor/clinician then applies the confirmed value. | stand-in (5 tests incl. another patient, unlinked patient, wrong role, wrong category, double confirm, unlinking invalidates), **real Atlas** (two linked patients), browser (supervisor applies the patient's confirmation) |
| **C2** Resolve button always failed | **FIXED** | UI sent a free-text `resolution` and used `prompt()`. | New `ConflictReview.jsx` (existing styling, wired into `App.jsx`): detail dialog with current value/version, evidence, confirmations, history; shows only actions the server allows for the role; two-step in-page confirmation; sends the exact API fields. Clinicians now reach the same screen (they previously got an empty page). Contract tests fail if the UI ever sends a resolution the server does not know. | 4 contract tests, **browser 23/23** |
| **C7** no separation of duties; unbounded query-string evidence | **FIXED** | — | Reviewer ≠ submitter (`separation_of_duties`). Evidence is now a JSON body: reference pattern/length, description ≤1000, claimed value validated; old query-string style rejected (422). | stand-in, **real Atlas** |
| **C8** no reject outcome, no history | **FIXED** | — | `reject_incoming` (clinician, written reason) → status `rejected`, value unchanged. Each conflict has an append-only `history` (created, proposed_again, evidence_submitted/approved/rejected, patient_confirmed, resolved/rejected, with actor, role, old/new value). `?status=` filter validated. | stand-in, **real Atlas** (history order), browser (reject) |
| **C9** duplicate pending conflicts | **FIXED** | No uniqueness. | Same proposed value for the same field → the existing conflict is reused (`existing: true`, history `proposed_again`); partial unique index `uniq_pending_conflict` makes the database refuse a twin. | stand-in; **real Atlas** (index exists; direct duplicate insert rejected by MongoDB) |
| **C10** account linking: query-string email, silent overwrite, no check record | **FIXED** | — | JSON body; requires `identity_verified: true` + method + note (recorded on the account); an account already linked is **never** moved (`account_already_linked`); a record already linked → `patient_already_linked`; atomic conditional update; partial unique index `uniq_linked_patient`; re-linking the same pair is idempotent; audit row holds no email or free text. | stand-in, **real Atlas** (database itself refuses a second link) |
| **C11** seeded demo conflicts used display names | **FIXED** | Used `'Date of birth'` etc. | Demo conflicts (only when `SEED_DEMO_DATA=true`) use real fields and categories, are flagged `synthetic` and can never be resolved (`synthetic_conflict`). | stand-in |

## 3. Phase 2 checklist
1. **Categories inspected and documented** — `CONFLICT_POLICY.md` (who may review, resolve, reject per category).
2. **Unauthorized resolution blocked at the API layer** — verified for supervisor, worker, patient, other patient, unauthenticated, wrong category, wrong resolution; including on real Atlas.
3. **History and evidence preserved** — nothing is deleted; rejected evidence stays; conflicts end `resolved`/`rejected`; history list; refused attempts audited.
4. **Concurrent resolution cannot change a newer record** — stale-version guard + conditional update + `with_transaction`. Real Atlas: two simultaneous resolutions → exactly one `200`, the other `409 conflict_not_pending`/`stale_conflict`; patient version rose at most once; one audit row.
5. **No last-write-wins for clinical data** — two simultaneous clinical updates from different devices on real Atlas: one applied, one recorded as a conflict, **both values retained**.
6. **Structured errors** — all conflict endpoints (and now every role check) return `{code, message, …}`; a test asserts the shape for refusals of every kind.

## 4. Tests added (actual outcomes)
| Suite | Type | Count | Outcome |
|---|---|---|---|
| `tests/test_conflict_engine.py` | **in-memory stand-in** (logic only) | 32 | all passed |
| `tests/test_conflict_contract.py` | frontend↔backend contract (reads the real source) | 5 | all passed |
| `tests/test_atlas_integration.py` | **REAL Atlas**, disposable database | 19 (11 new/rewritten for M3) | all passed |
| `e2e/phase_m3_conflict_ui.mjs` (+ `m3_setup.py`) | **real browser + real backend + real Atlas**, disposable database | 23 checks | all passed |
Covered regression cases: stale clinical resolution; two simultaneous clinical updates; evidence approved vs submitted value; supervisor arbitrary document resolution; patient confirming another patient's information; rejected-evidence resubmission; unauthorized clinician/supervisor/worker/patient actions; duplicate resolution requests; transaction rollback (**real**: injected failure at the last write → MongoDB rolled back patient, change feed and conflict history; the same request then succeeds); audit-history correctness (actor id/role, event order, old/new/claimed/approved values, no secrets, denial rows).
**Mutation checks** (code deliberately broken, tests must fail, then restored): removing the stale check → 2 failures; letting a supervisor resolve clinical conflicts → 4; taking the value from the caller instead of the evidence → 1; letting another patient confirm → 1.
**Bugs found by the new tests themselves (never shipped):** a helper-argument collision that would have turned "already resolved" refusals into HTTP 500s; a dialog that erased the stale-refusal message right after showing it (found via a flaky browser check; fixed and now asserted).
**What the stand-in does NOT prove:** real transaction isolation, rollback and concurrency — those are covered only by the real-Atlas tests above.

## 5. Behaviour changes to be aware of
* `require_roles` 403s are now structured (`code: role_not_permitted`, `required_roles`) for **every** endpoint.
* Conflict API contract changed (evidence/link/resolve bodies; new resolution names; `expected_field_version` required). The M2C e2e scripts for the old API are in `e2e/superseded/`.
* **Two unique partial indexes will be created in the real `sahaay` database the next time the backend starts against it** (`uniq_pending_conflict`, `uniq_linked_patient`). I verified read-only that existing real data cannot violate them. Not applied yet by me (only created on the disposable databases and the test database).
* The 3 old pending conflicts already in `sahaay` predate this change (no `history`, no `dedupe_key`, no `field_version_at_conflict`); the detail endpoint handles that, but their history view is empty.

## 6. Remaining security / correctness risks
1. **Evidence is a reference, not a document.** No upload, no authenticity check; approval records a human decision only.
2. **Linking relies on the supervisor's attestation** of an out-of-band identity check; the system cannot verify it. There is no *unlink/correct a mistaken link* flow (needs manual database work).
3. **Patient confirmation is limited to the two proposed values**; a patient whose correct value is a third value has no route (a clinician cannot apply it either for patient-verifiable fields — `reject_incoming` + a fresh edit is the workaround).
4. **Over-sensitive staleness by design:** any change to the field's version forces re-review (safe, sometimes annoying).
5. **Policy choice to confirm:** supervisors *can* apply a clinician-approved value / patient confirmation. If you want clinicians only, change one set in `POLICY`.
6. **Audit log** is an ordinary collection (mutable by anyone with database access); refusal audit rows are written outside the rolled-back transaction by design. Logins are still not audited (X5).
7. All staff still read **all** patients and the full change feed (X7); the in-memory login rate limiter is per-process; tokens remain JS-readable.
8. Concurrency was verified with two simultaneous requests, not a load test.
9. Existing real-`sahaay` conflicts (3 pending, legacy shape) have not been exercised through the new engine.

## 7. Functionality still requiring UI work
* **Patient:** a screen to see own conflicts and confirm a value (API ready: `/api/me/conflicts`, `confirm-field`); "My health" is still static.
* **Healthcare worker:** submit evidence (API ready); conflict notifications (S11); failed-operation list (S5).
* **Supervisor:** link-account screen with the identity-check attestation (API ready); staff creation.
* **Clinician:** dedicated dashboard and resolution history (the conflict dialog works; other pages still show the supervisor views).
* No automated component tests exist for `ConflictReview.jsx`; it is verified by the browser run and by contract tests only.

## 8. How to re-run
* Backend: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` ; real Atlas: `$env:SAHAAY_ATLAS_TESTS='1'; ... -m pytest tests/test_atlas_integration.py -q`.
* Frontend: `cd frontend; npm test; npm run build`.
* Browser workflow (disposable database only): start the backend with `MONGODB_DATABASE=sahaay_test_ui_<hex>` and `npm run dev`, then in `e2e` with `E2E_DATABASE` and `E2E_STATE` set: `m3_setup.py` then `node phase_m3_conflict_ui.mjs`; drop the disposable collections afterwards.
