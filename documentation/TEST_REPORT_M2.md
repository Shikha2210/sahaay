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

---
# Milestone 2b addendum — sync fixes (S1-S4)

## Results (this machine, no Atlas connection)
| Suite | Passed | Failed | Skipped | Expected-failure |
|---|---|---|---|---|
| Backend pytest | **79** | 0 | **10** (Atlas, opt-in) | 2 (C1, C2 — unrelated open bugs) |
| Frontend vitest | **40** | 0 | 0 | 0 |
| Frontend production build | **passed** | | | |

## What each kind of test proves
| Kind | Tests | Proves | Does NOT prove |
|---|---|---|---|
| **Pure logic** | `detect_field_conflicts` (5 tests) | conflict rules, incl. other-device write = conflict | anything about Mongo |
| **In-memory STAND-IN for MongoDB** (`tests/fakes.py: MemMongo`) | `tests/test_sync_fixes.py` (S1 x4, S2 x4, S3 x4 + merge, S4 x3, idempotency x2) | our sync LOGIC: ordering, blocking, receipts, one-result-per-op, rollback *as a snapshot restore* | real transactions, isolation, rollback, indexes, concurrency |
| Real endpoints, no DB reached | validation/auth tests | request handling | persistence |
| Client with fake-indexeddb + `fetch` double | `syncEngine.regression.test.js` (17) | queue ordering, per-patient blocking, base_version, retry, lost-ack resend | the real server |
| **REAL Atlas** | `tests/test_atlas_integration.py` (10 tests) | **BLOCKED: written, never run** | — |

Mutation checks (code deliberately broken, tests must fail, code restored): removing the queue sort -> 3 failures; hard-coding base_version -> 3 failures; making every field a conflict (writer logic) -> 2 failures.

## Real-Atlas tests: superseded - see "Real Atlas results" below (originally BLOCKED)
Written and ready, **not executed, so no claim is made**: connection+ping+replica-set, patient persistence, unique indexes, idempotent replay, transaction commit atomicity, **real rollback via injected failure**, clinical conflict recorded (no overwrite), **concurrent conflict resolution** (two threads), supervisor denied on clinical conflict, audit persistence. They run only against a throwaway `sahaay_test_<random>` database that is dropped afterwards; the real `sahaay` database is never used. Run: `$env:SAHAAY_ATLAS_TESTS='1'; .\.venv\Scripts\python.exe -m pytest tests/test_atlas_integration.py -v -rs`.

---
# Real Atlas results (2026-11-01) — REAL MongoDB Atlas, not a simulation

Cluster: Atlas replica set `atlas-in1nsb-shard-0` (transactions supported). Test database: disposable `sahaay_test_<random>`.

## Connectivity
* An earlier intermittent failure (`SSL handshake failed: TLSV1_ALERT_INTERNAL_ERROR`) was diagnosed as **Atlas Network Access**: the machine's outgoing connections alternated between two public IPs and only some were allowed. DNS, TCP, TLS certificate verification (Let's Encrypt, no interception), OpenSSL 3.0.13 and pymongo 4.13.0 were all fine. TLS verification was never disabled. After both IPs were added: `scripts/check_atlas.py` **5 of 5 PASS**.

## Integration tests: `tests/test_atlas_integration.py` — 10 passed, 0 failed
| Test | Result | What it proves (real Atlas) |
|---|---|---|
| connection, ping, replica set | PASSED | ping ok; replica set present |
| patient persists | PASSED | create op stored with identity_status=UNVERIFIED, version 1, field writers |
| unique indexes exist | PASSED | `operation_id` and `patient_id` unique indexes created |
| idempotent replay | PASSED | same operation twice -> `applied` then `duplicate`; 1 receipt, 1 audit row, version bumped once |
| transaction commit | PASSED | patient + receipt + change feed + audit all written |
| **transaction rollback** | PASSED | failure injected at the last write: MongoDB left no patient, receipt or change; same operation then succeeded once |
| clinical conflict | PASSED | competing write from another device -> conflict recorded, original value kept (no last-write-wins) |
| **concurrent conflict resolution** | PASSED | two simultaneous resolves: exactly one 200; conflict resolved once, patient version +1, one CONFLICT_RESOLVED audit |
| supervisor denied on clinical conflict | PASSED | 403, conflict stays pending |
| audit persistence | PASSED | actor id, role and timestamp stored |

Caveats: authentication was bypassed in these tests (a fake signed-in user is injected); the real HTTP login flow against Atlas, the pull feed, the bootstrap supervisor creation and the browser UI are **not yet tested against Atlas**. The loser of the concurrent-resolve race was only required to fail safely (404/409/503); its exact code was not recorded.

## Safety verification
Read-only snapshots (collection names + counts) of the real `sahaay` database before and after the test runs were **identical** (database does not exist yet; the cluster holds only Atlas's `sample_mflix`). A first run left its throwaway database behind because the Atlas user lacks the `dropDatabase` privilege; the fixture now falls back to dropping the throwaway database's collections, and that one leftover (8 test patients, ids `SH-T…`) was removed after a name-pattern and content check. No `sahaay_test_*` database remains.

## Other suites at this point
Backend 79 passed / 10 skipped (Atlas, opt-in) / 2 expected failures (C1, C2); frontend 40 passed.
