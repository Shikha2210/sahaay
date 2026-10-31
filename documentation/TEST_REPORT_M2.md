# TEST_REPORT_M2.md — Milestone 2 (security & auth), 2026-11-01. Actual results only.

Commands: `backend: .\.venv\Scripts\python.exe -m pytest -q` · `frontend: npm test` · `npm run build` · `npm audit`.

## Totals
| Suite | Passed | Failed | Expected-failure (open bug reproductions) | Notes |
|---|---|---|---|---|
| Backend pytest | **59** | 0 | 4 (S1, C1, C2, S4 — unchanged from M1) | 0.15 s–7 s |
| Frontend vitest | **24** | 0 | 2 (S2, S3 — unchanged from M1) | |
| Frontend production build | **passed** (1585 modules, PWA service worker generated) | | | |
| `npm audit` | 9 remaining (2 low, 7 moderate), build-time only | | | was 15 incl. 2 critical + 1 high; see DEPENDENCY_AUDIT.md |

## Requested test cases
Legend: ✅ passed · 🟡 passed with a fake (what was faked is stated) · ⛔ blocked (needs Atlas) · ❌ not performed.

| # | Requested case | Result | Where / what was faked |
|---|---|---|---|
| 1 | Missing JWT_SECRET | ✅ | test_security_config — refuses to start |
| 2 | Weak JWT_SECRET (short, placeholder, low variety, guessable) | ✅ | 6 parametrised cases + strong secret accepted |
| 3 | Missing bootstrap supervisor password | 🟡 | `bootstrap()` raises RuntimeError when no supervisor exists; **fake collection**, not Mongo |
| 4 | Demo seeding disabled by default | ✅ / 🟡 | setting defaults to False (real); `bootstrap()` seeds nothing (fake collection) |
| 5 | Invalid login (unknown user, wrong password, generic message, rate limit) | 🟡 | **fake users lookup**; bcrypt verification is real |
| 6 | Expired JWT | 🟡 | real JWT decode, `token_expired` code; users lookup faked |
| 7 | Unauthorized API requests (no token, garbage token, wrong secret, alg=none, no `exp`, deactivated user, wrong role) | ✅ / 🟡 | no-token cases fully real (rejected before any DB); others use fake users lookup |
| 8 | Logout with no pending operations | ✅ | frontend session.test.js (fake-indexeddb) |
| 9 | Logout with pending operations (count shown, refused without discard, discard wipes) | ✅ | same |
| 10 | User A logs out, User B logs in on the same device | ✅ | same; plus blocked/discard variants |
| 11 | A's queued operations not synced under B | ✅ | fetch test double; also **mutation-checked** (removing the owner filter makes 2 tests fail) |
| 12 | Session restoration and expiry | ✅ | valid / expired / tab-closed / 401 mid-session / failed login ≠ expiry |
| — | Token in sessionStorage, not localStorage; legacy keys purged | ✅ | |
| — | Input validation: identity extra fields (raw QR) rejected, chat blank/oversize, sync invalid values/ids rejected per-operation | ✅ | real endpoints, no DB reached |
| — | Security headers, CORS rejects unlisted origin | ✅ | |
| — | Regression: default bootstrap email was unusable at login | ✅ | new bug found while writing tests (see BUGS_AND_GAPS X13) |

## Blocked — require a real MongoDB Atlas deployment (NOT tested, NOT claimed)
Transactions; idempotent replay; concurrent resolution; unique indexes; real bootstrap against Atlas; real login/register persistence; `/health` with `mongodb: connected`; rate-limit behaviour across restarts; audit persistence; patient-linking; end-to-end login → sync → conflict flow.

## Not performed
* Manual browser walk-through of the new UI (logout dialog, switch-user dialog, expiry banner, login notice). The code compiles and builds, and the logic behind it is tested, but **the React components themselves have no automated tests and were not exercised in a browser**.
* Playwright/e2e; two-device sync; PWA offline install.
* `pip-audit` (Python dependency scan).
* Penetration/XSS testing beyond the static code scan.

## Test-quality notes
* `it.fails`/`xfail(strict)` reproductions were re-verified to fail for the documented reasons after the changes (S2/S3 messages unchanged).
* Fakes used: `tests/fakes.py` (users lookup), a fake collection for `bootstrap()`, fake-indexeddb, `fetch` test doubles. None of these is evidence about MongoDB or the real network.
