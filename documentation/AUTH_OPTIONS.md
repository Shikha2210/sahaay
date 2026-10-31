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
