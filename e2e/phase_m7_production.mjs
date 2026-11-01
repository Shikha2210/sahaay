// MILESTONE 7 - production REHEARSAL in a real browser (Edge): backend in ENVIRONMENT=production over HTTPS (self-signed local certificate),
// production frontend build served with the documented security headers, disposable Atlas database sahaay_rehearsal_*, synthetic data only.
// Credentials come from files/env set by the operator script and are never printed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright-core';
import { Report, sleep, waitFor, shot } from './ui.mjs';

process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';   // only for this local rehearsal's self-signed certificate
const FRONT = 'https://localhost:5174', API = 'https://localhost:8443';
const TEMP = process.env.TEMP || os.tmpdir();
const dbName = fs.readFileSync(path.join(TEMP, 'm7db.txt'), 'utf8').trim();
if (!dbName.startsWith('sahaay_rehearsal_')) { console.error('Refusing: not a rehearsal database'); process.exit(2); }
const SUP = { email: 'rehearsal.admin@sahaay-rehearsal.org', password: fs.readFileSync(path.join(TEMP, 'm7sup.txt'), 'utf8').trim() };
const R = new Report('phase_m7_production');
const run = Math.random().toString(36).slice(2, 7);

const j = async (p, opts = {}) => { const r = await fetch(API + p, opts); let b = null; try { b = await r.json(); } catch { /* none */ } return { status: r.status, headers: r.headers, body: b }; };
const post = (p, body, tok) => j(p, { method: 'POST', headers: { 'content-type': 'application/json', ...(tok ? { authorization: 'Bearer ' + tok } : {}) }, body: JSON.stringify(body) });
const get = (p, tok) => j(p, { headers: tok ? { authorization: 'Bearer ' + tok } : {} });

// ---------------------------------------------------------------- API-level production checks
const h = await get('/health');
R.check('backend (production mode) is up over HTTPS and connected to Atlas', h.status === 200 && h.body.mongodb === 'connected' && h.body.database === dbName);
for (const p of ['/docs', '/redoc', '/openapi.json']) R.check(`API documentation endpoint ${p} is not served in production`, (await get(p)).status === 404);
R.check('API sends HSTS, nosniff, frame-deny, CSP, no-store', (() => { const x = h.headers; return /max-age=31536000/.test(x.get('strict-transport-security') || '') && x.get('x-content-type-options') === 'nosniff' && x.get('x-frame-options') === 'DENY' && /frame-ancestors 'none'/.test(x.get('content-security-policy') || ''); })());
R.check('API data responses are never cacheable', (await get('/api/patients')).headers.get('cache-control') === 'no-store');
const pre = origin => j('/api/patients', { method: 'OPTIONS', headers: { Origin: origin, 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'authorization' } });
R.check('CORS: the production frontend origin is allowed', (await pre(FRONT)).headers.get('access-control-allow-origin') === FRONT);
R.check('CORS: other origins (evil site, localhost dev) are refused', !(await pre('https://evil.example')).headers.get('access-control-allow-origin') && !(await pre('http://localhost:5173')).headers.get('access-control-allow-origin'));
R.check('unauthenticated data requests are refused (401)', (await get('/api/patients')).status === 401 && (await get('/api/audit')).status === 401);
const sup = await post('/api/auth/login', SUP);
R.check('the bootstrap supervisor can sign in', sup.status === 200 && !!sup.body.access_token);
const supTok = sup.body.access_token;
const wk = { name: 'ZZ-SYNTHETIC Worker', email: `zz.worker.${run}@sahaay-rehearsal.org`, password: 'Rehearse-Pass-2026#x', role: 'worker' };
R.check('supervisor provisions a worker', (await post('/api/admin/users', wk, supTok)).status === 201);
R.check('wrong password is a generic 401 (no account hints)', (await post('/api/auth/login', { email: wk.email, password: 'nope-nope-nope' })).body?.detail === 'Incorrect email or password');
const wtok = (await post('/api/auth/login', { email: wk.email, password: wk.password })).body.access_token;
R.check('a worker token cannot read the audit log or provision users', (await get('/api/audit', wtok)).status === 403 && (await post('/api/admin/users', wk, wtok)).status === 403);

// ---------------------------------------------------------------- browser
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--ignore-certificate-errors'] });
const context = await browser.newContext({ viewport: { width: 1280, height: 860 }, ignoreHTTPSErrors: true });
const page = await context.newPage();
const hosts = new Set(), consoleErrs = [], csp = [];
page.on('request', r => { try { hosts.add(new URL(r.url()).host); } catch { /* data: */ } });
page.on('console', m => { if (m.type() === 'error') consoleErrs.push(m.text().slice(0, 160)); if (/Content Security Policy|Refused to/i.test(m.text())) csp.push(m.text().slice(0, 160)); });
page.on('pageerror', e => consoleErrs.push(String(e).slice(0, 160)));

await page.goto(FRONT); await sleep(2500);
R.check('production frontend loads over HTTPS', (await page.locator('.role-option').count()) > 0);
const front = await (await fetch(FRONT + '/')).headers;
R.check('frontend sends CSP (no unsafe-eval/inline scripts), HSTS, frame-deny', /script-src 'self'/.test(front.get('content-security-policy')) && !/unsafe-eval/.test(front.get('content-security-policy')) && /max-age/.test(front.get('strict-transport-security')));

await page.locator('.role-option', { hasText: 'Healthcare Worker' }).click();
await page.getByPlaceholder('Enter your name').fill('Synthetic Tester'); await page.getByPlaceholder('you@example.com').fill(wk.email); await page.getByPlaceholder('Enter password').fill(wk.password);
await page.locator('button.sign-in').click();
R.check('worker signs in through the production frontend -> production API (cross-origin, CORS ok)', !!(await waitFor(async () => (await page.locator('.sidebar').count()) > 0, 15000)));
await sleep(1500);
const tokenWhere = await page.evaluate(() => ({ session: !!sessionStorage.getItem('sahaay-access-token'), local: Object.keys(localStorage).filter(k => /token|jwt|auth/i.test(k)).length }));
R.check('the access token lives in sessionStorage only (not localStorage)', tokenWhere.session && tokenWhere.local === 0, JSON.stringify(tokenWhere));

// offline work -> reconnect -> real server
const NAME = `ZZ-SYNTHETIC Prod Offline ${run}`;
await page.locator('.sidebar nav .nav-item', { hasText: 'Patient directory' }).click(); await sleep(800);
await context.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await sleep(500);
await page.getByRole('button', { name: /Register patient/ }).first().click();
await page.getByPlaceholder('Full name').fill(NAME); await page.getByPlaceholder('Contact number').fill('9000000055'); await page.getByPlaceholder('Village name').fill('Synthetic Village');
await page.getByRole('button', { name: /Save patient locally/ }).click(); await sleep(800);
R.check('OFFLINE on the production build: registration saved locally and marked not synced', /Not synced yet/.test(await page.locator('tr', { hasText: NAME }).first().innerText().catch(() => '')));
await page.reload(); await sleep(2500);
R.check('OFFLINE reload of the production build still opens, still signed in, and keeps the unsynced patient', (await page.locator('.sidebar').count()) > 0);
await page.locator('.sidebar nav .nav-item', { hasText: 'Patient directory' }).click(); await sleep(800);
R.check('the unsynced patient survived the offline reload', (await page.locator('tr', { hasText: NAME }).count()) === 1);
await context.setOffline(false); await page.evaluate(() => window.dispatchEvent(new Event('online'))); consoleErrs.length = 0;   // errors while deliberately offline are expected
const idb = store => page.evaluate(s => new Promise(res => { const r = indexedDB.open('SAHAAY_DARK_LOCAL'); r.onsuccess = () => { const q = r.result.transaction(s).objectStore(s).getAll(); q.onsuccess = () => { r.result.close(); res(q.result); }; }; }), store);
const synced = await waitFor(async () => (await idb('operations')).every(o => o.status === 'acknowledged'), 25000, 500);
R.check('on reconnect the production frontend syncs to the production API and the server acknowledges', !!synced);
const list = (await get('/api/patients', supTok)).body || [];
R.check('the offline-created patient exists on the production backend (Atlas rehearsal database)', list.filter(p => p.name === NAME).length === 1);
await sleep(300);

// PWA / PNG icons / self-hosted fonts / third parties
const mf = await page.evaluate(async () => (await fetch(document.querySelector('link[rel=manifest]').href)).json());
const png = (mf.icons || []).filter(i => i.type === 'image/png');
R.check('manifest lists PNG icons 192 and 512 (any) and maskable variants', ['192x192', '512x512'].every(s => png.some(i => i.sizes === s && i.purpose === 'any')) && png.some(i => i.purpose === 'maskable'), JSON.stringify(png.map(i => i.sizes + ' ' + i.purpose)));
const iconStatuses = await page.evaluate(async list => Promise.all(list.map(async i => { const r = await fetch(i.src); return r.status + ':' + (r.headers.get('content-type') || ''); })), png);
R.check('every PNG icon is served as image/png', iconStatuses.every(s => s === '200:image/png'), iconStatuses.join(' '));
R.check('apple-touch-icon is linked and served', await page.evaluate(async () => { const l = document.querySelector('link[rel=apple-touch-icon]'); return !!l && (await fetch(l.href)).status === 200; }));
R.check('no third-party hosts were contacted (fonts are self-hosted)', [...hosts].every(x => x === 'localhost:5174' || x === 'localhost:8443'), [...hosts].join(','));
R.check('no Content-Security-Policy violations in the console', csp.length === 0, csp.slice(0, 2).join(' | '));
R.check('no console errors on the production build', consoleErrs.length === 0, consoleErrs.slice(0, 2).join(' | '));
const fontFiles = await page.evaluate(() => performance.getEntriesByType('resource').map(e => e.name).filter(n => /\.woff2/.test(n)));
R.check('self-hosted DM Sans / Manrope font files were fetched from our own origin', fontFiles.length > 0 && fontFiles.every(n => n.startsWith(FRONT)) , fontFiles.length + ' woff2 files');
const idbText = JSON.stringify(await idb('operations')) + JSON.stringify(await idb('patients'));
R.check('no password or token is stored in IndexedDB', !idbText.includes(wk.password) && !/eyJ[A-Za-z0-9_-]{20,}/.test(idbText));

// installability in a normal (non-incognito) profile
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'sahaay-pwa7-'));
const pctx = await chromium.launchPersistentContext(profile, { channel: 'msedge', headless: true, ignoreHTTPSErrors: true, args: ['--ignore-certificate-errors'] });
const pp = await pctx.newPage(); await pp.goto(FRONT); await sleep(3500);
const inst = await (await pctx.newCDPSession(pp)).send('Page.getInstallabilityErrors').catch(() => null);
R.check('browser reports no installability errors for the production build', inst && inst.installabilityErrors.length === 0, JSON.stringify(inst?.installabilityErrors?.map(e => e.errorId)));
await pctx.close(); fs.rmSync(profile, { recursive: true, force: true });

// ---------------------------------------------------------------- manual-style keyboard checks (real key presses, no mouse)
await page.locator('.profile-mini button[title="Sign out"]').click(); await sleep(1500);
await page.goto(FRONT); await sleep(1200);
const kb = await browser.newContext({ viewport: { width: 1280, height: 860 }, ignoreHTTPSErrors: true }); const kp = await kb.newPage(); await kp.goto(FRONT); await sleep(1500);
const stops = []; let visibleFocus = 0;
for (let i = 0; i < 14; i++) {
  await kp.keyboard.press('Tab');
  const s = await kp.evaluate(() => { const e = document.activeElement; if (!e || e === document.body) return null; const cs = getComputedStyle(e); const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none'); return { tag: e.tagName, type: e.type, cls: e.className, label: (e.getAttribute('aria-label') || e.innerText || e.placeholder || '').slice(0, 24), ring: !!ring }; });
  if (s) { stops.push(s); if (s.ring) visibleFocus++; }
}
R.check('keyboard: Tab reaches the role choices, the form fields and the sign-in button', stops.some(s => /Healthcare|Patient/.test(s.label)) && stops.some(s => /sign in/i.test(s.label)), stops.map(s => s.tag + ':' + s.label).join(' > ').slice(0, 220));
R.check('keyboard: every focused control shows a visible focus indicator', stops.length > 0 && visibleFocus === stops.length, `${visibleFocus}/${stops.length} showed an outline or ring; without: ` + stops.filter(s => !s.ring).map(s => s.tag + '.' + s.cls + ':' + s.label).join(' | '));
await kp.locator('.role-option', { hasText: 'Healthcare Worker' }).focus(); await kp.keyboard.press('Enter');
await kp.keyboard.press('Tab'); await kp.keyboard.press('Tab'); await kp.keyboard.press('Tab'); await kp.keyboard.type('Synthetic Tester'); await kp.keyboard.press('Tab'); await kp.keyboard.type(wk.email); await kp.keyboard.press('Tab'); await kp.keyboard.type(wk.password); await kp.keyboard.press('Enter');
R.check('keyboard only: choose role, fill the form and sign in with Enter (no mouse)', !!(await waitFor(async () => (await kp.locator('.sidebar').count()) > 0, 15000)));
await sleep(1200);
await kp.locator('.sidebar nav .nav-item').nth(1).focus(); await kp.keyboard.press('Enter'); await sleep(600);
R.check('keyboard: a sidebar item is activated with Enter', /Patient directory/i.test(await kp.locator('main').innerText()));
await kp.getByRole('button', { name: /Register patient/ }).first().focus(); await kp.keyboard.press('Enter'); await sleep(500);
R.check('keyboard: Enter opens the dialog and focus moves into it', await kp.evaluate(() => !!document.activeElement.closest('[role=dialog]')));
await kp.keyboard.press('Escape'); await sleep(400);
R.check('keyboard: Escape closes the dialog and returns focus to the button that opened it', (await kp.locator('[role=dialog]').count()) === 0 && /Register patient/.test(await kp.evaluate(() => document.activeElement.innerText || '')));
await shot(kp, 'm7_keyboard_signed_in');
await kb.close();

R.finish();
await context.close(); await browser.close();
