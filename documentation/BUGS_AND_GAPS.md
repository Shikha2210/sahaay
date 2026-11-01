# BUGS_AND_GAPS.md — SAHAAY audit findings (2026-11-01)

All items come from reading the code. **None has been reproduced by running it** (Python deps not installed, no Atlas connection, no browser run). Each item lists file:line and confidence. Severity: **C** critical (breaks a core promise or is a security hole), **H** high, **M** medium, **L** low.

## A. Offline sync correctness

| ID | Sev | Finding | Where | Confidence |
|---|---|---|---|---|
| S1 | C | **Offline patient creation is always rejected.** The create op sends `identity_status`, but the server's `SAFE_FIELDS` has no `identity_status`, so the op is rejected `unsupported_fields`. The client marks it `rejected` and never retries; the patient exists only locally. | App.jsx:32 → main.py:17-18,110-111 | High |
| S2 | C | **Queue order is random.** `where('status').equals('pending').toArray()` returns by primary key (random UUID), not `created_at`. An update can be sent before its create → server stores a permanent `patient_not_found` receipt → client marks `rejected` (terminal) → local edit silently lost. | syncEngine.js:17; main.py:127-130 | High |
| S3 | H | **False self-conflicts.** `base_version` is fixed at queue time and never advanced. Two sequential edits from one device to the same field both carry the old base; the second sees `field_version > base_version` (bumped by the first) and creates a conflict against itself. | main.py:135; syncEngine.js:10 | High |
| S4 | H | **Tx-commit failure can be reported as success.** `results.append(applied)` happens inside the transaction. If commit then fails, the `except PyMongoError` appends a second `retryable_error` for the same op. Client processes the first (`applied`) and marks it acknowledged although nothing committed → violates "never mark synchronized before server acknowledgement". No transient-error retry (`with_transaction` not used). | main.py:112-154 | High |
| S5 | H | **Rejected ops are invisible and unrecoverable.** `rejected` status is stored locally but no screen lists failed ops; no retry/edit path. `acknowledged` ops are never pruned. | syncEngine.js:27; App.jsx:35 | High |
| S6 | H | **Pull overwrites/ignores local state.** `refresh()` does `bulkPut` of the server patient list over local rows (clobbers unsynced local changes to existing patients). Pull ignores patients absent locally. Local edits are not applied optimistically at all. | App.jsx:10; syncEngine.js:46 | High |
| S7 | H | **No UI for updating an existing patient.** Only "Register patient" exists. Requirement "local record updates" is not satisfied end-to-end. | App.jsx:32-33 | High |
| S8 | M | **Patient-ID collisions.** Client generates `SH-` + random 4-digit number. Two devices can create the same ID; server then treats the second `__create__` as an update with `base_version 0` → field conflicts or a merge into someone else's record. | App.jsx:32 | High |
| S9 | M | No cross-tab coordination (`running` flag is per-tab; no Web Locks/BroadcastChannel). Idempotency prevents duplicates, but wasted/competing syncs. | syncEngine.js:51 | High |
| S10 | M | `fetch` has no timeout; a hung request leaves `running=true` forever. A failed pull throws after a successful push, so UI says "Sync failed". Pending ops that fail have unlimited retries and never reach a `failed` state. | api.js; syncEngine.js | High |
| S11 | M | Conflict rows saved on the worker device contain only `{conflict_id, field, category}` (no values) and are never refreshed/closed for workers (only supervisor/clinician refresh them). Workers cannot see conflict notifications or outcome. | syncEngine.js:26; App.jsx:10; main.py:165 | High |
| S12 | M | Server change feed lets **every staff user** pull **all patients'** changes (no scoping). | main.py:157-162 | High |
| S13 | M | Seq counter incremented inside each transaction serialises all writers on one document; under concurrency expect many `WriteConflict` aborts (clients retry). Acceptable for demo; note limitation. | main.py:29-31 | Medium |
| S14 | L | Dashboard `pending_operations` counts server `operations` with status `pending/retryable_error`, which are never stored → always 0. | main.py:255 | High |
| S15 | L | Server `duplicate` replay rewrites a stored `rejected` receipt as `duplicate`, which the client treats as acknowledged. | main.py:117; syncEngine.js:22 | High |

## B. Conflict-resolution engine

| ID | Sev | Finding | Where |
|---|---|---|---|
| C1 | C | **Patient confirmation endpoint does not exist.** `CONFLICT_WORKFLOWS.md` documents `POST /api/patients/{id}/confirm-field`; `main.py` has no such route (README says it returns 501; it actually 404s). `patient_confirmations` collection is only indexed. PATIENT_VERIFIABLE conflicts therefore **can never be resolved**. | main.py (absent) |
| C2 | C | **UI resolve call always fails.** Supervisor UI sends `resolution:'Reviewed in SAHAAY supervisor interface'`; server requires `accept_a`/`accept_b`/`custom` → HTTP 400 every time. Also uses `prompt()`. | App.jsx:37; main.py:171 |
| C3 | H | **"Exact approved value" not implemented.** Evidence review records approve/reject only, not the approved value. The resolver (supervisor or clinician) may pick any value, including `custom`, unrelated to the evidence or patient confirmation. | main.py:230-238,191 |
| C4 | H | **Supervisors can resolve DOCUMENT_VERIFIABLE and PATIENT_VERIFIABLE** conflicts (not clinician-only; spec for A says clinician reviews/records value). Acceptable for B only if value is bound to the patient's confirmation. | main.py:169,179-182 |
| C5 | H | **Stale-conflict overwrite.** Resolution is guarded only against changes during the transaction. If the field changed after the conflict was created (newer legit edit), `accept_b` overwrites it. Needs check that current field value/version still equals the conflict's `server_version`/`value_a`. | main.py:189-196 |
| C6 | M | Evidence re-submission after rejection → unique index `(evidence_reference, conflict_id)` → uncaught `DuplicateKeyError` → HTTP 500. | main.py:226; db.py:20 |
| C7 | M | No separation of duties: a clinician can submit evidence and approve it. Evidence has no attachment, only a free-text reference; `reference`/`description` are **query parameters** (end up in URLs/logs), unbounded length. | main.py:220-238 |
| C8 | M | No "reject/dismiss conflict" outcome; no history array (resolution overwrites fields on the conflict doc; only the audit log keeps history). No conflict-creation audit entry other than inside SYNC_OPERATION metadata. | main.py:197-202 |
| C9 | M | Conflict has no unique key on `(patient_id, field, status=pending)`; repeated ops spawn duplicate pending conflicts. | main.py:137 |
| C10 | M | Link-account takes `email` as a query parameter; re-linking an already linked account silently overwrites `linked_patient_id`; no unique index on `linked_patient_id` (race); no recorded identity-check evidence/consent. | main.py:208-218 |
| C11 | L | Seeded "synthetic" conflicts use display names (`Date of birth`) not schema fields; they are correctly blocked from resolution but also appear in worker/patient UIs as if real (local `seed()` and server `CF-DEMO-*`). | main.py:39-40; db.js |

## C. Authentication / authorization / security

| ID | Sev | Finding | Where |
|---|---|---|---|
| X1 | H | **Known default admin password.** `BOOTSTRAP_SUPERVISOR_PASSWORD` defaults to `ChangeThisBeforeUse123!` in code; `SEED_DEMO_DATA` defaults to `true`. An unset env var yields a supervisor with a publicly known password. No `JWT_SECRET` strength check (placeholder text accepted). | config.py:13-15 |
| X2 | H | **PHI persists in IndexedDB after logout / user switch.** Logout clears token only; patients, ops and audit tables remain; the next user on the device sees them and pending ops would sync under the new user's token. | App.jsx:11; db.js |
| X3 | H | Token stored in `localStorage` (readable by any XSS); no refresh/rotation/revocation; 12 h expiry. 401 from API is not handled (no logout/re-login prompt), so expired sessions show confusing errors. | api.js; security.py |
| X4 | M | `loginApi` stores the token **before** the client-side role-match check; picking the wrong role leaves a valid token in storage. The role selector is cosmetic (UI role comes from client-controlled `sahaay-session`). Backend still enforces roles, so this is UI-confusion/hygiene, not a bypass. | api.js:15; App.jsx:11 |
| X5 | M | No login rate limiting/lockout; no audit of login success/failure; no password reset. | main.py:63 |
| X6 | M | Weak validation: `body:dict` for identity & chatbot; `OperationIn.changes` values untyped (any JSON, any size); `identity method` arbitrary string; `PatientIn` unused for sync creates (name/age not validated). | main.py:241,258; schemas.py |
| X7 | M | Patient records are readable by all staff for all patients (no assignment/consent scoping); full phone numbers returned unmasked. Documented limitation. | main.py:81-101 |
| X8 | M | Patient role cannot read **any** record: `/api/patients*` and sync endpoints exclude it, and no "my record" endpoint exists. Patient UI is hard-coded "Asha Devi". | main.py:82-86,98 |
| X9 | M | Clinician allowed to write via `/api/sync/batch` but not via `POST /api/patients` (inconsistent). | main.py:90,104 |
| X10 | L | CORS: credentials allowed with env-driven origins and `*` methods/headers; no guard against `*` in origins. Fine for dev, tighten for prod. | main.py:46-47 |
| X11 | L | `python-jose`/`passlib` are lightly maintained (bcrypt pinned to 4.0.1 for passlib compatibility). Consider PyJWT + direct bcrypt/argon2 later. `ObjectId(sub)` bad value → 500. | requirements.txt; security.py:20 |
| X12 | L | Audit log is a normal collection (mutable by anyone with DB access); most `audit()` calls are outside the business transaction; no hash chaining. | main.py:27 |

Positive: passwords bcrypt-hashed; JWT validated and user re-loaded from DB on every request (deactivation takes effect); public registration forced to `patient`; staff provisioning supervisor-only; Groq key never reaches the frontend; identity endpoint refuses to store raw QR/XML; no secrets committed.

## D. Identity & consent

| ID | Sev | Finding |
|---|---|---|
| I1 | M | Backend `identity/attempt` requires `consent: true` but stores only a boolean — no consent text version, timestamp of consent distinct from attempt, or patient acknowledgment. |
| I2 | M | An attempt never changes server state to a reviewable workflow: no endpoint for a supervisor/clinician to complete a **manual identity review** or set `identity_status`. Client sets local status to `PENDING` only. |
| I3 | L | Wording is careful and honest ("not verified", "signature not checked"). Keep. The "VERIFIED" badge on seeded demo patients is synthetic and should be labelled. |
| I4 | L | `recordAttempt` writes locally even if the server call fails offline and does not queue the server call for later (the attempt is lost server-side). |

## E. AI assistant

| ID | Sev | Finding |
|---|---|---|
| AI1 | M | Safety is a single system prompt; no server-side emergency-keyword short-circuit, no output filter, no rate limiting, no length cap on history (no history is sent at all — single-turn only). |
| AI2 | M | User's question and answer stored in `chat_messages` indefinitely; users might type PHI. No notice, retention, or deletion. No PII scrubbing before sending to Groq. |
| AI3 | L | Chat suggestion chips ("Explain my blood pressure reading", "Update my details") invite personalised medical or record questions the assistant must refuse. Dead `aiReply()` function in App.jsx:31 (unused). |
| AI4 | L | Sync `httpx.post` inside a `def` route (runs in threadpool — OK). No retry; generic 502. Errors surfaced in chat bubble text. |

## F. Frontend / UX / missing features

| ID | Sev | Finding |
|---|---|---|
| F1 | C | **Clinician has no UI.** `App` renders Patient/Worker/Supervisor only; clinician sees the supervisor nav but a blank body, and cannot review evidence or resolve conflicts from the app. |
| F2 | H | No UI for: evidence submission, evidence review, patient confirmation, account linking, audit history (placeholder text), failed ops, conflict notifications. |
| F3 | H | Patient pages are static ("Asha Devi", fake timeline, "Verified*"). Worker dashboard "Today's visits 08", "Identity checks 2 pending" are hard-coded. Supervisor "Identity reviews 02" hard-coded. |
| F4 | M | `alert()`/`prompt()` used for login errors, forgot password, conflict resolution. No confirmation dialogs; Settings/Support/Notifications buttons are stubs ("coming soon"). |
| F5 | M | Sync-centre text still says "Sync currently simulates acknowledgements locally. It is not connected to FastAPI." (stale/false after integration) and "Backoff integration pending". |
| F6 | M | No search/filter/pagination except patient name search; no loading states; no form validation (phone, name); registration allows empty phone → saves `'Not added'` as data. |
| F7 | M | UI font sizes 8–10 px throughout (identity page inline CSS) → poor accessibility. Dark-only theme, contrast not measured. Only ~2 responsive breakpoints. |
| F8 | L | `refresh()` runs `seed()` on every call; for supervisor/clinician, deleting pending conflicts then bulkPut means if the server returns none, the next refresh re-seeds fake conflicts. |
| F9 | L | Inline `<style>` tag re-injected on each IdentityWorker render; whole UI is in 2 files of minified-style code (hard to maintain/test). |

## G. Platform, PWA, deployment, testing

| ID | Sev | Finding |
|---|---|---|
| P1 | H | **Zero automated tests** (backend, frontend, e2e). |
| P2 | H | Backend never run against Atlas by the authors; `/health` and transactions unverified. Standalone local Mongo will fail all sync writes (transactions need a replica set). |
| P3 | M | PWA: plugin configured but unverified; SVG-only icon (installability prefers 192/512 PNG); API responses not cached by design (OK) — offline shell works only after first online load; login requires network. Google Fonts `@import` is an external dependency that fails offline. |
| P4 | M | No Dockerfile/Procfile/render.yaml, no `/ready` endpoint, no structured logging, no global exception handler, no request-ID, no production-mode CORS/HTTPS notes. |
| P5 | M | Entire backend in one 269-line file with dense one-liners; routes, models, business logic mixed (hard to unit-test). |
| P6 | L | Docs are inconsistent with code (README endpoint list omits evidence/link routes and claims 501 confirm endpoint; IMPLEMENTATION_STATUS claims compile-only checks). Superseded by this audit. |
| P7 | L | `START_BACKEND_WINDOWS.ps1` exits after creating `.env` (fine) but README setup steps 3 and 7 contradict each other (venv vs. global pip). |

## H. Top issues by risk to the hackathon demo

1. S1 + S2 + S3 — the headline offline→sync→conflict flow fails or produces wrong conflicts as the code stands.
2. C1 + C2 + F1 — three of four conflict categories cannot be completed from the UI; clinician has no UI.
3. X2 — PHI left on shared device.
4. X1 — default admin password.
5. S4 — possible false "synced".


---
# Status after Milestone 2 (2026-11-01)

| ID | Status | Notes |
|---|---|---|
| X1 default admin password / seeding / JWT secret strength | **FIXED** | No built-in password; `BOOTSTRAP_SUPERVISOR_PASSWORD` validated (12+ chars, mixed, no known default/guessable words/email name); `SEED_DEMO_DATA` default false; `JWT_SECRET` required, 32+ chars, not a placeholder; app refuses to start otherwise. Tested (🟡 bootstrap uses a fake collection). |
| X2 PHI left on device after logout / user switch | **FIXED (logic tested; UI untested in browser)** | Wipe on logout, device-owner lock, explicit discard dialog. Residual: IndexedDB unencrypted; tab closed without logout leaves data until next login (see AUTH_OPTIONS). |
| X3 token in localStorage / no 401 handling | **PARTIALLY FIXED** | sessionStorage + 60-min tokens + clean 401/expiry handling. Still JS-readable. No refresh tokens/revocation. |
| X4 token stored before role check | **FIXED** | Login no longer stores anything; token stored only after device-claim succeeds. UI role selector remains cosmetic (backend enforces roles). |
| X5 login rate limit / audit | **PARTIALLY FIXED** | Rate limit added (per-process memory). Login success/failure **audit logging not done** (needs DB; Milestone 6). |
| X6 weak validation | **PARTIALLY FIXED** | Typed identity & chat bodies (`extra=forbid`), per-field type/length checks and id formats for sync ops (reported per operation as `rejected`), `status`/other query validation still open. Evidence/link-account still use query parameters (Milestone 3). |
| X10 CORS | **FIXED** | No wildcard (startup error), credentials off (Bearer header only), methods/headers restricted. |
| X11 `ObjectId(sub)` 500 | **FIXED** | Bad `sub` → 401. Library maintenance concern (python-jose/passlib) still open. |
| F8 local seed re-adding fake conflicts | **FIXED** | Local seeding only when `VITE_SEED_DEMO_DATA=true` (default off). |
| S10 (partial) no fetch timeout | **PARTIALLY FIXED** | 20 s timeout + friendly errors. No max-retry/failed state yet. |
| S1, S2, S3, S4, S5–S9, S11–S15, C1–C11, X7–X9, X12, I*, AI*, F1–F7, F9, P* | **OPEN** | Unchanged. The 6 reproduction tests still show the expected failures. |
| **X13 (new)** default `BOOTSTRAP_SUPERVISOR_EMAIL=supervisor@sahaay.local` could never log in | **FIXED** | Found by tests: `.local`/`.test`/`.invalid` are rejected by the same email validation the login form uses (HTTP 422), so the first supervisor was unreachable. Now validated at startup; default/example is `supervisor@example.com`. Regression test added. |
| **X14 (new)** Idle/unattended device shows cached PHI after token expiry (soft-expiry design) | **OPEN (documented trade-off)** | Consider idle auto-lock (Milestone 6/7). |
| **X15 (new)** IndexedDB data is unencrypted | **OPEN** | Out of scope for the prototype; document in pitch. |
| **X16 (new)** No Content-Security-Policy; third-party Google Fonts import | **OPEN → Milestone 9** | Set CSP header on the static host; self-host fonts. |

---
# Status after Milestone 2b (sync correctness)

| ID | Status | Fix / evidence |
|---|---|---|
| **S1** offline patient create rejected (`identity_status`) | **FIXED (logic-tested with an in-memory stand-in; real Atlas run pending)** | `identity_status` is accepted on create ONLY and ONLY as `UNVERIFIED`; `VERIFIED` (or any change via an update op) is rejected, so sync can never mark anyone verified. Defaults to UNVERIFIED. |
| **S2** random operation order | **FIXED** | Client: per-device sequence number (`seq`) orders the queue (clock-skew proof; legacy ops fall back to time); an op is never sent ahead of an earlier op for the same patient that is waiting for retry or whose create was rejected. Server: batch processed strictly in order; after a failure, later ops for that patient return `blocked_by_earlier_operation` (kept pending, no back-off penalty). `patient_not_found` no longer stores a permanent receipt, so the same operation can succeed once its create arrives. |
| **S3** false self-conflicts | **FIXED (logic-tested; real Atlas run pending)** | Server records the last writer device per field (`field_writers`). A field changed after `base_version` is a conflict UNLESS the same device wrote it last; another device's write is ALWAYS a conflict (no last-write-wins for clinical data; covered by test). Client `base_version` now defaults to the local patient's version (0 for new patients) instead of a hard-coded 1. |
| **S4** failed commit reported as applied | **FIXED (simulated commit failure; real Atlas rollback test pending)** | Each operation runs in `session.with_transaction(...)` (driver retries transient errors) and a result is built only after commit. Exactly one result per operation; commit failure or mid-transaction error -> `retryable_error`, nothing persisted, same op_id succeeds once on retry. |
| S15 `rejected` receipt rewritten as `duplicate` | **FIXED** | Replays return `duplicate` only for applied/conflict receipts. |
| **New (found while fixing)** `.env` was read relative to the current directory | **FIXED** | Config now always reads `backend/.env`. |
| Residual / still open | OPEN | S5-S9, S11-S14, C1-C11 unchanged. Known limit: a pending conflict's `value_a` can go stale if its own device later overwrites that field (C5). Two devices sharing one `device_id` (same browser profile) are treated as one device by design. |

# Verification on real Atlas (2026-11-01)
S1, S3, S4 and the idempotency/ordering guarantees now also pass against a real Atlas replica set (see TEST_REPORT_M2.md). Server-side S2 ordering is covered by stand-in tests; the real-Atlas suite covers create/update/conflict flows only. New operational note: **Atlas Network Access must contain every public IP the machine uses** (intermittent `TLSV1_ALERT_INTERNAL_ERROR` otherwise).

---
# Confirmed on the live system — Milestone 2C (2026-11-01)
Full evidence and reproduction steps in `MILESTONE_2C_REPORT.md`.
* **New / re-prioritised:** C5 (stale conflict overwrote newer dosage, **High, clinical**), C6 (HTTP 500 on evidence resubmission, confirmed), C3/C4 (supervisor resolved a document conflict with an arbitrary value, confirmed), C1/C2/F1/X8/S5/S11/X5 confirmed in the running app; S9 refined: manual "Sync now" and the auto-sync timer can upload the same operation concurrently (safe because of server idempotency).
* **Fixed bugs verified live on Atlas:** S1, S3, S4, ordering (S2), idempotent replay, lost-acknowledgement recovery, X1/X2/X3/X4, bootstrap and index creation.
* **Operational:** synthetic accounts/records (`ZZ-SYNTHETIC ...`) now exist in the real `sahaay` database; there is no delete/deactivate endpoint.
