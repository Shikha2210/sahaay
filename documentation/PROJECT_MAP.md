# PROJECT_MAP.md — SAHAAY repository map (audit date 2026-11-01)

Method: every tracked file was read in full. Nothing was executed (no install, no server start, no build). All findings are from code reading unless stated.

## 1. Where things live (important)

There are **three sibling folders** in `C:\Users\YOGA\Downloads\` that make up SAHAAY. Only the first is a git repository.

| Folder | What it is |
|---|---|
| `sahaay-v3-conflict-workflows/` (git repo, 1 commit `191ac0c`) | **Current backend + "integration bundle" for the frontend.** Contains the FastAPI/MongoDB backend and 4 replacement frontend files. **It is not a runnable frontend**: no `package.json`, `main.jsx`, `index.html`, `vite.config.js`, `styles.css`. |
| `sahaay-dark-dashboard-v2/sahaay-dark-dashboard/` (not in git) | **The actual Vite project** (React 18, Vite 6, Dexie 4, lucide-react, vite-plugin-pwa, Tailwind listed). Its `src/App.jsx` is the *original, fully simulated* UI (no API calls). `src/db.js` is the original v1 Dexie schema. `node_modules` is installed. |
| `sahaay-v3-full-integration/sahaay-v3/` (not in git) | Earlier copy of the backend bundle (older `main.py`/`db.py`; lacks `CONFLICT_WORKFLOWS.md`). Superseded by the git repo. |
| `sahaay-backend-v2/` (not in git) | Older SQLite/SQLAlchemy backend + a `venv`. Superseded. |
| `sahaay-backend (3)/` | Not inspected (older backup by name). |

The 4 files in `frontend-integration/` are meant to be copied over `src/` of the Vite project. That copy step has to be done and verified; the two codebases have not been merged or built together.

## 2. Tracked files in this repo (19)

```
sahaay-v3/
├─ .env.example                  backend env template (placeholders only)
├─ .gitignore                    .env, .venv/, __pycache__/, *.pyc, .pytest_cache/
├─ START_BACKEND_WINDOWS.ps1     venv + pip + uvicorn helper
├─ requirements.txt              fastapi, uvicorn, pymongo[srv], pydantic(-settings), python-jose, passlib+bcrypt, httpx, email-validator
├─ README.md / CONFLICT_WORKFLOWS.md / IMPLEMENTATION_STATUS.md   prior self-written docs (partly inaccurate, see BUGS_AND_GAPS.md)
├─ app/
│  ├─ config.py      pydantic-settings; env → Settings
│  ├─ db.py          MongoClient + indexes + counters
│  ├─ security.py    bcrypt hashing, HS256 JWT, current_user, require_roles
│  ├─ schemas.py     Pydantic models (Register, Login, Patient, Operation, Batch, Resolve)
│  └─ main.py        269 dense lines: ALL routes, bootstrap/seed, sync, conflicts, chatbot
└─ frontend-integration/
   ├─ App.jsx        244 lines, all UI (Login, Patient, Worker, Supervisor, IdentityWorker, Modal)
   ├─ api.js         fetch wrapper + auth calls, token in localStorage
   ├─ db.js          Dexie v1→v2 schema + seed()
   ├─ syncEngine.js  operation queue, push/pull, backoff, auto-sync
   ├─ INSTALL.md / .env.example
```

No tests, CI, Dockerfile, deployment config, service worker code, or manifest in this repo.

## 3. Architecture as actually built

```
Browser (React/Vite PWA-plugin configured in the separate project)
 ├─ App.jsx ── role-based screens (patient | worker | supervisor ; clinician has NO screen)
 ├─ Dexie "SAHAAY_DARK_LOCAL" v2: patients, operations(queue), conflicts, audit, sync_state(cursor)
 ├─ syncEngine.js: push POST /api/sync/batch (≤50 ops) → pull GET /api/sync/pull (cursor)
 └─ api.js: fetch + Bearer JWT (localStorage)
            │ HTTPS/JSON
FastAPI (app/main.py, single module)
 ├─ security.py: bcrypt, HS256 JWT (12h), DB lookup per request, require_roles()
 ├─ Sync: per-operation Mongo transaction (idempotency receipt, patient upsert, conflict, change feed, audit)
 ├─ Conflicts: evidence submit/review, resolve (transaction), patient link-account
 ├─ Chatbot → Groq (httpx, key only on server)
 └─ Mongo collections: users, patients, operations, conflicts, changes, counters, audit,
                       evidence, patient_confirmations, identity_attempts, chat_messages
MongoDB Atlas (replica set required for transactions)
```

## 4. Stack vs. expected stack

| Expected | Found | Note |
|---|---|---|
| React, Vite | ✔ (in separate folder) | |
| Tailwind CSS | ⚠ Listed as devDependency; **unused** (`styles.css` is hand-written CSS, no `@tailwind`, no config) | Document-only difference; keep hand CSS. |
| Lucide, Dexie, IndexedDB | ✔ | |
| PWA | ⚠ `vite-plugin-pwa` configured (autoUpdate, manifest, navigateFallback); not built/tested; icon is SVG only | |
| FastAPI, REST, JWT | ✔ | |
| MongoDB Atlas | ✔ code present; never run against a cluster | |
| Groq via backend | ✔ code present; needs `GROQ_API_KEY`; key not in frontend | |

## 5. Roles

`patient`, `worker`, `supervisor`, `clinician` exist in backend. Frontend renders screens for patient, worker, supervisor only.

## 6. Environment variables (backend)

`MONGODB_URI`, `MONGODB_DATABASE`, `JWT_SECRET`, `JWT_EXPIRE_MINUTES`, `CORS_ORIGINS`, `GROQ_API_KEY`, `GROQ_MODEL`, `BOOTSTRAP_SUPERVISOR_EMAIL`, `BOOTSTRAP_SUPERVISOR_PASSWORD`, `SEED_DEMO_DATA`. Frontend: `VITE_API_URL`. No `.env` files exist on disk in this repo (checked: file count equals tracked count).
