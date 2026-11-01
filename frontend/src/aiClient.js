// Client for the SAHAAY AI endpoints (Milestone 5). The browser only ever talks to OUR backend; the Groq API key and the Groq URL
// exist only on the server. Nothing the user types or the AI answers is stored by the app (conversation lives in component state).
import { api } from './api';

// [code, label shown in the picker]. Codes match the backend's supported list.
export const LANGUAGES = [['auto', 'Auto (my language)'], ['en', 'English'], ['hi', 'हिन्दी (Hindi)'], ['bn', 'বাংলা (Bengali)'], ['ta', 'தமிழ் (Tamil)'],
  ['te', 'తెలుగు (Telugu)'], ['mr', 'मराठी (Marathi)'], ['gu', 'ગુજરાતી (Gujarati)'], ['kn', 'ಕನ್ನಡ (Kannada)'], ['ml', 'മലയാളം (Malayalam)'], ['pa', 'ਪੰਜਾਬੀ (Punjabi)'], ['ur', 'اردو (Urdu)']];
const LANG_KEY = 'sahaay-language';   // a display preference only (no health data), so localStorage is fine
export function getLanguage() { try { const v = localStorage.getItem(LANG_KEY); return LANGUAGES.some(([k]) => k === v) ? v : 'auto'; } catch { return 'auto'; } }
export function setLanguage(v) { if (LANGUAGES.some(([k]) => k === v)) { try { localStorage.setItem(LANG_KEY, v); } catch { /* storage unavailable */ } } }

const AI_TIMEOUT_MS = 35000;   // the server waits up to 20 s for the AI service
const post = (path, body) => api(path, { method: 'POST', timeoutMs: AI_TIMEOUT_MS, body: JSON.stringify(body) });

function requireOnline() {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) throw Object.assign(new Error('The AI assistant needs an internet connection.'), { code: 'offline' });
}
export const aiStatus = () => api('/api/ai/status');
export async function askAssistant({ message, history = [], language = 'auto' }) { requireOnline(); return post('/api/chatbot/message', { message, history, language }); }
// `consented` must come from an explicit user action (checkbox); the server refuses a summary without it.
export async function summariseRecord({ patientId = null, language = 'auto', consented }) {
  requireOnline(); return post('/api/ai/patient-summary', { ...(patientId ? { patient_id: patientId } : {}), language, acknowledge_external_ai: consented === true });
}
export async function explainConflict({ conflictId, language = 'auto' }) { requireOnline(); return post('/api/ai/conflict-explanation', { conflict_id: conflictId, language }); }

// Turns any failure into one clear sentence plus a kind the screens can style. Never shows raw server text for unexpected errors.
export function describeAiError(e) {
  const code = e && (e.code || (e.detail && e.detail.code));
  if (code === 'offline' || code === 'network') return { kind: 'offline', message: 'You appear to be offline. The AI assistant needs an internet connection. The rest of SAHAAY keeps working.' };
  if (code === 'ai_rate_limited') { const wait = e.detail && e.detail.retry_after_seconds; return { kind: 'rate', retryAfter: wait || null, message: e.message || 'Too many requests. Please wait a moment.' }; }
  if (code === 'ai_not_configured') return { kind: 'config', message: 'The AI assistant is not set up on this server yet. Please ask your administrator.' };
  if (code === 'ai_timeout') return { kind: 'error', message: 'The AI assistant took too long to answer. Please try again.' };
  if (code === 'token_expired' || code === 'token_invalid') return { kind: 'auth', message: 'Your session has expired. Please sign in again.' };
  if (e && typeof e.message === 'string' && e.message) return { kind: 'error', message: e.message };
  return { kind: 'error', message: 'Something went wrong. Please try again.' };
}
