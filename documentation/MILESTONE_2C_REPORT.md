# MILESTONE_2C_REPORT.md — Real end-to-end verification (2026-11-01)

**What was run:** the real FastAPI backend (uvicorn, port 8000) against the real MongoDB Atlas `sahaay` database, the real React/Vite frontend (port 5173) driven in a real browser (Microsoft Edge via Playwright), plus Python scripts that call the live HTTP API and inspect the database. Nothing in Phases 1–4 uses mocks or in-memory stand-ins. **Synthetic data only** (every record is labelled `ZZ-SYNTHETIC` or is the bootstrap supervisor). No application code was changed in this milestone; only test scripts (`e2e/`) and documentation were added.

Legend: **PASS** executed and succeeded · **FAIL** executed and failed (every FAIL below is a genuine product bug or missing feature, not a test error — test-only races were fixed and are described) · **BLOCKED** cannot be done because a feature does not exist · **NOT TESTED** not attempted.

## 1. Summary

| Phase | Script | PASS | FAIL | Other |
|---|---|---|---|---|
| 1 Backend, bootstrap, HTTP auth | `phase1_backend.py` | 33 | 0 | |
| 2 Frontend authentication (browser) | `phase2_ui_auth.mjs` | 31 | 2 | |
| 3A Provisioning + role matrix (API) | `phase3a_provision_and_roles.py` | 26 | 0 | 1 GAP |
| 3B Sync semantics on real Atlas (API) | `phase3b_api_sync.py` | 24 | 0 | |
| 3 Workflows in the browser | `phase3_ui_workflows.mjs` | 46 | 1 | 1 NOTE |
| 4A Create one conflict per category | `phase4a_conflicts_create.py` | 18 | 0 | |
| 4B What each role sees/does (browser) | `phase4b_ui_conflicts.mjs` | 5 | 5 | |
| 4C Resolution rules per category (API) | `phase4c_conflicts_resolve.py` | 39 | 7 | |
| **Total** | | **222** | **15** (≈11 distinct defects) | 2 |

Regular suites at the end: backend **79 passed**, 10 skipped (Atlas opt-in), 2 expected failures (C1, C2); real-Atlas integration suite **10 passed** (rerun in this milestone); frontend **40 passed**; production build **passed** (1585 modules, PWA service worker generated); `npm audit`: 9 remaining (2 low, 7 moderate, build-time only).

## 2. Phase 1 — backend and bootstrap (all PASS)
Started the backend: startup completed, `/health` reports `mongodb: connected`, database `sahaay`.
* **Index creation:** unique indexes on `users.email`, `patients.patient_id`, `operations.operation_id`, `conflicts.conflict_id`, `changes.seq` exist; collections created by `init_db`; sequence counter initialised.
* **First-supervisor bootstrap in the real database:** exactly one supervisor, email as configured, active, password stored only as a bcrypt hash; no demo patients or conflicts were seeded.
* **HTTP login:** supervisor login → 200; token lifetime exactly 60 min with minimal claims (`sub, role, iat, exp`); `/api/auth/me` works; no password hash in any response.
* **Invalid credentials:** wrong password and unknown email give the *identical* 401 (no account enumeration); rate limit 5×401 then 429, scoped per (IP, email) so the real supervisor stayed usable.
* **Unauthorized access:** six protected endpoints return 401 without a token; garbage token → 401 `token_invalid`; public registration of a supervisor → 403 and no account created.
* **Headers/CORS:** security headers present; preflight allowed for the Vite origin only.

## 3. Phase 2 — frontend authentication (browser)
Verified in Edge: supervisor login through the UI; token only in `sessionStorage` (nothing in `localStorage`); session persists across reload; a *new tab has no session*; logout (nothing pending) returns to login, clears token/profile and wipes local patients/operations; wrong password shows an in-page message (no `alert()`); correct credentials with the wrong role selected are refused and store no token; **expired-session handling**: a really-signed expired token → "Session expired" banner, *nothing is sent to the server*, local data and queued operation survive, "Sign in again" explains changes are kept, re-login as the same user syncs the queue; a tampered token → server 401 → banner. No uncaught JavaScript errors.
**Role navigation:** supervisor, worker and patient show the expected menus. **FAIL (known gaps):** (a) patient "My health" shows hard-coded content ("Asha Devi"), not the patient's own record; (b) clinician signs in but the page body is empty — there are no clinician screens (screenshot reviewed).

## 4. Phase 3 — application workflows
| Step requested | Result |
|---|---|
| Supervisor creates a healthcare worker | **PASS via API only.** There is **no screen** for it (gap F2). 3 staff accounts + 1 patient created in Atlas; each stored with role + bcrypt hash; audit `USER_PROVISIONED` written; duplicate email → 409; role matrix verified over HTTP (worker/patient/clinician denied where expected). |
| Worker logs in | PASS (browser). |
| Create a synthetic patient while online | PASS: registered in the UI, synced, in Atlas once (UNVERIFIED, version 1, created_by worker, 1 receipt, audit row); local op marked acknowledged only after the server answered. |
| Create a patient while offline | PASS: queued locally as `pending` (owner-tagged, base_version 0), **not** in Atlas while offline, listed in Sync centre. |
| Reconnect → sync to MongoDB | PASS: queue synced automatically after reconnect; exactly one record. |
| Pull feed updates the frontend | PASS: a second device edited via the API; with `GET /api/patients` blocked, the browser still received the change **only through `/api/sync/pull`**; cursor advanced; directory screen shows it. |
| Duplicate operation handling | PASS: replay → `duplicate`, one receipt/audit row; the same op twice in one batch → `applied` then `duplicate`; **lost acknowledgement** (server applied, response dropped): browser kept it pending, retried, got `duplicate`, Atlas has one patient and one receipt. |
| Failed operations and retry | PASS for network failure (attempt counted, error recorded, back-off scheduled, retry succeeds, one record). Server-rejected op (130-char name) recorded as `rejected`, nothing stored. **FAIL:** the Sync centre shows a rejected operation with a **green** badge like a success, and there is no failed-operations list/retry/discard (S5). |
| User switching & offline ownership | PASS: logout with an unsynced change shows the count and warning, Discard stays disabled until the checkbox is ticked, Cancel keeps everything; another user signing in is **blocked** with a dialog and gets no session; the first user's change stays on the device tagged to them, is **never uploaded under the second user**, and syncs when they sign back in (Atlas `created_by` = first user); a clean logout lets the next user in with an empty queue; explicit discard works and the discarded change never reaches Atlas. |

API-level checks on real Atlas (Phase 3B, all PASS): offline-create payload accepted (S1); `VERIFIED` can never be set through create or update; invalid/injection-shaped/oversized values rejected individually; unknown fields rejected; `patient_not_found` stores no poisoned receipt; create + dependent updates in one batch applied in order; two edits from one device with the same base do **not** conflict (S3); a second device editing a clinical field from a stale base **does** conflict and the original value is kept; pull cursor correct; change-feed sequence unique and gap-free.
**NOTE:** a manual "Sync now" and the auto-sync timer can upload the same operation at the same time (no in-tab sync mutex, gap S9). Harmless in every run because the server is idempotent (one record), but wasteful.

## 5. Phase 4 — conflict workflows
Six real conflicts (two competing devices, same stale base) were created in Atlas, one per field class. All were classified correctly, stored pending with both values, audited, **and the patient kept the first device's value for every contested field (no last-write-wins)**.

| Category (field) | UI | API | Database |
|---|---|---|---|
| **DOCUMENT_VERIFIABLE** (age, name) | Supervisor queue shows it with both values; resolve button uses `prompt()` and always fails (C2) | Resolve without approved evidence refused; worker submits evidence *reference*; supervisor/worker cannot review (403); clinician approves; clinician resolves → value applied; second resolve refused; **PASS** | patient updated, version +1; conflict kept as `resolved` with actor/role/time/evidence ref; audit + change-feed rows. **FAIL:** evidence stores no approved value (C3); resubmitting a rejected reference → **HTTP 500** (C6); a *supervisor* resolved a document conflict with an arbitrary unrelated value (C3/C4) |
| **PATIENT_VERIFIABLE** (phone) | no patient screen | Unconfirmed/fabricated-confirmation resolution refused (PASS); supervisor account-linking works with correct 403/404/409s and audit (PASS). **BLOCKED:** `confirm-field` returns 404 — endpoint not implemented (C1), so this category **cannot be completed**; patient cannot read own record (403, X8) | linking stored with `linked_by/linked_at`; no confirmations exist; conflict still pending |
| **CRITICAL_CLINICAL** (condition, dosage) | no clinician screen (F1) | supervisor/worker/patient → 403; clinician resolves; **PASS**. **FAIL (clinically significant, C5):** after a conflict was raised, a newer legitimate dosage "15 mg" was recorded; resolving the old conflict then **silently overwrote it with "10 mg"** (HTTP 200, no warning) | unchanged after denied attempts; resolved by clinician with audit |
| **REQUIRES_REVIEW** (last_visit) | — | supervisor → 403, stays pending (never auto-resolved); clinician resolves; **PASS** | applied once |

Other UI findings: supervisors see all conflicts with category and values but no evidence/confirmation/history and no reject option; **workers get no conflict notice** (only a count in the sync toast, S11); clinicians and patients have no conflict screens. Audit API (clinician) exposes the workflow events and contains no passwords/tokens; resolved conflicts are retained (history preserved); **logins are not audited** (X5).
**Not tested here:** concurrent resolution through the HTTP layer was verified in the earlier real-Atlas integration suite (exactly one winner), not re-run through the browser.

## 6. Bugs discovered / confirmed on the live system
| ID | Severity | Finding | Evidence |
|---|---|---|---|
| **C5** | **High (clinical)** | Resolving an old conflict overwrites a newer value (dosage) with no warning | Phase 4C, HTTP 200, dosage 15 mg → 10 mg |
| C1 | High | Patient-confirmation endpoint missing → PATIENT_VERIFIABLE conflicts can never be resolved | 404 |
| C2 | High | UI "Review case" always fails (invalid `resolution` value) and uses `prompt()` | UI toast: "resolution must be accept_a…" |
| F1 | High | Clinician has no UI at all | screenshot: empty page |
| C3/C4 | High | Evidence review records no approved value; a supervisor can resolve a document conflict with any value | Phase 4C |
| C6 | Medium | Re-submitting evidence after rejection → HTTP 500 (`DuplicateKeyError`) | the only 500 in the whole run (backend log) |
| X8 / F3 | Medium | Patients cannot read their own record; patient screens and several dashboard numbers ("2 pending", "08 visits") are hard-coded | screenshots, 403 |
| S5 | Medium | Rejected operations look successful (green) and are un-actionable; they block logout only through the unsynced-count dialog | Phase 3 F |
| S11 | Medium | Workers receive no conflict notification | Phase 4B |
| F2 | Medium | No UI to create staff, link accounts, submit/review evidence | API used |
| S9 | Low | No in-tab sync mutex (manual + auto overlap) | Phase 3 NOTE |
| X5 | Medium | Logins/failed logins are not audited | 0 audit rows |

**Test-harness issues found and fixed (not product bugs):** an expiry test raced with the old page legitimately syncing on reconnect; the lost-acknowledgement test raced with the auto-sync timer. Both were verified by request-level diagnostics before being changed.

## 7. Remaining implementation gaps (unchanged by this milestone)
Clinician workspace; patient workspace (own record, conflict confirmation); evidence/approval/resolution screens replacing `prompt()`; failed-operations screen; staff-management and account-linking screens; worker conflict notifications; bind resolved value to evidence/patient confirmation; stale-conflict guard; patient-level access scoping; AI assistant (needs Groq key — **not tested**); identity-verification UI flow (**not tested** end to end); search/filter/pagination; responsive/mobile layout and accessibility (**not tested**); PWA install and service-worker offline start (**not tested** — dev server only); two physical devices (simulated with separate browser contexts and API devices); Dexie upgrade from v1/v2 data (**not tested**); cross-tab behaviour (**not tested**).

## 8. Security concerns
1. **C5/C3/C4 integrity issues** (clinical data can be overwritten with stale or unsupported values) — fix before any real data.
2. Every staff user can read **all** patients and the full change feed (X7); full phone numbers returned.
3. Token in `sessionStorage` (readable by any script on the page); no refresh/revocation; no CSP header yet; IndexedDB unencrypted (documented).
4. Login failures/successes are not audited; rate limiter is in-memory per process.
5. **Atlas Network Access** must include every public IP in use (the machine alternated between two); never open `0.0.0.0/0` with real data.
6. **Synthetic accounts remain in the real `sahaay` database** (3 staff + 2 patient accounts, labelled `ZZ-SYNTHETIC`, passwords random and not stored anywhere readable except git-ignored `e2e/.e2e_state.json`). There is no deactivate/delete endpoint; they were not deleted. Remove them (or the whole `sahaay` database) before any real use — with your approval.
7. Positive: no credentials, tokens or connection strings appeared in the backend log; passwords hashed; role checks enforced server-side on every call tested; unauthorized/forged/expired tokens rejected; the server never trusts the client role.

## 9. Database state and safety
Final read-only inspection of `sahaay`: users 6 (bootstrap supervisor + 5 synthetic), patients 28, operations 59, changes 56 (gap-free sequence), conflicts 8 (5 resolved, 3 pending), audit 74, evidence 3, confirmations 0. **No record was deleted, no collection or database dropped.** No patient is unlabelled. Databases on the cluster: `sahaay`, Atlas's `sample_mflix` (no leftover `sahaay_test_*`). Credentials were read from `backend/.env` at run time and never printed.

## 10. Is SAHAAY ready for the hackathon demonstration?
**Not for the full 12-step demo; yes for a focused, honest sync demo.**
* **Can be demonstrated live today (verified):** login with roles; offline patient creation; reconnection and automatic synchronisation into MongoDB; the pull feed updating a second device; idempotent retries and lost-acknowledgement recovery; conflict **detection and classification** with no last-write-wins; supervisor viewing the conflict queue; clinician-only enforcement of clinical conflicts (via API/Swagger); account/session safety (expiry, logout protection, user switching); audit records in Atlas.
* **Cannot be demonstrated yet:** steps 9–11 of the planned demo — evidence review in the UI, patient confirmation, and clinician resolution in the UI — because the clinician and patient workspaces and the patient-confirmation endpoint do not exist, and the Resolve button fails. Do not present them as working.
* **Must be fixed before showing clinical resolution at all:** C5, C3/C4, C6.
Recommendation: proceed to Milestone 3 (conflict engine: C1, C2, C3/C4, C5, C6) and then the clinician/patient screens, before rehearsing the demo.

## 11. How to re-run
Start the backend (`backend\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000` from `backend`) and the frontend (`npm run dev` in `frontend`, with `frontend/.env` containing `VITE_API_URL=http://127.0.0.1:8000`), then from `e2e` run the scripts in order: `phase1_backend.py`, `phase3a_provision_and_roles.py`, `phase2_ui_auth.mjs`, `phase3b_api_sync.py`, `phase3_ui_workflows.mjs`, `phase4a_conflicts_create.py`, `phase4b_ui_conflicts.mjs`, `phase4c_conflicts_resolve.py` (Python ones with the backend venv; browser ones with `node`; `npm install` once in `e2e`). Every run adds more labelled synthetic records.
