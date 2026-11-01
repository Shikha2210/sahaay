# MILESTONE_5_REPORT.md — AI integration completed (2026-11-02)

Branch `milestone-5-ai-integration` (from `milestone-4-role-workspaces`). Read first: `MILESTONE_4_REPORT.md`.
This was an **integration** milestone: the existing chat page, backend endpoint, roles, M3 conflict policy and M4 workspaces were reused. No M6 work.

## ⚠ The most important thing to know
**No Groq API key exists in this project (`backend/.env` has none), so the real Groq service was never contacted.** Every AI test used a **clearly labelled local test double of the external service** (`e2e/fake_groq.py`; every answer begins "TEST-DOUBLE") or simulated HTTP responses. The test double records exactly what the backend sends, which is how privacy was verified. What this proves: our code, roles, redaction, rate limits, auditing, guards, error handling and UI. What it does **not** prove: how the real model answers, answer quality (especially in the 10 non-English languages), Groq's actual error payloads/latency, or that the output guard catches real model wording. **Before relying on any of this, add the key and run `backend\.venv\Scripts\python.exe backend\scripts\check_groq.py [language]`** (sends one generic question with no patient data; prints only PASS/FAIL).

## 1. Results at a glance
| Check | Result |
|---|---|
| Backend tests (default run) | **216 passed**, 0 failed, 25 skipped (the opt-in real-Atlas suite) |
| Real MongoDB Atlas integration tests (disposable databases) | **25 passed** (3 new for AI); external AI service = test double |
| Frontend unit tests | **52 passed** (12 new for the AI client) |
| Frontend production build | **passed** (1594 modules, PWA service worker generated) |
| Browser (Edge), real backend + Atlas (disposable DB), AI test double | **54 / 54** checks (all four roles) + **10 / 10** for the "no key configured" state |
| Backend authorization/privacy over real HTTP (UI bypassed) | **29 / 29** |
| Regression: M4 browser suite | **65 / 65** (one earlier run missed 1 check — a timing flake, see §6) · M4 HTTP checks **39 / 39** |
| Regression: M3 browser suite | **23 / 23** (after fixing a real regression M5 caused, see §6) |
| Backend logs across all browser/API runs | 8 logs: 0 tracebacks, 0 HTTP 500, no tokens / connection strings / API-key text |
| Real `sahaay` database | **untouched** (counts identical before/after); 7 disposable databases removed |
| `npm audit` | unchanged (9: 2 low, 7 moderate, build-time only) |

## 2. What existed before (inspection)
* **Backend:** one `/api/chatbot/message` endpoint, Groq URL hard-coded, one prompt, no identifier removal, no rate limit, generic errors, and it **stored every question and answer** in the database (privacy gap AI2).
* **Frontend:** the chat page already called that endpoint, but its "Assistant available" badge was static, there was no loading/offline handling, the quick prompts invited personal medical questions ("explain my blood pressure reading"), and a dead scripted-reply function (`aiReply`) was left over.

## 3. What was built, against your requirements
| # | Requirement | Implementation |
|---|---|---|
| 1–2 | Inspect; connect the existing UI to the real API | New `backend/app/ai.py` (privacy/safety layer + Groq client), endpoints in `main.py` on the existing role system; chat page rebuilt as `AiAssistant.jsx` in the **same layout and styles**; `GET /api/ai/status` drives the badge (real availability, not hard-coded). Dead scripted replies removed. |
| 3 | Healthcare information + **multilingual** | General-information assistant with a safety prompt; **12 language options** (auto, English, Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Urdu); choice remembered on the device. The language instruction reaches the model (verified); **reply quality per language is unverified** (no real model). |
| 4 | Authorized, privacy-conscious **record summaries** | `POST /api/ai/patient-summary`: patients → **own record only**; clinicians → a record they name; workers/supervisors refused. Explicit consent required (server refuses without it). Only an **age range + clinical details** are sent — never name, phone, village, patient ID, identity status or exact age; numbers hidden in free text are removed. The sharing is **audited before anything is sent** (if the audit write fails, nothing is sent). The UI lists exactly which details were shared. |
| 5 | **Explain conflicts** without deciding | `POST /api/ai/conflict-explanation`: visibility follows the existing roles (patient: own patient-verifiable conflicts; worker: their own; supervisor/clinician: any). The model is given **no record values and no identifiers** — only the shape of the situation (what kind of detail, who decides, status, what this reader may do — computed by the server from the M3 policy). It has no tools and the AI module cannot reach the database; a test proves nothing changed after explanations. Buttons appear in the conflict dialog, worker cards and the patient's confirmation cards. |
| 6 | Loading, error, rate-limit, offline | "Thinking…" indicator + disabled input while waiting; structured errors for not-configured (503), rate limits (429 with `Retry-After`, per-user and shared-key limits, plus passing through Groq's own 429), timeouts (504), upstream faults (502); a "Try again" button where it helps; the client never contacts the network when the browser is offline. |
| 7 | Key only on the backend | Verified: no frontend source or **built bundle** contains the key name, `gsk_` or the provider address (tests); the key never appears in any response body/header (tests + 36 inspected HTTP responses); the browser never contacted the AI service directly. |
| 8–9 | Role-based data access; nothing unnecessary sent (no Aadhaar/credentials) | See §4. |
| 10 | No invented facts; never a diagnosis | Prompts require "use only these facts / say if unsure / never diagnose / never change medicines"; temperature 0.2; summaries restricted to listed facts; a **server-side guard replaces** answers that read like a diagnosis or a dosing instruction; every AI text is labelled "AI-generated … not medical advice or a diagnosis"; emergencies get a fixed non-AI message. **This reduces the risk; it cannot guarantee a model never states something wrong** (see §7). |

## 4. What is sent to the external AI service, and what is not
| Feature | Sent | Never sent |
|---|---|---|
| Chat | The user's question and last ≤6 turns, **after** removal of Aadhaar/VID-like numbers, phone numbers, emails, credentials/tokens, "OTP/password …" phrases, patient IDs and other long numbers; the language instruction | Any patient record or conflict data; anything typed that matched a pattern |
| Record summary | Age **range**; condition, diagnosis, medications, allergies, treatment plan, blood pressure, glucose, dosage, last-visit date (only those present), identifiers inside them removed | Name, phone, village, address, patient ID, identity status, exact age, who created/edited the record, timestamps |
| Conflict explanation | Field *name* (e.g. "phone number"), kind of detail, status, reader's role and permitted actions, whether it changed since raised, evidence/confirmation counts | **Any value**, any name or ID, device or user identifiers |
| Emergencies | Nothing — a fixed message (English + Hindi, 112) is returned without calling the AI, also when the AI is not configured or the user is rate-limited | — |
**Stored by us:** nothing the user typed or the AI wrote (the old question/answer storage was removed). Only a content-free audit row for record summaries and conflict explanations (who, which patient, mode, language, **field names** shared).

## 5. Tests added (actual outcomes)
| Suite | Type | Count | Outcome |
|---|---|---|---|
| `tests/test_ai.py` | logic tests, **test double** for the AI service, in-memory DB stand-in | 61 | passed |
| `tests/test_ai_contract.py` | frontend↔backend contract; scans the source **and the built bundle** for secrets/provider address; dialog-scroll regression | 12 | passed |
| `tests/test_atlas_integration.py` | **real Atlas**, test-double AI | 25 (3 new) | passed |
| `src/aiClient.test.js` | client logic (offline guard, language, request shapes, error mapping) | 12 | passed |
| `e2e/phase_m5_ai.mjs` + `m5_setup.py` | **real browser + backend + Atlas**, AI test double, 4 roles | 54 | passed |
| `e2e/phase_m5_ai_unconfigured.mjs` | real browser, backend started **without** a key | 10 | passed |
| `e2e/m5_api_checks.py` | real HTTP, UI bypassed | 29 | passed |
**Mutation checks** (break a protection on purpose; tests must fail; restore): sending raw unredacted chat text → fails; adding name/phone to summaries → fails; patient summarising another's record → fails; anyone explaining any conflict → fails; emergencies sent to the AI → fails (8); output guard disabled → fails (5); consent not required → fails; conflict explanation including real values → fails.

## 6. Failures and findings during the milestone
| Finding | What it was | Resolution |
|---|---|---|
| **M3 browser suite crashed after my first AI changes** | **Real regression I caused:** the added AI sections made the conflict dialog taller than the screen, and the shared dialog (`.modal`) had no height limit or scrolling, so the **confirmation buttons were off-screen and unreachable**. | Two CSS properties on the shared dialog (short dialogs look identical); permanent test added; M3 suite now 23/23, M4 65/65, M5 54/54. |
| M4 browser suite missed 1 check once | The notification-bell count arrives from an asynchronous fetch; my check read it too early. Next run: `bell="5"`, 65/65. | Check now waits and prints the observed value. (Flake in the test, but it confirms the count is not instant.) |
| Screenshot review | Language picker label was cut off; rate-limit messages said the wait twice. | Shortened the label; single wording. |
| Test slips | A substring check matched `build_chat_messages`; a static check tripped on a docstring word. | Tests made precise (parse the code, check calls). |

## 7. Limitations and remaining gaps (honest list)
**Cannot be known without a real key:** answer quality and factual accuracy; quality of Hindi/Bengali/Tamil/Telugu/Marathi/Gujarati/Kannada/Malayalam/Punjabi/Urdu replies (I cannot verify translations; medical terms may be mistranslated); real Groq error/latency behaviour; whether the default model name is still available; whether the output guard fits real wording.
**Safety/privacy limits by design:**
* **Identifier removal is pattern-based.** It catches Aadhaar/VID-like numbers, phone numbers, emails, tokens, OTP/password phrases and patient IDs. It **cannot detect names, addresses or other free-text identifiers**, or numbers written in words or with unusual separators. The UI warns users; clinical free-text fields written by workers could contain a name that would be sent in a summary.
* **The output guard is heuristic and English-only.** Diagnosis/dosing phrases in other languages, or phrased unusually, may pass. Hallucination cannot be ruled out; mitigations only.
* **Consent model:** a clinician's summary request is the *clinician's* consent, not the patient's. For real patients, sending clinical details to an external processor needs a legal/organisational basis (patient consent, data-processing terms with the provider, Indian data-protection requirements). **This is a policy decision outside the code; do not use real patient data until it is settled.**
* Provider-side retention/logging of what we send is outside our control.
* Only the emergency message is localized (English + Hindi); other languages show that same bilingual message.
**Engineering gaps:** rate limits are in memory per process (a multi-instance deployment needs a shared store); no streaming; the conversation lives only in the open page (lost on reload — by design); the AI chat page exists for patients only (workers/clinicians/supervisors get explanations, clinicians also summaries); conflict explanations are deliberately generic because they get no values; the language preference is stored in `localStorage` (not sensitive, but it persists across users on a shared device); the "Contact my care team" button is still the old placeholder; AI features need a connection and were browser-tested in Edge only (no mobile/accessibility testing); no component-level unit tests for the new React components (covered by contract tests and the browser runs).

## 8. Turning on the real service
1. Put `GROQ_API_KEY=...` in `backend/.env` (never commit it; optional `GROQ_MODEL`, `AI_RATE_PER_MINUTE`, `AI_GLOBAL_PER_MINUTE`, `AI_TIMEOUT_SECONDS`).
2. `cd backend; .\.venv\Scripts\python.exe scripts\check_groq.py hi` → expect `PASS` (one generic question, no patient data).
3. Read several real answers in each language you intend to offer with a native speaker before exposing it to users.

## 9. How to re-run
* Backend: `cd backend; .\.venv\Scripts\python.exe -m pytest -q` ; real Atlas: `$env:SAHAAY_ATLAS_TESTS='1'; ... -m pytest tests/test_atlas_integration.py -q`
* Frontend: `cd frontend; npm test; npm run build`
* Browser (disposable database only): start `e2e/fake_groq.py`; start the backend with `MONGODB_DATABASE=sahaay_test_ui_<hex>`, `GROQ_API_KEY=<any dummy>`, `GROQ_API_URL=http://127.0.0.1:8765/openai/v1/chat/completions`, `AI_RATE_PER_MINUTE=8`; `npm run dev`; in `e2e` set `E2E_DATABASE`/`E2E_STATE`, then `m5_setup.py`, `node phase_m5_ai.mjs`, `m5_api_checks.py`; drop the disposable collections afterwards.
