# MILESTONE_4_REPORT.md — Role workspaces completed (2026-11-02)

Branch `milestone-4-role-workspaces` (from `milestone-3-conflict-engine`). Read first: `MILESTONE_3_REPORT.md`, `CONFLICT_POLICY.md`.
This was an **integration** milestone: the existing M3 conflict dialog, authentication, permissions and visual design are reused; the M3 conflict policy is unchanged (no resolution rule was touched, and nothing writes a value without the M3 checks). No M5 work, no AI.

## 1. Result at a glance
| Check | Result |
|---|---|
| Backend tests (default run) | **143 passed**, 0 failed, 22 skipped (the opt-in real-Atlas suite) |
| Real MongoDB Atlas integration tests (disposable databases) | **22 passed**, 0 failed (3 new for M4) |
| Frontend unit tests | **40 passed** |
| Frontend production build | **passed** (1591 modules, PWA service worker generated) |
| **Real browser (Edge), all four roles, real backend + Atlas, disposable database** | **65 of 65 checks passed** |
| **Backend authorization checks over real HTTP (UI bypassed)** | **39 of 39 passed** |
| Backend logs during all browser/API runs | 0 tracebacks, 0 HTTP 500, no tokens or connection strings |
| Real `sahaay` database | **untouched** (counts identical before/after every run); both disposable databases removed |
| `npm audit` | unchanged (9: 2 low, 7 moderate, build-time only) |

## 2. What was completed, per role
### Patient — own record and confirmations (real data only)
* **New backend:** `GET /api/me/record` (the patient's own linked record, no internal fields), `GET /api/me/conflicts?status=` now also shows outcomes. Both are patient-role only; M3's `confirm-field` is used for confirming.
* **Screens:** Overview (real record id, "needs your confirmation" count, last updated, identity status, real recent activity), **My health** (real name/phone/etc., real activity), new **Confirm information** page (choose between the two recorded values, in-page confirmation step, shows "You confirmed … your record does not change until your care team applies it", and a "Recently closed" list). An **unlinked** account sees an honest explanation instead of demo content.
* **Removed:** the hard-coded "Asha Devi", "On track", "04 Nov", fake timeline and "Verified*" demo text.
* **Browser-verified:** unlinked state; own data only; patient two never sees patient one's name, phone or conditions (and vice-versa); clinical conflict details are never shown to the patient; confirming writes exactly one confirmation and **does not change the record**; after a supervisor applies it the patient sees "Applied" and the new phone number.

### Clinician — dedicated workspace
* **New backend:** `GET /api/evidence?status=` (clinician and supervisor; supervisors read-only).
* **Screens:** own navigation (Overview, Conflict review, **Evidence review**, **Resolution history**); dashboard with real counts (clinical conflicts, evidence to review, other pending, total open) and lists with Review buttons; the conflict queue gained category filters; evidence queue; resolution history table (closed conflicts, outcome, value, role). All decisions use the existing M3 dialog (approve/reject evidence with the exact value, resolve, reject incoming).
* **Browser-verified:** counts match the database; evidence approved with value **42** while the claim was **41** (both stored); clinical conflict resolved from the dashboard; the case then appears in the history; your own evidence cannot be reviewed (button disabled; the server also refuses — M3 tests).

### Healthcare worker — notifications and evidence submission
* **New backend:** `GET /api/sync/conflicts` — conflicts that came from **this worker's own** synchronised changes (or evidence they submitted), with evidence status and outcome.
* **Screens:** new **Sync conflicts** page (Open/Closed, outcomes, evidence status) with an evidence form (reference, description, claimed value; only for document conflicts); dashboard "Open conflicts" and "Synced changes" now real (replacing the fake "2 pending" / "08 visits"); the **notification bell** shows a real, role-specific count and opens the right page.
* **Browser-verified:** worker one sees their three conflicts and none of worker two's; the evidence form creates a `pending_review` record claiming 41; re-using the reference shows a clear message and creates no duplicate; after clinician approval and supervisor application the worker's *Closed* tab shows the outcome ("record now shows 42") with the approved evidence.

### Supervisor — patient-account linking
* **New backend:** `GET /api/admin/patient-accounts` (supervisor only, no password data).
* **Screens:** **Patient accounts** page listing every patient login and its link; link dialog with record search, identity-check method, note, and a required attestation ("I have verified this person's identity myself"). The dashboard's fake "Identity reviews 02" and fixed "Needs attention" items are now real (unlinked accounts, pending conflicts).
* **Browser-verified:** the button stays disabled until record + note + attestation; linking to a record that is already linked is **refused with a clear message** and changes nothing; the correct link succeeds; the database holds the method, note and the supervisor who checked; audit row `PATIENT_ACCOUNT_LINKED`; the supervisor then applies the approved value and the patient's confirmation through the M3 dialog.

## 3. Authorization is in the backend (39 real-HTTP checks)
Without a token every new endpoint returns 401. With a token: patients cannot read other records, staff lists or other patients' conflicts, and cannot confirm another patient's information (403 `not_your_record`; naming another patient's conflict on their own record → 404); workers cannot list accounts/evidence/conflicts, review evidence, resolve or link; clinicians cannot link accounts or confirm; supervisors cannot review evidence and cannot re-link an account or record; unlinked attestation is refused. Worker notifications are filtered **by the query**, not the screen (verified on real MongoDB, where `$or`/`$elemMatch` behave differently from the in-memory stand-in).

## 4. Tests added (actual outcomes)
| Suite | Type | Count | Outcome |
|---|---|---|---|
| `tests/test_workspaces_api.py` | in-memory stand-in (logic only) | 12 | passed |
| `tests/test_workspace_contract.py` | frontend↔backend contract (reads the real source: routes exist, no demo content, no browser dialogs, role navigation) | 15 | passed |
| `tests/test_atlas_integration.py` | **real Atlas** | 22 (3 new) | passed (twice in a row) |
| `e2e/phase_m4_workspaces.mjs` + `m4_setup.py` | **real browser + backend + Atlas**, disposable DB | 65 | passed |
| `e2e/m4_api_checks.py` | **real HTTP**, UI bypassed | 39 | passed |
Mutation checks (code deliberately broken, tests must fail, then restored): leaking internal fields from `/api/me/record` → failed; worker sees all conflicts → failed; evidence queue open to workers → failed; account list open to clinicians → failed.

## 5. Failures and problems met on the way (and what they were)
| Finding | What it was | Resolution |
|---|---|---|
| Browser check "the confirmation is recorded and shown" failed | **Real UI defect:** the app's CSS makes every `<p>` inside a conflict card a flex row, so sentences containing bold text were split into separately spaced pieces ("You confirmed   9222222222   ."). The data was correct. | Sentences wrapped in a single span (3 places); re-run 65/65. |
| Atlas test failed (`200` instead of `403`) | Test mistake: identity overrides are global to the app, so a reused client was acting as the supervisor. | Test now switches identity explicitly. |
| One run of the Atlas suite reported 22 setup errors | Transient connectivity (the known alternating-public-IP issue); the ping passed straight after and two consecutive full runs passed. | None needed; unchanged risk (see M2C). |
| API check expected `patient_already_linked` | The server correctly answered 409 `account_already_linked` (that account was already linked). | Check accepts either code; both are correct refusals. |
No application defect remained open at the end of the milestone's test runs.

## 6. Remaining gaps (honest list)
**In the four workspaces**
* New screens need a connection (they read the server). They show a clear offline message; **offline behaviour of these screens was not browser-tested**.
* Notifications are polled (staff ~every 15 s with sync, patients every 30 s); there is no push.
* Evidence is still only a *reference* (no file upload, no authentication of documents). A patient can only choose between the two recorded values (M3 policy).
* Patient history shows patient-verifiable items only; the patient *Identity & consent* and *AI assistant* pages are unchanged (static text / existing chat).
* Clinician navigation no longer includes the old supervisor-style Identity/Sync/Audit pages; **no clinician audit-history view exists** (the supervisor Audit page is still a placeholder).
* No staff-creation screen (staff are still created through the API); no way to correct a mistaken account link (needs manual database work).
* Lists are capped (conflicts 500, evidence/notifications 200) with category filters only; no search/pagination.

**Elsewhere in the app (not touched, still visible)**: the Sync centre / Sync monitoring pages still contain prototype text ("Backend pending", "simulates acknowledgements locally"), rejected operations still display as green with no failed-operations list (S5), Settings/Support/"Forgot password" are stubs, identity-verification screens unchanged.

**Quality gaps**: browser coverage is Microsoft Edge only; responsive/mobile layout and accessibility (screen reader, contrast) were **not tested**; the new components have no component-level unit tests (they are covered by contract tests and the browser runs); concurrency was exercised with two simultaneous requests, not load.

## 7. Security notes
* No backend permission or M3 policy changed; every new endpoint is role-gated and tested for 401/403.
* New read endpoints expose staff-visible data to staff only (X7 unchanged: all staff can still read all patients); patients see only their own record and patient-verifiable conflicts; patient responses contain no staff identities.
* The patient account list is supervisor-only and carries emails (personal data) but no secrets.
* Token storage, expiry handling and the offline ownership protections from M2 are unchanged and were exercised again by the logins in these runs.

## 8. How to re-run
* Backend: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` ; real Atlas: `$env:SAHAAY_ATLAS_TESTS='1'; ... -m pytest tests/test_atlas_integration.py -q`
* Frontend: `cd frontend; npm test; npm run build`
* Browser + HTTP checks (disposable database only): start the backend with `MONGODB_DATABASE=sahaay_test_ui_<hex>` and `npm run dev`; in `e2e` set `E2E_DATABASE` and `E2E_STATE`, then `m4_setup.py`, `node phase_m4_workspaces.mjs`, `m4_api_checks.py`; drop the disposable collections afterwards.
