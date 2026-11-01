// AI client behaviour (Milestone 5). `fetch` is a test double for OUR backend: this checks client logic only (request shapes, offline
// guard, error mapping). The AI service itself is never contacted from the browser.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { askAssistant, summariseRecord, explainConflict, describeAiError, getLanguage, setLanguage, LANGUAGES } from './aiClient';
import { api, setToken } from './api';

const json = (status, body, headers = {}) => ({ ok: status < 400, status, headers: { get: k => headers[k.toLowerCase()] }, json: async () => body });
let calls;
function stubFetch(responder) { calls = []; vi.stubGlobal('fetch', vi.fn(async (url, opts) => { calls.push({ url: String(url), opts, body: opts.body ? JSON.parse(opts.body) : null }); return responder(String(url)); })); }
const setOnline = v => Object.defineProperty(navigator, 'onLine', { value: v, configurable: true });

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); setToken(null); vi.unstubAllGlobals(); setOnline(true); });
afterEach(() => setOnline(true));

describe('language preference', () => {
  it('defaults to automatic and remembers a valid choice', () => {
    expect(getLanguage()).toBe('auto'); setLanguage('hi'); expect(getLanguage()).toBe('hi');
  });
  it('ignores unknown values and recovers from a corrupted stored value', () => {
    setLanguage('hi'); setLanguage('klingon'); expect(getLanguage()).toBe('hi');
    localStorage.setItem('sahaay-language', 'not-a-language'); expect(getLanguage()).toBe('auto');
  });
  it('offers Hindi and ten other Indian languages plus automatic, with native names', () => {
    expect(LANGUAGES.length).toBe(12); expect(LANGUAGES.map(l => l[0])).toEqual(['auto', 'en', 'hi', 'bn', 'ta', 'te', 'mr', 'gu', 'kn', 'ml', 'pa', 'ur']);
    expect(LANGUAGES.find(l => l[0] === 'hi')[1]).toContain('हिन्दी');
  });
});

describe('requests go only to our own backend with the exact fields the API expects', () => {
  it('chat: message, recent history and language', async () => {
    stubFetch(() => json(200, { answer: 'ok' }));
    await askAssistant({ message: 'What is anemia?', history: [{ role: 'user', text: 'hi' }], language: 'ta' });
    expect(calls[0].url.endsWith('/api/chatbot/message')).toBe(true);
    expect(calls[0].body).toEqual({ message: 'What is anemia?', history: [{ role: 'user', text: 'hi' }], language: 'ta' });
    expect(calls[0].opts.timeoutMs).toBeUndefined();                       // the override is consumed by api(), never forwarded to fetch
    expect(JSON.stringify(calls)).not.toMatch(/groq|gsk_|api\.groq/i);
  });
  it('summary: consent flag comes only from the caller, patient_id only when given', async () => {
    stubFetch(() => json(200, { summary: 'x' }));
    await summariseRecord({ language: 'hi', consented: true });
    await summariseRecord({ patientId: 'SH-1', consented: false });
    await summariseRecord({ language: 'en' });
    expect(calls[0].body).toEqual({ language: 'hi', acknowledge_external_ai: true });
    expect(calls[1].body).toEqual({ patient_id: 'SH-1', language: 'auto', acknowledge_external_ai: false });
    expect(calls[2].body.acknowledge_external_ai).toBe(false);
  });
  it('conflict explanation sends only the conflict id and language (never record values)', async () => {
    stubFetch(() => json(200, { explanation: 'x' }));
    await explainConflict({ conflictId: 'CF-1', language: 'bn' });
    expect(calls[0].url.endsWith('/api/ai/conflict-explanation')).toBe(true);
    expect(calls[0].body).toEqual({ conflict_id: 'CF-1', language: 'bn' });
  });
});

describe('offline handling', () => {
  it('does not even try the network when the browser is offline', async () => {
    stubFetch(() => json(200, {})); setOnline(false);
    for (const call of [() => askAssistant({ message: 'hi' }), () => summariseRecord({ consented: true }), () => explainConflict({ conflictId: 'CF-1' })]) {
      await expect(call()).rejects.toMatchObject({ code: 'offline' });
    }
    expect(calls.length).toBe(0);
    expect(describeAiError({ code: 'offline' }).kind).toBe('offline');
  });
  it('a network failure is described as offline, not as a server fault', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const e = await askAssistant({ message: 'hi' }).catch(x => x);
    expect(e.code).toBe('network');
    expect(describeAiError(e)).toMatchObject({ kind: 'offline' });
    expect(describeAiError(e).message).toMatch(/internet connection/);
  });
});

describe('error mapping', () => {
  it('rate limits carry the wait time from the server', async () => {
    stubFetch(() => json(429, { detail: { code: 'ai_rate_limited', message: 'You are sending requests too quickly. Please wait 7 seconds.', retry_after_seconds: 7, scope: 'user' } }));
    const e = await askAssistant({ message: 'hi' }).catch(x => x);
    expect(e.status).toBe(429); expect(e.detail.retry_after_seconds).toBe(7);
    expect(describeAiError(e)).toMatchObject({ kind: 'rate', retryAfter: 7 });
  });
  it('maps every documented failure to a clear, non-technical sentence', () => {
    expect(describeAiError({ code: 'ai_not_configured', message: 'x' })).toMatchObject({ kind: 'config' });
    expect(describeAiError({ code: 'ai_timeout', message: 'x' }).message).toMatch(/too long/);
    expect(describeAiError({ code: 'token_expired', message: 'x' })).toMatchObject({ kind: 'auth' });
    expect(describeAiError({ code: 'ai_unavailable', message: 'The AI assistant is temporarily unavailable.' }).message).toBe('The AI assistant is temporarily unavailable.');
    expect(describeAiError(null).message).toMatch(/try again/);
    expect(describeAiError({}).message).toMatch(/try again/);
  });
  it('a refused consent surfaces the server\'s message', async () => {
    stubFetch(() => json(422, { detail: { code: 'consent_required', message: 'Please confirm that you agree.' } }));
    const e = await summariseRecord({ consented: false }).catch(x => x);
    expect(e.code).toBe('consent_required'); expect(describeAiError(e).message).toBe('Please confirm that you agree.');
  });
  it('plain api() calls keep working and still expose structured details', async () => {
    stubFetch(() => json(403, { detail: { code: 'role_not_permitted', message: 'no', required_roles: ['clinician'] } }));
    const e = await api('/api/anything').catch(x => x);
    expect(e.code).toBe('role_not_permitted'); expect(e.detail.required_roles).toEqual(['clinician']);
  });
});
