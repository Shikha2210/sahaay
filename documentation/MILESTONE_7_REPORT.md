# Milestone 7 – Final security, deployment and hackathon readiness

Branch `milestone-7-security-deployment` (from M6). **Nothing was deployed and no production infrastructure was changed.** Companion document: `DEPLOYMENT_GUIDE.md`.

## Verdict
* **Demo / hackathon use with synthetic data: READY**, provided the pre-launch checklist in `DEPLOYMENT_GUIDE.md` §1 is done (above all: a scoped Atlas user, HTTPS hosting that applies the documented headers, a generated `JWT_SECRET` and supervisor password).
* **Real patient data: NOT READY.** Open items include a legal/compliance review, a replaced database user, shared rate limiting, password recovery, and an independent security test (see "Remaining risks").

## What was and was not tested (honesty notes)
* Browser tests ran in **Microsoft Edge only**, headless, against real MongoDB Atlas, using disposable databases (`sahaay_test_*`, `sahaay_rehearsal_*`) and **synthetic data**. All disposable databases were emptied afterwards (the Atlas user cannot `dropDatabase`).
* The **production rehearsal** used: backend with `ENVIRONMENT=production` over HTTPS with a **throw-away self-signed certificate**, the production frontend build served by my own small server (`e2e/serve_prod.mjs`) carrying the documented headers, and Atlas. It is a rehearsal on `localhost`, **not** a deployment on a real host, domain or CA-signed certificate. The browser was launched with `--ignore-certificate-errors` so that the service worker would run on the self-signed certificate.
* **Mobile:** **no real phone was available or used → real-device testing is BLOCKED/not done.** What was done is *emulation* in Edge (393×851 and 320×568 viewports, touch taps, mobile user agent, device pixel ratio). It does not prove install-to-home-screen, the OS keyboard, real touch hardware, or behaviour on Safari/iOS.
* **Manual accessibility:** I cannot operate a screen reader. The "manual-style" keyboard checks are scripted **real key presses** (Tab, Enter, Escape), not a human walkthrough. axe-core (WCAG 2.0/2.1 A+AA) was run on all 25 navigation pages of all four roles; automated tools catch only part of the problems.
* No penetration test or external review was performed. The Groq service was never contacted (no key; test double only, unchanged from M5).
* The real `sahaay` database was only read (counts, indexes, and the user's privileges). Counts after M7 are identical to those recorded at the end of M6 (audit 76, changes 58, chat_messages 0, conflicts 8, counters 1, evidence 3, identity_attempts 0, operations 61, patient_confirmations 0, patients 30, users 6) — a real before/after comparison.

## 1. Security audit

### Authentication, authorization, API access
| Check | Result |
|---|---|
| Every non-public route returns 401 for a missing or garbage token (every route in the live route table, swept automatically) | **PASS** (`test_m7_security.py`) |
| Every route that declares roles returns 403 to every *other* role (≥40 combinations, never a 500 or data) | **PASS** |
| Only four routes are open to "any signed-in user" (logout, me, AI status, chatbot); the list is pinned by a test so a new unprotected route fails the build | **PASS** |
| Tokens signed with another key or with `alg:none` are rejected | **PASS** |
| Role is taken from the database user on every request (a token cannot claim a role); disabled accounts are rejected immediately | **PASS** (code review + existing tests) |
| Login errors do not reveal whether an account exists; failed/blocked/successful sign-ins and sign-outs are audited without passwords, tokens or typed e-mails | **PASS** (M6, re-verified on production rehearsal) |
| CORS: only the configured origin; no credentials; other origins and the dev origin are refused in production mode | **PASS** (rehearsal) |
| Patient-data isolation: patient accounts reach only their own linked record/conflicts (M4/M5 Atlas + API suites re-run); patients cannot list the registry (403) | **PASS** |
| Workers can read the whole patient registry | **By design** (shared offline registry); documented as a risk below |

### Fixed in M7 (findings and fixes)
| # | Finding | Severity | Fix |
|---|---|---|---|
| 1 | API documentation (`/docs`, `/redoc`, `/openapi.json`) public in production | Medium | `ENVIRONMENT=production` disables them (verified 404) |
| 2 | **Startup validation errors echoed the rejected value** (pydantic prints `input_value`, e.g. a weak `JWT_SECRET` or the Atlas URI) into logs | **High** | `hide_input_in_errors=True`; test proves the secret never appears |
| 3 | No production-mode safety net: the dev `.env` (localhost CORS, demo seeding, plain-http AI URL, non-TLS DB, example supervisor address) could be deployed | **High** | Production mode refuses to start on any of these (8 parametrised tests; verified live that the dev `.env` is refused) |
| 4 | Password spraying: limit was per address+account only | Medium | Added per-address limit across accounts (30 failures / 5 min) |
| 5 | Unlimited self-registration | Medium | 10 registrations per address per hour |
| 6 | Real client address unreliable behind a proxy | Medium | `TRUST_PROXY_HEADERS` (off by default) uses the proxy's `X-Forwarded-For` |
| 7 | Missing response headers | Medium | API: CSP `default-src 'none'`, Permissions-Policy, CORP, HSTS (production), plus existing nosniff/frame/referrer/no-store. Frontend headers specified in the guide and rehearsed (strict CSP: `script-src 'self'`, no `unsafe-eval`) |
| 8 | **Dependencies: 46 known advisories** in the backend (starlette, python-multipart, pymongo, pyasn1, ecdsa, python-jose) | **High** | Upgraded FastAPI/Starlette/python-multipart/pymongo/uvicorn/pydantic; **replaced python-jose with PyJWT** (removes ecdsa, pyasn1, rsa). `pip-audit`: **0 known vulnerabilities**. A fresh virtualenv built only from `requirements.txt` passes all 248 tests |
| 9 | Third-party request to Google Fonts (leaks user IPs, breaks offline, blocks a strict CSP) | Medium | Fonts self-hosted (`@fontsource`, latin subset); rehearsal confirmed no third-party hosts |
| 10 | Production build could silently embed `http://127.0.0.1:8000` | Medium | `npm run build` refuses unless `VITE_API_URL` is https and not localhost and demo data is off (`SAHAAY_LOCAL_BUILD=1` for local rehearsals) |
| 11 | Extra indexes for audit queries and role lookups | Low | added in `init_db` |

### Session security (review)
* Access token: HS256 JWT, 60 minutes, kept in **sessionStorage** (cleared when the tab closes; verified not in localStorage or IndexedDB; local data is wiped on sign-out/user switch).
* The XSS exposure of sessionStorage is mitigated, not removed: scripts are `'self'` only, no inline scripts, no third-party scripts.
* **No server-side revocation:** sign-out is audited but the token stays valid until expiry (≤60 min). Rotating `JWT_SECRET` or setting `active:false` ends access immediately. Accepted risk, documented.

## 2. MongoDB review
| Item | Finding |
|---|---|
| Application user privileges | **`readWriteAnyDatabase` on `admin`** — wider than needed (can read/write *every* database in the cluster, e.g. the sample data). Not `dropDatabase`, not user admin. **Action for you:** create a user with `readWrite` on `sahaay` only (guide §3) before production. I cannot change Atlas settings from here. |
| TLS | `mongodb+srv` (TLS); production mode now refuses non-TLS URIs |
| Indexes (live `sahaay`) | Present: unique `users.email`, `patients.patient_id`, `operations.operation_id`, `conflicts.conflict_id`, `changes.seq`, `evidence`, `patient_confirmations`. Some newer indexes (unique linked-patient/pending-conflict, new audit indexes) are created by `init_db` the next time the backend starts against that database; they were created and exercised on every disposable database. **Check the first production start log**: creating a unique index fails if duplicate data already exists. |
| Atlas IP allow-list, tier, backups | **Not visible from the app credentials; not verified.** Guide §1/§3 lists what to check (no `0.0.0.0/0`; M0 has no automated backups) |
| Backup & recovery | New `scripts/backup_db.py` (read-only) and `scripts/restore_db.py` (into a new empty database only; SHA-256 and count verification; index re-creation). **Tested on disposable databases:** data and unique index restored identically; refusals verified for live name, non-empty target, backup inside the repo, tampered file. The live `sahaay` database was not backed up or restored in this milestone. |

## 3. Secrets, environment, CORS, headers, dependencies
* `backend/.env` is git-ignored and not tracked; git history was scanned: **no real secrets** (only a legacy placeholder `ChangeThisBeforeUse123!` in an old `.env.example`, already blocked by the startup validator and never to be used).
* Weak or placeholder `JWT_SECRET`/supervisor passwords are rejected at startup (existing + new tests). `.env.example` documents the new variables.
* **Backend dependencies:** 0 known vulnerabilities (`pip-audit`). **e2e tooling:** 0 (`npm audit`). **Frontend:** 9 moderate/low advisories remain, all in the **build-time** toolchain (workbox-build → glob/minimatch/ejs/jake, deepmerge); `npm audit fix` could not resolve them without a breaking upgrade. Not shipped to browsers; revisit when vite-plugin-pwa updates.

## 4. PWA icons
Generated from the existing branding (`icon.svg`, purple tile with white cross) by `e2e/make_icons.mjs`: `icon-192/512.png` (any), `icon-maskable-192/512.png` (cross scaled into the safe zone), `apple-touch-icon.png` (180). Manifest and `index.html` updated; SVG kept. Rehearsal: all icons served as `image/png`; Edge reports **no installability errors** (checked in a non-incognito profile).

## 5. Accessibility fixes found by the checks
* **Keyboard focus was invisible** on inputs (CSS removed the outline) → global `:focus-visible` ring; 13/13 tab stops now show a focus indicator.
* **Enter did not submit the sign-in form** (no `<form>`) → real form; role buttons expose `aria-pressed`.
* Unnamed `<select>` (identity verification), toast dismiss button and sign-out button too small/unnamed; toast is now a polite live region.
* Dialog behaviour (M6) re-verified: role/name, focus move/trap, Escape, focus return.

## 6. Test results
| Suite | Result |
|---|---|
| Backend (in-memory stand-in) | 248 passed, 28 skipped (Atlas opt-in) — includes 20 new security tests |
| Backend **with real Atlas** (`SAHAAY_ATLAS_TESTS=1`) | **277 passed, 0 skipped** (28 Atlas integration tests) |
| Frontend vitest | 80 passed |
| Production build (guard: refuses without https API URL; builds with it) | OK |
| **Production rehearsal** `phase_m7_production.mjs` (HTTPS, production mode, Atlas) | **37 PASS, 0 FAIL** — docs hidden, HSTS/CSP/headers, CORS allow/deny, role checks, cross-origin sign-in, offline register → offline reload → reconnect → synced on the production API, installability, no third-party hosts, no CSP violations, no token in localStorage/IndexedDB, keyboard-only sign-in and dialog |
| Mobile **emulation** `phase_m7_mobile_emulation.mjs` | 16 PASS, 0 FAIL (2 viewports) |
| axe sweep, 4 roles × all pages `phase_m7_a11y.mjs` | 25 pages, **0 critical/serious**, no moderate/minor reported |
| Regression: M6 browser / M3 / M4 / M5 (+API) | 74 / 23 / 65 (+39) / 54 (+29) PASS, 0 FAIL |
Before the final fixes, early runs of the new checks failed on: invisible focus, Enter-to-submit, unnamed select, small toast button (all fixed above), and the offline-console-error noise and the self-signed-certificate service-worker limitation (test-setup issues). The M3/M4/M5/M6 browser regression suites ran *before* the last two tiny accessibility edits (toast live-region/button size, a `select` label); vitest, build, Atlas tests, axe and mobile emulation ran after them, and the M6 suite was not repeated after those two edits. M4 must run on a fresh database (counts are cumulative); the first M4 run in each session on a shared database fails for that reason only.

## 7. Remaining risks and limitations
1. **Atlas user is `readWriteAnyDatabase`** and the IP allow-list/tier/backup settings are unverified — must be fixed/confirmed by you before real use.
2. **Not tested on a real phone or in Safari/Firefox**; installation to a home screen and iOS service-worker behaviour are untested.
3. Frontend security headers can only be applied by the hosting platform; they were rehearsed with my own server, not the host you will pick.
4. In-memory rate limiters (login, registration, AI): one backend instance only; restarts reset them.
5. JWT in sessionStorage and no server-side revocation (≤60 min).
6. Workers can see every patient in the shared registry (by design); the audit log is writable by anyone with database-admin rights (no tamper-evidence).
7. No password recovery (the button is a placeholder `alert`), no MFA, no e-mail/SMS verification, no account lock beyond rate limits.
8. No legal/compliance review (consent, retention, data residency); no penetration test; no load test; no monitoring/alerting set-up.
9. CSP allows inline *styles* (the UI uses style attributes); scripts are strict.
10. 9 moderate/low advisories in the frontend build toolchain (not shipped).
11. AI remains verified only against the test double; no real Groq key was used.
12. Backup/restore was proven on disposable data only; no scheduled backup is configured.

## 8. Commands
Backend: `cd backend && .venv\Scripts\python -m pytest` (add `SAHAAY_ATLAS_TESTS=1` for Atlas). Frontend: `cd frontend && npx vitest run`; `npm run build` (needs `VITE_API_URL=https://…`) or `SAHAAY_LOCAL_BUILD=1 npm run build`. Rehearsal: see `e2e/phase_m7_production.mjs` header and `DEPLOYMENT_GUIDE.md`.
