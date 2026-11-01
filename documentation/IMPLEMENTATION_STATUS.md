# IMPLEMENTATION_STATUS.md — audit baseline (2026-11-01)

Replaces the earlier self-reported status (still available in git: `git show 191ac0c:sahaay-v3/IMPLEMENTATION_STATUS.md`).
**Basis:** code reading only. Nothing has been executed or tested, so "Fully implemented" below means *the code for it exists and appears correct on reading*, not *verified working*. Nothing is verified by a test yet.

Legend: **Full** fully implemented · **Partial** · **FE** frontend only · **BE** backend only · **Mock** mock/simulated · **None** not implemented · **Broken** broken or contradicted by code.

## Authentication & authorization
| Feature | Class | Notes |
|---|---|---|
| bcrypt password hashing | Full | passlib+bcrypt |
| JWT login/register/me | Full | No expiry handling in UI (X3) |
| Server-side role checks (`require_roles`) | Full | Consistently applied; patient scoping missing |
| Staff provisioning by supervisor | BE | No UI |
| Patient→record linking | BE | Route exists; no UI; weak controls (C10) |
| Patient access to own record | None | X8 |
| Login rate limiting / lockout / reset | None | |
| Safe local data on logout | Broken | X2 |

## Offline-first & sync
| Feature | Class | Notes |
|---|---|---|
| Dexie local DB, v1→v2 migration | Full | |
| Local patient list read offline | Full | After at least one online load |
| Local patient **creation** | Broken | Queued but server rejects (S1) |
| Local patient **update** | None | No UI (S7) |
| Persistent op queue, UUIDs, timestamps | Full | |
| Ordered replay | Broken | S2 |
| Retry with backoff | Partial | No max/“failed” state (S10) |
| Server idempotency (`operation_id`) | Full | Unique index + receipt |
| Ack handling | Partial | S4, S15 |
| Pull change feed with cursor | Partial | S6, S12 |
| Field-level conflict detection | Partial | False self-conflicts (S3) |
| Conflict classification | Full | Static field→category map |
| Cross-tab coordination | None | S9 |
| Crash/refresh recovery | Partial | Ops persist and replay is idempotent; untested |
| Network recovery (`online` event + 15 s timer) | Full | |
| Failed/rejected ops screen | None | S5 |

## Conflict workflows
| Category | Class | Notes |
|---|---|---|
| A DOCUMENT_VERIFIABLE | Partial (BE) | Submit + clinician review + resolve exist; approved value not captured (C3); no UI |
| B PATIENT_VERIFIABLE | Broken | Confirmation endpoint missing (C1) |
| C CRITICAL_CLINICAL | Partial (BE) | Clinician-only enforced server-side; no clinician UI |
| D REQUIRES_REVIEW | Partial (BE) | Clinician-only; no UI |
| Duplicate-resolution prevention | Full | Status-filtered update in transaction |
| Concurrent-update safety | Partial | C5 |
| Mongo transactions | Full (code) | Needs replica set; unverified |
| Audit of resolution | Full | Inside transaction |
| Conflict history | Partial | C8 |
| Resolution UI | Broken | prompt()-based and always 400 (C2) |

## Frontend by role
| Role | Class | Notes |
|---|---|---|
| Patient | Mock | Static content; only AI chat is real |
| Worker | Partial | Directory (read), register patient, identity workflow, sync page; no update, evidence, conflict notices, failed ops |
| Supervisor | Partial | Conflict list real, resolve broken; dashboard/identity/sync pages partly hard-coded; audit page placeholder; no link-account UI |
| Clinician | None | No screens at all (F1) |

## Identity & consent
| Feature | Class | Notes |
|---|---|---|
| QR decode (BarcodeDetector) | FE | Honest labelling ✔ |
| XML parse | FE | Syntax only ✔ |
| Consent checkbox + info modal | FE | |
| Backend attempt record | Partial | Boolean consent only (I1) |
| Manual identity review | None | I2 |
| Cryptographic verification | None | Correctly **not** claimed |

## AI assistant
| Feature | Class | Notes |
|---|---|---|
| Groq via backend | BE+FE | Needs key; never run |
| Safety prompt | Partial | Prompt only (AI1) |
| Error/unavailable handling | Partial | |
| History / rate limit / PII scrub | None | |

## Platform
| Feature | Class | Notes |
|---|---|---|
| Vite build | Untested | Project lives in separate folder |
| PWA (vite-plugin-pwa) | Untested | |
| Tests (any kind) | None | |
| Deployment config / CI | None | |
| Structured logging, ready probe | None | |

## Milestone tracker (to be updated after each milestone)
| # | Milestone | Status |
|---|---|---|
| 0 | Audit + documentation | **Done** (this document set) |
| 1 | Consolidate repo (frontend + backend) | **Done** 2026-11-01 — see TEST_REPORT_M1.md. Atlas run pending (blocker) |
| 2b | Sync correctness S1-S4 | **Done and verified on real Atlas** (10/10 integration tests, 2026-11-01). Not yet tested on Atlas: HTTP login flow, pull feed, bootstrap, browser UI |
| 2 | Security & auth (part of backend correctness) | **Done 2026-11-01** (code-only; Atlas-dependent verification **blocked**). Sync/transaction fixes (S1–S4) deferred to next milestone |
| 3 | Conflict engine | **Done 2026-11-02** — C1–C11 fixed; see MILESTONE_3_REPORT.md. UI for patient/worker/link-account still to do (M5) |
| 4 | Offline sync rework | Not started |
| 5 | Frontend workflows (all 4 roles) | Not started |
| 6 | Security hardening | Not started |
| 7 | UI/UX polish | Not started |
| 8 | Automated tests + reports | Not started |
| 9 | Deployment prep | Not started |
| 10 | Hackathon material | Not started |

## Milestone 1 result (2026-11-01)
- Repo restructured (git history kept): `backend/`, `frontend/`, `documentation/`. Integration `App.jsx/api.js/db.js/syncEngine.js` applied on top of the original Vite project (original simulated `App.jsx`/`db.js` kept in `documentation/original-frontend-simulated/`). Original folders in Downloads untouched.
- Frontend `npm ci` + production build + dev server: passed. Backend deps installed; fails fast without config (expected).
- Six critical bugs: reproduced by tests (4 backend, 2 frontend); see TEST_REPORT_M1.md.
- **Blocked:** Atlas connection (backend can't start; transactions/idempotency/auth E2E untested). Groq key deferred.
- **No behaviour changes to source yet** apart from file moves, new tests/config, `.env.example` and `.gitignore`.
- Auth/token-storage evaluation written: `AUTH_OPTIONS.md` (awaiting your decision).

## Milestone 2 result (2026-11-01) — see TEST_REPORT_M2.md
Done: strong-secret enforcement, no default supervisor password, seeding off by default, short-lived tokens, sessionStorage token, structured 401s, rate-limited login, typed/validated inputs, security headers, strict CORS; frontend: per-user local data isolation (device owner lock), op ownership tagging, logout with unsynced-change confirmation, soft session expiry; dependency audit (critical/high removed); 59 backend + 24 frontend tests passing, build passing.
Not done / blocked: anything needing Atlas; refresh tokens; login audit log; browser verification of new dialogs; CSP header; sync correctness bugs S1–S4 (reproduced, still failing as expected).
Changes to existing behaviour to be aware of: tokens now expire after 60 min (was 12 h); first supervisor needs a real-domain email and a strong password in `.env` or the backend refuses to start; the frontend no longer shows local fake demo patients unless `VITE_SEED_DEMO_DATA=true`; `/api/auth/*` user objects now include `id`.

## Milestone 2b result (2026-11-01) — see TEST_REPORT_M2.md addendum
* Offline patient creation now works end-to-end in logic tests (S1); queue replays in creation order and holds dependent ops behind failed ones (S2); sequential edits from one device no longer self-conflict, other devices' writes still conflict (S3); results are reported only after a committed transaction (S4).
* Preserved: persistent queue, retry/back-off, idempotency, owner tagging, no-LWW for clinical fields.
* Tooling added: `backend/scripts/setup_env.ps1` (hidden-input .env setup) and `backend/scripts/check_atlas.py` (read-only ping). **Neither has been run against Atlas yet.**
* Blocked: all real-database verification until the user runs `setup_env.ps1`.

## Atlas verification (2026-11-01)
* `backend/.env` created via `scripts/setup_env.ps1` (git-ignored). `check_atlas.py` 5/5 PASS after the two public IPs were added to Atlas Network Access (earlier `TLSV1_ALERT_INTERNAL_ERROR` = IP allow-list).
* Real-Atlas integration tests: **10 passed**: persistence, idempotent replay, transaction commit, real rollback, no-LWW conflict, concurrent resolution (exactly once), role denial, audit persistence. Real `sahaay` database verified untouched.
* Remaining risk: public IP can change again; any new address must be added to Network Access.

## Milestone 2C result (2026-11-01) — real end-to-end verification
See `MILESTONE_2C_REPORT.md`. Executed against the real backend, real Atlas `sahaay` database and real browser: **222 checks passed, 15 failed (≈11 distinct genuine defects), 2 notes**. Verified working: bootstrap, indexes, HTTP auth, role matrix, session expiry/logout/user-switch protection, offline create, reconnect sync, pull feed, idempotency, lost-ack recovery, retry/back-off, conflict detection for all four categories without last-write-wins, clinician-only enforcement. Not working / missing: clinician and patient workspaces, patient confirmation endpoint, UI conflict resolution, value binding to evidence, stale-conflict guard, failed-operations UI. **Demo readiness: sync demo yes; full 12-step demo no.** Milestone 3 not started.

## Milestone 3 result (2026-11-02) — conflict engine safety and correctness
Server-enforced policy per category; stale-version guard (`expected_field_version`) with atomic conditional updates; evidence records the clinician's exact approved value and resolutions copy it; patient confirmation endpoint (own record only); reject outcome + conflict history; duplicate-pending prevention and one-account-per-record enforced by partial unique indexes; structured errors everywhere. Tests: backend 116 passed (+19 real-Atlas passed), frontend 40 passed, build passed, browser workflow 23/23 on a disposable database; real `sahaay` untouched. Not started: Milestone 4 and any unrelated features.
