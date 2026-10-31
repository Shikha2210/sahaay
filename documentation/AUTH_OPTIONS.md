# AUTH_OPTIONS.md — token storage & logout evaluation (decision pending; no auth code changed yet)

## What exists today
- Login returns a JWT (HS256, 12 h). Frontend keeps it in `localStorage` (`sahaay-access-token`) and sends `Authorization: Bearer`.
- Frontend also keeps `{role,name,email}` in `localStorage` (`sahaay-session`); UI role/nav comes from this client-editable value. Backend ignores it and re-checks the user and role from the database on every request, so editing it only changes what the UI shows.
- Logout removes the token and `sahaay-session` only. **IndexedDB (patients, operations, conflicts, audit, sync cursor) is left intact** (audit X2). `sahaay-device-id` also persists.
- 401 responses are not handled; there is no refresh and no revocation.

## The two threats that matter here
1. **XSS reads the token.** Anything in `localStorage` is readable by any script on the page. Your app has no user-generated HTML rendering today and React escapes by default, which lowers the risk, but one bad dependency or future `dangerouslySetInnerHTML` would expose a 12 h token.
2. **Shared devices.** Healthcare workers share phones/tablets. Patient data left in IndexedDB after logout is the bigger real-world risk, and it is independent of where the token lives.

## Options
| Option | Protects against | Costs / risks for SAHAAY |
|---|---|---|
| **A. Status quo** (`localStorage` Bearer, 12 h) | – | XSS steals token; survives browser restarts (good for field use, bad on shared devices). |
| **B. Bearer in memory + `sessionStorage`, short access token (e.g. 15–30 min) + rotating refresh token** | Reduces window of theft; cleared on tab close | Still readable by XSS during the session. Offline use: when the access token expires offline the user can still read local data and queue changes (the sync just waits for re-login). Small backend change (refresh endpoint). |
| **C. HttpOnly + Secure + SameSite cookie (short access, rotating refresh)** | XSS cannot read the token (it can still *use* the session while the page is open) | Needs CSRF protection (SameSite=Lax/Strict + custom header), same-site or carefully configured CORS (`allow_credentials`, exact origins). Frontend and API on different domains (e.g. Netlify + Render) make `SameSite` cookies awkward — typically needs a shared parent domain or a reverse proxy. Offline: the browser can't read the cookie, so the app must remember "who is logged in" separately (profile in IndexedDB). More moving parts to get right before a hackathon. |
| **D. Opaque server-side sessions (random id in HttpOnly cookie, stored in Mongo)** | Instant revocation, no JWT forgery risk | Everything in C plus a session store and a DB hit per request (already true today since users are re-loaded per request). |

## Recommendation (needs your approval before I change anything)
Do it in two steps so the demo stays reliable:

1. **Now (Milestone 2/4, low risk, independent of token storage):**
   - Logout and user-switch **wipe IndexedDB** (patients, operations, conflicts, audit, sync_state) and the device-scoped localStorage keys, but **only after** checking for unsynced operations: if there are pending operations the user is warned and must either sync first or explicitly discard (confirmation dialog). Pending operations are never silently deleted.
   - Every queued operation and local record is tagged with the owning `user_id`; sync refuses to send operations owned by a different user than the logged-in one.
   - Handle 401: stop syncing, keep local data, show "session expired — sign in again". Re-login as the *same* user resumes the queue; a *different* user triggers the wipe flow above.
   - Move the access token to **memory + `sessionStorage`**, shorten expiry to 30–60 min with a refresh token (Option B). Keep `JWT_SECRET` strength check.
2. **Later, only if time allows and deployment domains permit:** Option C (HttpOnly cookie). I'd evaluate it once we know where it will be hosted.

Trade-off you are accepting with B: a script injected into the page could still read the token for the duration of the session. Mitigated by short expiry, no HTML injection points, CSP headers on the static host, and dependency auditing.

**Please confirm or change this plan** (reply "approve auth plan" or say which option you prefer). Until then I will not touch token storage; the logout wipe (step 1) is a data-safety fix and will be implemented in Milestone 2/4 with a confirmation dialog.


---
# Decision and implementation status (Milestone 2, 2026-11-01)

**Approved by the project owner:** Option B (step 1 of the recommendation): short-lived JWT, token in `sessionStorage`, logout/user-switch data protection, per-user operation tagging. HttpOnly cookies remain a later option.

## What was implemented
| Item | Status |
|---|---|
| Access token lifetime 720 → **60 min default** (configurable 5–240) | Done, tested |
| Token in **sessionStorage** (+ memory copy); old `localStorage` token/profile deleted on load | Done, tested |
| Structured 401s: `token_expired`, `token_invalid`, `account_unavailable`; `exp`+`sub` required; `alg=none`/wrong-secret rejected | Done, tested (user lookup faked) |
| Expired token handling in the UI: **soft expiry** — banner, sync paused, local data kept, "Sign in again" | Done in code; **UI not manually verified in a browser** |
| Logout: counts unsynced ops, explains loss, requires checkbox + explicit button; "Sync now" option | Done in code; logic tested, dialog UI not browser-tested |
| Device owner lock: another user cannot use the device while the previous user has unsynced changes; explicit discard dialog | Done (logic tested; dialog UI not browser-tested) |
| Every queued op has `owner_id`; sync only sends the signed-in user's ops | Done, tested incl. a mutation check |
| Refresh tokens | **Not implemented.** After 60 min the user must sign in again (online). Local work continues offline meanwhile. |
| Login rate limit (5 failures / 5 min per IP+email, in-memory) | Done, tested; per-process only |

## Why "soft expiry" (design decision)
Login needs the server, so hard-locking a field worker out the moment the token expires would block access to their own offline data. Instead expiry stops all server calls and sync, shows a banner, and keeps local data readable/editable until explicit logout. Trade-off: on an unattended unlocked device, cached records remain visible until the tab closes or the user signs out. (Idle auto-lock is a possible later addition.)

## Honest limitations (please keep these in the pitch)
* **sessionStorage is readable by any JavaScript on the page.** If an attacker achieves XSS they can read the token for as long as the tab lives. Short expiry limits the window; it does not prevent theft.
* **IndexedDB is not encrypted.** Anyone with access to the unlocked browser profile (or devtools) can read cached patient data. The owner-lock is application-level protection, not a security boundary against a determined local attacker. Tab closure without logout leaves the previous user's records on disk until the next login wipes/blocks.
* The client-side session profile (role, name) in sessionStorage is editable by the user; it only changes what the UI shows. All permissions are enforced by the backend on every request.
* Login rate limiting is per backend process (memory); a multi-instance deployment would need a shared store.

## XSS review (code scan, Milestone 2)
* Searched `frontend/src` for `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, string `setTimeout`, `javascript:` URLs, user-controlled `href`/`src`: **no occurrences** (only a comment). All data is rendered through React text nodes, which are escaped.
* The QR value shown on the identity page is rendered as text, not HTML. XML is parsed with `DOMParser` as `application/xml` and only the root element name is displayed.
* **No Content-Security-Policy** is set yet. A meta-CSP would break the Vite dev server and the app's inline `<style>`/style attributes; a proper CSP (script-src 'self', connect-src = API origin, frame-ancestors 'none') should be set as an HTTP header on the static host — **scheduled for Milestone 9 (deployment)**. Backend now sends `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-store` on `/api/*`.
* The Google Fonts `@import` is a third-party origin loaded by every page (also an offline failure); self-host fonts in Milestone 9.
* Dependencies: see DEPENDENCY_AUDIT.md.
