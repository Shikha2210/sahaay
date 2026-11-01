# Milestone 6 – Offline reliability and product hardening

Branch `milestone-6-offline-hardening`. This was an integration / bug-fixing milestone: existing screens, APIs, Dexie database, sync engine and design were reused. M3 conflict policies and M4/M5 behaviour are preserved (their full suites were re-run, see below).

**Honesty notes**
* Browser tests ran in **Microsoft Edge only** (Playwright), headless, against the **production build** served by `vite preview`, the real FastAPI backend and **real MongoDB Atlas**, in disposable `sahaay_test_*` databases with synthetic data. Those databases were emptied afterwards (the Atlas user cannot `dropDatabase`, so collections are dropped).
* The "two devices" test uses two separate browser profiles (separate IndexedDB / device id) on one machine, not two physical devices.
* There is **no real Groq key**; AI suites use the labelled test double `e2e/fake_groq.py` (unchanged from M5).
* No snapshot of the real `sahaay` database was taken *before* M6. Every M6 run asserted the disposable database name (via `/health` or a random `sahaay_test_` name) before writing, and no M6 step was pointed at `sahaay`. Counts now: audit 76, changes 58, conflicts 8, evidence 3, operations 61, patients 30, users 6. These are recorded for future comparison; they are not a proven before/after diff.

## Results by priority

| # | Priority | Result | Evidence |
|---|---|---|---|
| 1 | Rejected operations never show green | **PASS** | Sync centre uses a red badge ("Rejected by the server"); a real server `patient_not_found` rejection showed red and no green badge on that row (`phase_m6_hardening`). Unit tests in `syncEngine.hardening.test.js`. |
| 2 | Failed-operations list with retry and details | **PASS** | "Needs attention" panel: reason text, attempts, Retry, Retry all, Discard with confirmation ("Keep it" leaves it; confirming removes it and records `OPERATION_DISCARDED` in the local audit trail). Waiting changes can never be discarded. |
| 3 | Offline behaviour of existing screens | **PASS (with gaps below)** | Patient, worker conflicts, clinician queue/history/conflict list, supervisor accounts and sync monitoring show the last saved copy with a "saved on this device" notice (new Dexie `cache` table, per user, wiped on sign-out). Server-only actions (confirm, evidence, link, decisions) are disabled with an explanation instead of failing silently. Workers can now edit patients offline (new Edit dialog, queued with `base_version`). |
| 4 | PWA install and offline startup | **PASS** | Manifest valid; service worker active and controlling; Edge reports **no installability errors** in a normal (non-incognito) profile; with the network off, the sign-in screen and a signed-in session reload from cache; deep links fall back to the app shell. |
| 5 | No simultaneous manual + automatic sync | **PASS** | `synchronize()` is guarded by a Web Lock (`sahaay-sync`, `ifAvailable`) with an in-tab fallback. Two tabs clicking Sync at the same instant delivered the operation **exactly once** (server `operations_total` +1, one patient copy). Idempotent `operation_id` remains the second line of defence. |
| 6 | Audit history and login audit | **PASS** | New: `LOGIN_SUCCESS`, `LOGIN_FAILED` (reason, hashed account, IP – never the password or typed e-mail), `LOGIN_BLOCKED` (once per window so it cannot flood the log), `LOGOUT` (`POST /api/auth/logout`, best effort from the client), `ACCOUNT_REGISTERED`. `GET /api/audit` is filterable (activity group, role, patient, dates), paginated, shows actor names; security events are visible to supervisors only; workers get 403. UI: Audit history for supervisor and clinician. Audit write failures never break sign-in. |
| 7 | Mobile responsiveness and accessibility | **PASS (automated scope)** | 375 px: no horizontal scrolling on login, dashboard, directory; drawer menu works. axe-core (WCAG 2.0/2.1 A/AA): no critical/serious violations on login, worker overview, directory (mobile), Sync centre, register dialog, supervisor audit history. Shared `Modal` is now a real dialog (`role=dialog`, `aria-modal`, labelled, focus moved in, Tab trapped, Escape closes, focus restored). Icon-only buttons have names. Sign-out button enlarged to ≥32 px (a real finding: it was smaller than 24 px). |
| 8 | Cross-tab and multi-device sync | **PASS** | Cross-tab: a registration in tab 1 appears as "Not synced yet" in tab 2 without reload (BroadcastChannel) and clears after tab 1 syncs. Two devices editing one phone offline: first is applied, second is `acknowledged_with_conflict` (not overwritten, not green), server keeps the first value, a pending conflict exists. Unsynced local edits are protected from being overwritten by a server refresh (overlay merge). |

## Test runs

| Suite | Result |
|---|---|
| Backend unit/contract (in-memory stand-in – logic only) | 229 passed, 28 skipped (Atlas opt-in) |
| **Real Atlas integration** (`SAHAAY_ATLAS_TESTS=1`, random `sahaay_test_*` DB) | **28 passed** (25 earlier + 3 new: login/logout/failed audit + filters/pagination, role hiding of security events, sync summary) |
| Frontend (vitest, fake-indexeddb) | 80 passed (52 earlier + 28 new hardening tests) |
| Production build | passes |
| `phase_m6_hardening.mjs` (real browser) | **74 PASS, 0 FAIL** (+2 informational) |
| M3 conflict UI browser suite | 23 PASS, 0 FAIL |
| M4 workspaces browser suite / API checks | 65 PASS / 39 PASS, 0 FAIL |
| M5 AI browser suite (test double) / API checks / unconfigured | 54 PASS / 29 PASS / 10 PASS, 0 FAIL |

Test-expectation changes (not product regressions): M4 clinician menu now lists "Audit history"; M5 explanation-button check now waits instead of using a fixed 1.5 s sleep. During development the first M6 browser run had 9 failures; 8 were wrong test assumptions (status names, tab on the wrong page, device id read too early, incognito installability) and **2 were real product gaps that were fixed**: the clinician conflict queue lacked an offline notice, and the sign-out button was below minimum target size. M4/M5 reruns on shared databases also showed that suites must each run on a fresh database (counts are cumulative).

## Remaining gaps (not done in M6)
* **Browsers/devices:** only Edge was tested. No Safari/Firefox, no real phones, no real install-to-home-screen. The manifest has only an SVG icon; Edge accepts it, but iOS and some Android launchers want PNG icons (192/512 and apple-touch-icon).
* **Accessibility:** automated axe checks cover only the screens listed; no manual screen-reader, keyboard-only walkthrough of every screen, colour-contrast review beyond axe, or reduced-motion checks. Moderate/minor axe findings were not triaged.
* **Offline scope:** nothing can be *signed in* offline (by design); decisions, evidence, linking, patient confirmations and AI need a connection and say so. The read cache has no size limit or expiry policy and is only as fresh as the last online visit.
* **Rejected operations** are only removable by the device owner; the supervisor sees counts but not another device's rejected list.
* **Retry semantics:** a rejected `create` blocks later edits for that patient until retried or discarded (intentional, documented in the UI).
* **Login rate limiter** is still in-memory (resets on restart, not shared across server instances); the audit log has no retention/archival policy.
* **Logout audit** is best effort (not recorded if signing out offline).
* **AI** still verified only against the test double; real Groq quality and language behaviour are untested.
* Real `sahaay` DB had no pre-M6 snapshot (see above).
* Deployment, HTTPS hosting and production hardening were deliberately not started.

## How to re-run
Disposable DB: start backend with `MONGODB_DATABASE=sahaay_test_<hex>` (plus `GROQ_API_KEY=test-double-key`, `GROQ_API_URL=http://127.0.0.1:8765/openai/v1/chat/completions` and `e2e/fake_groq.py` for AI suites); `npm run build && npx vite preview --port 5173`; in `e2e` set `E2E_DATABASE` / `E2E_STATE`, run `m5_setup.py` then `node phase_m6_hardening.mjs`. Run each older suite on a **fresh** database.
