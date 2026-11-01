# SAHAAY deployment guide

**Status: nothing has been deployed.** This guide describes how to deploy safely and what was rehearsed locally (see `MILESTONE_7_REPORT.md`). Do not deploy with the development `.env`; the backend now refuses to start in production mode with unsafe settings.

## 0. What you are deploying

| Piece | What it is | Where it can run |
|---|---|---|
| Frontend | Static files from `npm run build` (`frontend/dist`) | Any static HTTPS host (Netlify, Cloudflare Pages, Vercel, nginx, S3+CDN) |
| Backend | FastAPI app (`backend/app`), started with uvicorn | Any host that runs Python 3.11+ behind HTTPS (Render, Railway, Fly.io, a VM + nginx/Caddy) |
| Database | MongoDB Atlas | Atlas (TLS only) |
| AI (optional) | Groq API, called **only by the backend** | Needs `GROQ_API_KEY`; without it, AI features show "not configured" |

Front end and API should be on different **https** origins (for example `https://app.example.org` and `https://api.example.org`). Service workers and installation require HTTPS.

## 1. Before you start (checklist)
1. [ ] A real domain (or two sub-domains) with HTTPS certificates (the host usually provides them).
2. [ ] An Atlas **dedicated application user** with `readWrite` on the `sahaay` database only (see section 3). Do not use the admin/owner user or `readWriteAnyDatabase`.
3. [ ] Atlas **Network Access**: allow only your backend host's outbound IPs (never `0.0.0.0/0` for production). Many PaaS hosts have changing IPs; if so, use a static-IP add-on or accept `0.0.0.0/0` **only** together with a strong, scoped DB user and TLS, and record that as an accepted risk.
4. [ ] A strong `JWT_SECRET` and a strong supervisor password generated on your own machine (section 2).
5. [ ] A backup tested (section 5) **before** real data is entered.
6. [ ] A written decision on who is the data controller, retention, and consent. SAHAAY stores health information; the law that applies to you (for example India's DPDP Act, or HIPAA/GDPR elsewhere) is outside what this software can decide for you. **Legal/compliance review is required before real patient data is used.**

## 2. Backend configuration (environment variables on the host; never in git)

| Variable | Production value |
|---|---|
| `ENVIRONMENT` | `production` (turns on the strict checks below, hides `/docs`, `/redoc`, `/openapi.json`, adds HSTS) |
| `MONGODB_URI` | the Atlas `mongodb+srv://…` string of the **scoped application user** |
| `MONGODB_DATABASE` | `sahaay` (or a new name when restoring) |
| `JWT_SECRET` | 48+ random characters: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `JWT_EXPIRE_MINUTES` | `60` (default; 5–240 allowed) |
| `CORS_ORIGINS` | exactly your frontend origin, e.g. `https://app.example.org` (https only; no `*`, no localhost) |
| `BOOTSTRAP_SUPERVISOR_EMAIL` / `BOOTSTRAP_SUPERVISOR_PASSWORD` | a real address you control and a strong password; used **only** if no supervisor exists yet. Remove the password variable after first start. |
| `SEED_DEMO_DATA` | `false` (production refuses `true`) |
| `ALLOW_WORKER_SIGNUP` | **DEMO ONLY.** `true` lets anyone create a health-worker account (email + password) with no approval, which gives them read/write access to every patient record in the database. The backend refuses it unless `MONGODB_DATABASE` starts with `sahaay_demo`. Pair with `VITE_ALLOW_WORKER_SIGNUP=true` on the frontend build. Set both to `false` (or remove) before any real use. |
| `ALLOW_RECORD_CLAIM` | leave unset (`false`). `true` re-enables patients connecting themselves to an existing record with Patient ID + phone (weaker: anyone knowing both could take over that record; guesses are throttled and audited). |
| `SELF_SERVICE_PATIENT_RECORDS` | `true` (default): a patient who signs up immediately gets their **own new** record, no supervisor approval. It never gives access to a record staff already created (that still needs the supervisor's identity check). `false` restores supervisor-only linking; the M3/M4/M5 browser suites need `false` because their setup links accounts to pre-made records. |
| `TRUST_PROXY_HEADERS` | `true` **only** if the backend is behind a proxy you control that sets `X-Forwarded-For` (needed for correct per-address rate limits and audit IPs). Otherwise leave `false`. |
| `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_API_URL` | optional; URL must be https |
| `AI_RATE_PER_MINUTE`, `AI_GLOBAL_PER_MINUTE`, `AI_TIMEOUT_SECONDS` | defaults are sensible |

The backend **refuses to start** in production if: demo data is on, CORS contains non-https/localhost origins, the supervisor e-mail is an example domain, the Atlas URI has no TLS, the database name starts with `sahaay_test_`, or the AI URL is not https. A rejected secret is never printed in the error.

Install and run (example):
```
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="<proxy ip>"
```
Run **one** backend instance (or add a shared store first): the login/registration rate limiters and the AI rate limiter are in memory.

Health check URL: `GET /health` → `{"status":"ok","mongodb":"connected",...}`. It shows the database name; expose it only to your monitoring if that matters to you.

## 3. MongoDB Atlas
1. Create a database user (Database Access → Add user → *Built-in role* → **Specific privileges** → `readWrite` on database `sahaay`). Use a generated password.
2. Keep the existing owner/admin user for people, not for the app.
3. Network Access: restrict as in the checklist. Enable **TLS** (Atlas enforces it) and keep the server at the version Atlas manages.
4. On first start the backend creates indexes (unique emails, patients, operations, conflicts, linked accounts, audit indexes). Watch the first log for errors.
5. Turn on **Cloud Backup** if your tier supports it (M10+). The free **M0 tier has no automated backups**; use the scripts in section 5 on a schedule.
6. Turn on Atlas **alerts** (connections, failed auth) and **database auditing** if your tier offers it.
Review finding (current development setup): the application user has `readWriteAnyDatabase` — see the report; replace it before production.

## 4. Frontend
```
cd frontend
npm ci
set VITE_API_URL=https://api.example.org        (Windows)    # or: export VITE_API_URL=https://api.example.org
set VITE_SEED_DEMO_DATA=false
npm run build            # output: frontend/dist
```
`npm run build` **refuses** to build for production when `VITE_API_URL` is missing, not https, or points to localhost, or when demo data is on. (For a local rehearsal only: `SAHAAY_LOCAL_BUILD=1`.)

Upload `frontend/dist`. Required host behaviour:
* HTTPS only; redirect http → https.
* Single-page-app fallback: unknown paths return `index.html` (status 200).
* Cache: `/assets/*` immutable for a year; `index.html`, `sw.js`, `registerSW.js`, `manifest.webmanifest` must be **no-cache** (otherwise users keep old versions).
* Send these headers on every response (replace `API_ORIGIN`):
```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' API_ORIGIN; manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
Cross-Origin-Opener-Policy: same-origin
```
`'unsafe-inline'` for styles is needed because the UI uses inline style attributes; scripts are strictly `'self'`. This exact policy was rehearsed with the production build: no violations, no third-party requests (fonts are self-hosted).

## 5. Backup and recovery
Scripts (read-only against the live database; they never print the connection string):
```
cd backend
python scripts/backup_db.py --out D:\sahaay-backups                      # writes <db>-<timestamp>/ with *.jsonl.gz + manifest.json (SHA-256 + indexes)
python scripts/restore_db.py --backup D:\sahaay-backups\sahaay-<stamp> --into sahaay_restore_YYYYMMDD
```
* Backups contain **patient data and password hashes**: store them outside the repository (the script refuses to write inside it), encrypt the disk/folder, restrict access, and delete old copies per your retention policy.
* Restore only goes into a **new, empty** database name, verifies checksums first and document counts afterwards, and refuses the live name. To recover for real: restore into a new name → inspect → set `MONGODB_DATABASE` to the new name → restart the backend. Keep the old database until you are sure.
* Schedule backups (cron / Task Scheduler) at least daily; **do a restore drill** quarterly. Recovery point = time of last backup; recovery time = minutes for small data.
* Both scripts were exercised on disposable databases (identical data and unique indexes restored; live-name, non-empty-target, in-repo and tampered-file cases refused).

## 6. First start and smoke test
1. Start the backend; confirm `/health`, and that `/docs` returns 404.
2. Sign in as the bootstrap supervisor on the frontend. **Change nothing else yet**; remove `BOOTSTRAP_SUPERVISOR_PASSWORD` from the host configuration.
3. Provision staff accounts (supervisor → API `POST /api/admin/users`; there is no staff self-registration).
4. With a **synthetic** patient: register while offline, reconnect, confirm it syncs; open the audit history and confirm the sign-ins are listed.
5. On a phone: open the site, check the install prompt and an offline start (see report: not yet tested on a real device).

## 7. Rotating secrets / incident basics
* Rotate `JWT_SECRET` → all sessions end immediately (users sign in again; unsynced local changes are kept on their device).
* Suspected leaked DB password: change it in Atlas, update the host variable, restart.
* Disable an account: set `active:false` on the user document (the app checks it on every request).
* Review `GET /api/audit` (supervisor) for `LOGIN_FAILED` / `LOGIN_BLOCKED` bursts.

## 8. Not covered (needs your decision)
Hosting choice and cost, domain/DNS, monitoring/uptime alerts, log retention, multi-instance rate limiting (needs Redis or similar), SMS/e-mail password recovery (the "Forgot password" button is still a placeholder), legal review. See `MILESTONE_7_REPORT.md` → remaining risks.
