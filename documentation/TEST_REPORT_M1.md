# TEST_REPORT_M1.md — Milestone 1 (2026-11-01). Actual results only.

Environment: Windows 11, Node v24.22.1 / npm 11.19.1, Python 3.11.9. No MongoDB available locally (no `mongod`, no Docker). No Atlas cluster configured yet. No Groq key.

## Build / startup checks
| Check | Result |
|---|---|
| `npm ci` (frontend, from preserved lockfile) | **Passed** (npm reports 15 vulnerabilities incl. dev tools: 2 low, 10 moderate, 1 high, 2 critical; production deps only: 9 = 2 low, 7 moderate. Not fixed yet.) |
| `npm run build` (Vite 6 + PWA plugin, integrated App.jsx/api.js/db.js/syncEngine.js) | **Passed** — 1584 modules, JS 318 kB (99 kB gzip), service worker `sw.js` + manifest generated, 6 precache entries |
| `vite` dev server serves `/`, `/src/App.jsx`, `/src/syncEngine.js` | **Passed** (HTTP 200, integration files present) |
| Backend `pip install -r requirements.txt` (Python 3.11) | **Passed** |
| Backend start with no `.env` | **Fails fast** with "MONGODB_URI / JWT_SECRET field required" (expected) |
| Backend start with unreachable Mongo | **Fails fast** after 10 s `ServerSelectionTimeoutError`, "Application startup failed" (expected) |
| Backend start against Atlas, `/health` | **Not performed** — needs Atlas (blocker) |
| Browser run of the UI, PWA install, offline behaviour | **Not performed** in M1 |

## Reproduction tests for the six critical bugs
`xfail`/`it.fails` = test asserts the correct behaviour and currently fails *for the documented reason* (I re-ran each without the marker and read the failure message).

| Bug | Test | Method | Result |
|---|---|---|---|
| S1 create rejected | `backend/tests/…::test_S1…` | Real FastAPI endpoint via TestClient, auth dependency overridden; payload keys parsed from the real `App.jsx`. Rejection happens before any DB access. | **Reproduced**: `{'status':'rejected','reason':'unsupported_fields','fields':['identity_status']}` |
| C1 no patient-confirmation route | `…::test_C1…` | Inspect registered routes | **Reproduced**: no route |
| C2 resolve button always 400 | `…::test_C2…` | Real endpoint; resolution string parsed from `App.jsx` | **Reproduced**: `'Reviewed in SAHAAY supervisor interface' -> 400 "resolution must be accept_a, accept_b or custom"` |
| S4 failed commit reported as applied | `…::test_S4…` | **Simulation**: fake pymongo session whose commit raises. Shows the control flow, **not** a real Mongo transaction. | **Reproduced** (simulated): results `['applied','retryable_error']` |
| S2 random send order | `frontend/src/syncEngine.reproduce.test.js` | fake-indexeddb + `fetch` test double; 20 ops with real random UUIDs | **Reproduced**: sent order ≠ creation order |
| S3 stale base_version | same | queue two edits for one patient | **Reproduced**: second `base_version` (1) not greater than first (1). *The server-side consequence (false self-conflict) is by code reading only; needs Atlas to confirm.* |

Totals — backend: 4 xfailed, 0 passed, 0 failed. Frontend: 3 passed (2 expected-failure reproductions + 1 control that the queue persists unique pending ops).

## Not performed / blocked
- Anything requiring a real database: transactions, idempotency, concurrency, conflict resolution, auth flows end-to-end, audit persistence → **needs Atlas M0**.
- Chatbot → **needs Groq key** (later milestone).
- Two-browser/device sync, crash recovery, PWA offline → later milestones.
- A real-Mongo version of S3 (false self-conflict) and S4 (commit failure) → needs Atlas.
