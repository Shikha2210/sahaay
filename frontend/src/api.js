const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
const TOKEN_KEY = 'sahaay-access-token';
const REQUEST_TIMEOUT_MS = 20000;
export const SESSION_EXPIRED_EVENT = 'sahaay:session-expired';

// SECURITY NOTE: the access token lives in sessionStorage (cleared when the tab closes) and a memory copy.
// This is better than localStorage (which survives browser restarts) but is STILL readable by any JavaScript
// running on the page, so an XSS bug would expose it. Mitigations: short token lifetime (server), no HTML
// injection points (React escapes output; no dangerouslySetInnerHTML), dependency auditing.
let memoryToken = null;

// Tokens/profiles written by earlier prototype versions lived in localStorage; remove them.
export function purgeLegacyStorage() {
  try { localStorage.removeItem('sahaay-access-token'); localStorage.removeItem('sahaay-session'); } catch { /* storage unavailable */ }
}
purgeLegacyStorage();

export const getToken = () => {
  if (memoryToken) return memoryToken;
  try { memoryToken = sessionStorage.getItem(TOKEN_KEY); } catch { memoryToken = null; }
  return memoryToken;
};
export const setToken = token => {
  memoryToken = token || null;
  try { token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage unavailable */ }
};

// Client-side convenience only (to show "session expired" early). The server is the authority.
export function tokenExpiry(token) {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(part.padEnd(part.length + (4 - part.length % 4) % 4, '='))).exp * 1000;
  } catch { return null; }
}
export const isTokenExpired = (token, now = Date.now()) => { const exp = tokenExpiry(token); return exp === null || exp <= now; };

export class ApiError extends Error {
  constructor(message, { status = 0, code = null, detail = null } = {}) { super(message); this.name = 'ApiError'; this.status = status; this.code = code; this.detail = detail; }
}

// FastAPI sends validation problems as a list; turn the first one into a sentence the person can act on.
const FIELD_LABEL = { password: 'Password', email: 'Email address', name: 'Name', phone: 'Phone number', village: 'Village', role: 'Role' };
function validationMessage(items) {
  const it = items[0] || {}; const field = [...(it.loc || [])].reverse().find(x => typeof x === 'string' && x !== 'body');
  const label = FIELD_LABEL[field] || 'One of the fields';
  if (it.type === 'string_too_short') return `${label} is too short: it needs at least ${it.ctx?.min_length ?? 'more'} characters.`;
  if (it.type === 'string_too_long') return `${label} is too long: at most ${it.ctx?.max_length ?? 'fewer'} characters.`;
  if (field === 'email') return 'Enter a valid email address, for example name@example.com.';
  if (it.type === 'missing') return `${label} is required.`;
  return `${label} is not valid. Please check it and try again.`;
}

export async function api(path, rawOptions = {}) {
  const { timeoutMs, ...options } = rawOptions;   // timeoutMs: per-call override (the AI assistant needs longer than ordinary calls)
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || REQUEST_TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers, signal: controller.signal });
  } catch (e) {
    throw new ApiError(e.name === 'AbortError' ? 'The server took too long to respond.' : 'Cannot reach the server.', { code: 'network' });
  } finally { clearTimeout(timer); }
  const data = await response.json().catch(() => ({ detail: 'Unexpected server response' }));
  if (!response.ok) {
    const d = data.detail;
    const message = typeof d === 'string' ? d : (d && d.message) || (Array.isArray(d) ? validationMessage(d) : `Request failed (${response.status})`);
    const code = d && typeof d === 'object' && !Array.isArray(d) ? d.code : null;
    const isAuthCall = path.startsWith('/api/auth/login') || path.startsWith('/api/auth/register');
    if (response.status === 401 && token && !isAuthCall) {
      // Keep local data; only stop using the dead token. The app shows a "session expired" banner.
      setToken(null);
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT, { detail: { code } }));
    }
    throw new ApiError(message, { status: response.status, code, detail: d && typeof d === 'object' && !Array.isArray(d) ? d : null });
  }
  return data;
}
export async function registerApi(name, email, password) { const result = await api('/api/auth/register', { method: 'POST', body: JSON.stringify({ name, email, password, role: 'patient' }) }); return result; }
export async function loginApi(email, password) { return api('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }); }
export async function meApi() { return api('/api/auth/me'); }
