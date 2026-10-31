# SAHAAY v3 — MongoDB Atlas + Offline Synchronization Integration

This package replaces the earlier SQLite/SQLAlchemy backend with a MongoDB Atlas backend and provides frontend integration files designed around the supplied SAHAAY `App.jsx` and Dexie schema.

## Important status statement

This is a substantially more complete hackathon implementation, **not a production-certified healthcare system**. It has been syntax-compiled locally, but it has not been run against your personal Atlas cluster or browser environment because your credentials and machine are not accessible here. You must run the verification steps below. Do not use real patient or Aadhaar data.

## Architecture

- MongoDB Atlas: central/global storage.
- Dexie / IndexedDB: device-local patient cache, persistent operation queue and global pull cursor.
- FastAPI: only network interface to MongoDB.
- Push: batches of up to 50 operations; each has a UUID, device ID and base patient version.
- Idempotency: unique MongoDB index on `operation_id` and stored response.
- Conflict detection: field-level revisions; changes to distinct fields can merge, competing updates to the same field create a conflict.
- Pull: globally sequenced change feed; client stores `global_cursor` in Dexie and re-applies changes idempotently.
- Retry: exponential backoff; pending operations are retained until server acknowledgement.

## Backend setup (Windows PowerShell)

1. Extract this folder.
2. Open the `sahaay-v3` folder in VS Code.
3. Create and activate a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, do not change system-wide execution policy. Run commands without activation:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

4. Copy `.env.example` to `.env`.
5. Put your Atlas URI in `MONGODB_URI`. Replace the username/password placeholders. URL-encode special characters in the password. Set a long random `JWT_SECRET`.
6. Keep `.env` private; `.gitignore` excludes it.
7. Install and run:

```powershell
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

8. Visit `http://127.0.0.1:8000/health`. It must report `"mongodb":"connected"`.
9. API docs: `http://127.0.0.1:8000/docs`.

### Atlas network settings

- Add your current laptop IP under Network Access.
- Ensure the database user has read/write access to the SAHAAY database.
- If your IP changes, update the Atlas IP access list.
- Do not use `0.0.0.0/0` for normal use.

### Bootstrap supervisor

The first startup creates a supervisor using `BOOTSTRAP_SUPERVISOR_EMAIL` and `BOOTSTRAP_SUPERVISOR_PASSWORD` in `.env`. Change the example password before running. After login, provision worker and clinician accounts through `POST /api/admin/users` in `/docs` using the supervisor bearer token. Public registration can create patient accounts only. The integrated login screen includes a patient account creation action; worker, supervisor and clinician accounts must be provisioned by a supervisor.

## Frontend integration

Copy these files from `frontend-integration/` into your existing frontend `src/` folder, replacing `App.jsx` and `db.js` and adding `api.js` and `syncEngine.js`:

- `App.jsx`
- `db.js`
- `api.js`
- `syncEngine.js`

Create frontend `.env` (usually in the Vite project root, not `src/`):

```env
VITE_API_URL=http://127.0.0.1:8000
```

Restart Vite after changing `.env`:

```powershell
npm run dev
```

Install `dexie` and `lucide-react` if they are not already installed.

**Dexie migration:** `db.js` adds database version 2 and a `sync_state` table. It preserves existing version-1 patient, operation, conflict and audit records. Do not delete browser site data.

## Core API endpoints

- `GET /health`
- `POST /api/auth/register` (patient accounts only)
- `POST /api/auth/login`
- `GET /api/auth/me`
- `POST /api/admin/users` (supervisor creates worker/clinician/supervisor)
- `GET/POST /api/patients`
- `GET /api/patients/{patient_id}`
- `POST /api/sync/batch`
- `GET /api/sync/pull?since_seq=0&limit=200`
- `GET /api/conflicts?status=pending`
- `POST /api/conflicts/{conflict_id}/resolve`
- `POST /api/identity/{patient_id}/attempt`
- `GET /api/audit`
- `GET /api/dashboard/summary`
- `POST /api/chatbot/message`

## Critical design constraints / known incomplete areas

1. **Document-verifiable conflicts:** the API requires an evidence reference, but does not independently authenticate documents, validate digital signatures, or establish credential ownership. A trusted verifier process still needs to be integrated.
2. **Patient-verifiable conflicts:** the API requires a patient-confirmation reference. Secure patient-to-record linking and confirmation UI are not implemented; the endpoint deliberately returns 501 rather than pretending confirmation exists.
3. **Clinical conflicts:** only the `clinician` role can resolve them. A clinician account must be provisioned by a supervisor. This demo role is not a substitute for real clinical credential verification.
4. **Patient access:** public patient registration exists, but linking a patient account to a patient record is intentionally not implemented. Patient dashboard still contains some synthetic static content.
5. **Chatbot:** requires `GROQ_API_KEY`. It provides general education only and must not receive identifiable health data. Chat history is stored but no history retrieval UI is provided yet.
6. **Identity:** QR decoding and XML parsing in the frontend are previews only. The backend stores a minimal pending attempt; it does not perform UIDAI/Aadhaar verification.
7. **Conflict demo records:** synthetic sample conflicts are seeded for dashboard walkthroughs. They are not produced by the sync engine and must be clearly labelled as synthetic. Do not resolve them as if they were real patient conflicts.
8. **MongoDB transactions:** sync batch processing uses MongoDB transactions for atomic operation receipt, patient update, conflict creation, change log and audit. If Atlas rejects transactions, do not disable transaction handling silently; check the cluster deployment/topology and fix configuration.
9. **Sync semantics:** each operation currently carries a single `base_version` (patient revision) and field revisions are compared against it. This is a practical field-level optimistic-concurrency scheme, not a full vector-clock/CRDT implementation.
10. **Patient record authorization:** healthcare worker/supervisor/clinician roles can read the patient registry in this hackathon prototype. Add assignment/consent-based patient-level authorization before any real deployment.
11. **Conflict resolution UI:** the supplied App.jsx uses browser prompts for review references. Replace with a proper accessible review modal before final presentation. Backend enforces required references and roles.
12. **Testing:** local Python compile check passed during packaging. Atlas connection, browser integration, multi-device tests and end-to-end tests remain to be run on your machine.

## Essential end-to-end test

1. Create a worker and clinician account via supervisor.
2. Log in as worker on browser/device A and create a patient while offline.
3. Reconnect and click Sync now. Confirm patient appears in Atlas `patients` and operation receipt in `operations`.
4. Use a second browser profile/device. Pull the patient record.
5. Create two operations against the same `base_version`, changing different fields. Both should apply.
6. Create two operations against the same `base_version`, changing the same field to different values. The later operation should return a conflict.
7. Confirm `CRITICAL_CLINICAL` conflict resolution is rejected for supervisor and allowed only for clinician.
8. Re-submit the same `operation_id`; response must be `duplicate` and no second update should occur.
9. Stop network during sync; pending operations must remain and retry after reconnection.

## Run commands

Backend:

```powershell
uvicorn app.main:app --reload
```

Frontend (from your Vite project root):

```powershell
npm run dev
```
