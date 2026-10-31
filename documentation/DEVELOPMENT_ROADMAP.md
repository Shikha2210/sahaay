# DEVELOPMENT_ROADMAP.md — proposed implementation order

Principle: fix what the demo story depends on (offline → sync → conflict → resolve → audit) first, backend before frontend, and add tests as each milestone lands. IDs refer to BUGS_AND_GAPS.md.

## Decisions needed before Milestone 1 (see end of audit summary)
1. **Repo consolidation:** the git repo has the backend but not the Vite project. Proposal: copy `sahaay-dark-dashboard` into this repo as `frontend/`, move backend to `backend/`, keep the originals untouched. (Moves files; needs your OK.)
2. **MongoDB:** you need an Atlas cluster (free M0 supports transactions) and a database user. For automated tests I propose `mongomock` is **not** used (it lacks transactions); instead tests run against a local single-node replica set or a dedicated Atlas test database. Tell me which you can do.
3. **Groq API key** (optional until Milestone 2; chatbot returns 503 without it).
4. Keep hand-written CSS and drop the unused Tailwind devDependency? (Recommendation: leave it, don't churn.)

## M1 — Consolidate & make it runnable (small) — **DONE 2026-11-01 (Atlas run still pending)**
- Create `frontend/` (copy of Vite project) and apply the 4 integration files; `npm install`, `npm run build`, `npm run dev`.
- Backend: create venv, install, run against Atlas, verify `/health`.
- Smoke-run the current flow once to **reproduce** S1/S2/C2 so fixes are proven, not assumed.
- Commit.

## M2 — Backend correctness & security (Backend first) — **SECURITY HALF DONE 2026-11-01; sync-correctness half moved to M2b**
- Split `main.py` into routers/services (auth, patients, sync, conflicts, identity, chat, audit); no behaviour change first.
- Config: refuse to start if `JWT_SECRET` is short/placeholder or bootstrap password is default (X1); `SEED_DEMO_DATA` default false.
- Validation: typed schemas for sync `changes` per field (types/lengths), identity/chat bodies, evidence/link bodies in JSON not query (X6, C7, C10).
- Sync fixes: allow `identity_status` on create or drop it client-side (S1); `with_transaction` + retry for transient errors; never append result before commit (S4); don't turn `rejected` into `duplicate` (S15); don't persist `patient_not_found` as terminal—return `retryable`/`blocked_by_dependency` (S2); correct dashboard counters (S14); patient-ID server-assigned or collision-safe (S8).
- Auth: login audit, simple rate limit, uniform 401, token expiry claim `iat`, optional token-version for revocation.
- Patient scoping: `GET /api/me/record` for patients; staff-side scoping hook (assignment) kept simple.
- Logging: structured, no PII; global exception handler; `/health` + `/ready`.
- Tests: pytest suite for auth, roles, idempotency, concurrency (needs replica set).

## M3 — Conflict engine
- Evidence review records `approved_value`; resolution must equal it (A).
- `POST /api/patients/{id}/confirm-field` + patient "my conflicts" endpoint; confirmation bound to authenticated linked patient; resolution must equal confirmed value; confirmation never mutates record (B).
- Resolve policy matrix enforced server-side: A clinician; B clinician or supervisor using patient-confirmed value only; C/D clinician only.
- Stale-conflict guard (C5), `reject` outcome, per-conflict history array, one pending conflict per (patient, field) (C8, C9), separation of duties (C7), evidence resubmission (C6), link-account hardening with unique index (C10).
- Tests per category incl. 403s, duplicate resolution, concurrent resolution.

## M4 — Offline sync rework (client)
- Ordered queue (sort by sequence number), per-patient dependency (create before update), `base_version` tracked per patient and advanced by local ops (S2, S3).
- Optimistic local apply for create/update, preserved across pull/refresh (S6, S7); pull merges only when no pending local op touches that field.
- States: `pending → sending → acknowledged | conflict | failed`; max attempts → `failed` with manual retry/discard-with-confirm (S5, S10).
- Request timeout via AbortController; Web Locks API (fallback BroadcastChannel) for single sync leader across tabs (S9).
- Clear all local PHI + queue-guard on logout/user switch; bind queue to user id (X2).
- Conflict notifications for workers via a new `GET /api/sync/conflicts` (own ops only) (S11).
- Tests: Vitest + fake-indexeddb for queue; offline simulated with mocked fetch.

## M5 — Frontend workflows for all four roles
- Split App.jsx into `pages/`, `components/`, `hooks/` (keeping markup/CSS classes so look is preserved). Add Toast, ConfirmDialog, Modal, DataTable (search/filter/pagination), loading/empty states. Remove `alert/prompt`.
- Patient: my record, my conflicts + confirm, sync status, AI assistant.
- Worker: dashboard, directory, patient detail + edit, evidence submission, failed/pending ops, conflict notices.
- Supervisor: dashboard (live counts), link-account wizard, conflict/identity/sync monitoring, audit history with filters.
- Clinician: dashboard, review queue, evidence approve/reject with value, resolve, unknown-conflict review, resolution history.

## M6 — Security hardening
- Token handling decision (see below), 401 handling → clean logout, idle timeout, CORS tightening, security headers, dependency audit (`pip-audit`, `npm audit`), log scrub review, bundle scan for secrets.
- Token storage recommendation: **httpOnly, Secure, SameSite cookie for a short-lived access token + CSRF header** is best, but it complicates cross-origin Netlify/Render hosting and offline use. Pragmatic hackathon choice: keep Bearer token in memory + `sessionStorage` (cleared on tab close), short expiry, logout wipes local DB. I will propose this and ask before changing.

## M7 — UI/UX
- Raise base font sizes (≥12–14 px), check contrast (WCAG AA), keyboard focus, responsive tables → cards on mobile, status chips, consistent dialogs. No functional removals.

## M8 — Testing & QA
- Backend pytest (real Mongo replica set), frontend Vitest + React Testing Library, e2e Playwright (two browser contexts = two devices, `context.setOffline`). Produce TEST_CASES.md, TEST_REPORT.md (actual output only), BUGS_REMAINING.md.

## M9 — Deployment prep
- `.env.example`s, `DEPLOYMENT.md`, README rewrite, PNG PWA icons, self-hosted fonts (offline), service-worker verification, `Procfile`/`render.yaml`, production CORS. **No public deployment without your explicit approval.**

## M10 — Hackathon material
- SYSTEM_ARCHITECTURE.md, HACKATHON_DEMO.md (12-step 3–5 min script), PITCH_SCRIPT.md, JUDGES_QA.md, DEMO_DATA.md (synthetic seeds + a one-command reset script). Honest "implemented / simulated / limitations" slide.

## Effort estimate (rough)
M1 small · M2 large · M3 large · M4 large · M5 largest · M6–M7 medium · M8 large (parallel with others) · M9–M10 small/medium. If time is short for the hackathon, cut order: M7 polish, then M6 beyond basics; never cut M2–M4 or the tests for them.

## Things I will not do without asking
Move/delete folders (M1), change token storage (M6), drop collections/migrate existing Atlas data, deploy publicly, add new major dependencies (Vitest, Playwright, pytest are proposed test tooling).

## Update after M1
- New paths: `backend/`, `frontend/`, `documentation/` (older text above refers to the former layout).
- Added to M2 (first tasks, because they are security priorities): fail-to-start on weak/missing `JWT_SECRET` and default bootstrap password; `SEED_DEMO_DATA` default false; logout/user-switch wipe of IndexedDB with unsynced-ops warning; per-user tagging of queued ops; 401 handling. Flip the 6 reproduction tests from xfail to normal tests as each bug is fixed.
- Needs from you before M2 can be fully verified: (1) Atlas M0 cluster + DB user + Network Access entry for your IP → put the URI in `backend/.env`; (2) decision on `AUTH_OPTIONS.md`.
- Dependency note: `npm audit` shows 1 high + 2 critical in dev tooling; to be reviewed in M6 (not run `audit fix` blindly).

## Update after M2
* **M2 (done):** security/auth items above. See TEST_REPORT_M2.md.
* **M2b (next, needs Atlas for real verification):** S1 (create op), S2 (ordered queue), S3 (base_version), S4 (commit-failure result), S15, S14, patient-ID collisions (S8); flip the six reproduction tests to normal tests as each is fixed; first real-Mongo test run (idempotency, transaction, concurrency).
* Moved: login success/failure audit log → M2b (needs DB); CSP header + self-hosted fonts → M9; idle auto-lock → M7.
* **Need from you before M2b can be verified:** Atlas M0 + `backend/.env` (`MONGODB_URI`, `JWT_SECRET` from `python -c "import secrets; print(secrets.token_urlsafe(48))"`, `BOOTSTRAP_SUPERVISOR_EMAIL` with a real-looking domain, `BOOTSTRAP_SUPERVISOR_PASSWORD` of your choosing). Code-only work on S1–S4 can still start without it.
