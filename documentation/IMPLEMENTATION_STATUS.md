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
| 1 | Consolidate repo (frontend + backend) | Not started |
| 2 | Backend correctness & security | Not started |
| 3 | Conflict engine | Not started |
| 4 | Offline sync rework | Not started |
| 5 | Frontend workflows (all 4 roles) | Not started |
| 6 | Security hardening | Not started |
| 7 | UI/UX polish | Not started |
| 8 | Automated tests + reports | Not started |
| 9 | Deployment prep | Not started |
| 10 | Hackathon material | Not started |
