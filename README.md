# SAHAAY — offline-first community healthcare sync

> **Status: development prototype. Not for real patient data. Do not expose publicly yet** (known issues listed in `documentation/BUGS_AND_GAPS.md`).

```
frontend/       React + Vite PWA + Dexie (IndexedDB)     -> npm
backend/        FastAPI + MongoDB Atlas + JWT            -> Python 3.11
documentation/  audit, API docs, roadmap, status
```

## Run the frontend (PowerShell)
```powershell
cd frontend
npm ci                 # first time only
copy .env.example .env # VITE_API_URL=http://127.0.0.1:8000
npm run dev            # http://localhost:5173
npm run build          # production build
npm test               # reproduction tests (currently expected-failures, see below)
```

## Run the backend (PowerShell)
Needs a MongoDB Atlas cluster (free M0 is fine). Copy `backend\.env.example` to `backend\.env` and fill in `MONGODB_URI`, `JWT_SECRET` (generate one, command is in the file) and your own `BOOTSTRAP_SUPERVISOR_PASSWORD`.
```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# check: http://127.0.0.1:8000/health  and  http://127.0.0.1:8000/docs
.\.venv\Scripts\python.exe -m pytest -v -rxX    # tests
```
Never commit `.env`. Do not use real patient or Aadhaar data.

## Documentation
`documentation/` : PROJECT_MAP, IMPLEMENTATION_STATUS, BUGS_AND_GAPS, API_DOCUMENTATION, DEVELOPMENT_ROADMAP, AUTH_OPTIONS, CONFLICT_WORKFLOWS, TEST_REPORT_M1, and `original-frontend-simulated/` (your original UI files, kept for reference).
