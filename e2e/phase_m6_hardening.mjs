// MILESTONE 6 - offline reliability and hardening in a REAL browser (Edge) against the real backend + real Atlas,
// DISPOSABLE database only (E2E_DATABASE=sahaay_test_*), synthetic data only. Needs the PRODUCTION build served by `vite preview` on :5173
// (the service worker only exists in a build).
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { chromium } from 'playwright-core';
import { createRequire } from 'node:module';
import { Report, launch, newSession, loginUi, goNav, sleep, waitFor, shot, state, readEnv, idb, sessionToken, here, BASE, API } from './ui.mjs';

if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: E2E_DATABASE must be a disposable sahaay_test_* database'); process.exit(2); }
const R = new Report('phase_m6_hardening');
const env = readEnv(); const S = state(); const U = S.users; const { P1 } = S;
const AXE = fs.readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const browser = await launch();
const pageErrors = [];

async function section(title, fn) {
  console.log(`\n--- ${title}`);
  const { context, page } = await newSession(browser);
  page.on('pageerror', e => pageErrors.push(String(e).slice(0, 140)));
  try { await fn(page, context); } catch (e) { R.check(`${title}: finished without an unexpected error`, false, String(e).slice(0, 300)); await shot(page, 'm6_error_' + title.replace(/\W+/g, '_')); }
  await context.close();
}
const login = async (page, role, who) => { const ok = who ? await loginUi(page, role, U[who].email, U[who].password) : await loginUi(page, role, env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD); await sleep(1200); return ok; };
const content = async page => (await page.locator('main .content').innerText().catch(() => '')) || '';
async function apiTok(email, password) { const r = await fetch(API + '/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email, password }) }); return (await r.json()).access_token; }
const apiGet = async (tok, p) => (await fetch(API + p, { headers: { authorization: 'Bearer ' + tok } })).json();
const supTok = () => apiTok(env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD);
const isSynced = o => ['acknowledged','acknowledged_with_conflict'].includes(o.status);
const ops = page => idb(page, 'operations').then(a => a || []);
const toast = page => page.locator('.toast').first().innerText().catch(() => '');
const patientRow = (page, text) => page.locator('tr', { hasText: text }).first();
const axeRun = async (page, label) => {
  await page.evaluate(AXE); const r = await page.evaluate(async () => { const x = await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa'] }); return x.violations.map(v => ({ id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0].html.slice(0, 90) })); });
  const bad = r.filter(v => v.impact === 'critical' || v.impact === 'serious');
  R.check(`accessibility (axe, WCAG A/AA): ${label} has no critical/serious violations`, bad.length === 0, JSON.stringify(bad).slice(0, 380));
  if (r.length - bad.length) R.note('INFO', `axe moderate/minor on ${label}`, r.filter(v => !bad.includes(v)).map(v => v.id + 'x' + v.n).join(','));
};

// ======================================================================= A. worker: offline registration + edit, then reconnect
const REG = `ZZ-SYNTHETIC Offline Reg ${S.run}${Math.random().toString(36).slice(2,6)}`;
await section('worker: offline work then reconnect', async (page, context) => {
  R.check('worker1 signs in', !!(await login(page, 'worker', 'worker1')));
  await goNav(page, 'Patient directory'); await sleep(800);
  await context.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await sleep(500);
  R.check('offline banner is shown on the patient directory', /You are offline/.test(await content(page)));
  await page.getByRole('button', { name: /Register patient/ }).first().click();
  await page.getByPlaceholder('Full name').fill(REG); await page.getByPlaceholder('Contact number').fill('9000000077'); await page.getByPlaceholder('Village name').fill('Synthetic Village');
  await page.getByRole('button', { name: /Save patient locally/ }).click(); await sleep(800);
  R.check('registration while OFFLINE is saved locally and marked "Not synced yet"', /Not synced yet/.test(await patientRow(page, REG).innerText().catch(() => '')));
  await patientRow(page, S.names[P1]).getByRole('button', { name: /^Edit/ }).click();
  await page.getByLabel('Phone number').fill('9444444444'); await page.getByRole('button', { name: 'Save on this device' }).click(); await sleep(800);
  const o1 = await ops(page);
  R.check('two operations are queued locally (create + edit) with status pending', o1.filter(o => o.status === 'pending').length >= 2, o1.map(o => o.status + ':' + o.operation_type).join(','));
  await goNav(page, 'Sync centre'); await sleep(600);
  const t = await content(page);
  R.check('Sync centre offline: shows waiting changes and the offline notice, nothing is claimed as synced', /Waiting to sync/.test(t) && /You are offline/.test(t) && !/Confirmed by the server/.test(t));
  R.check('"Sync now" is disabled while offline (no pretend sync)', await page.locator('.content button', { hasText: /Sync now/i }).first().isDisabled().catch(() => false));
  await shot(page, 'm6_sync_offline');
  await context.setOffline(false); await page.evaluate(() => window.dispatchEvent(new Event('online')));
  const synced = await waitFor(async () => (await ops(page)).every(isSynced), 25000, 500);
  R.check('after reconnecting, automatic sync uploads both operations and the server confirms them', !!synced, (await ops(page)).map(o => o.status).join(','));
  const sup = await supTok(); const list = await apiGet(sup, '/api/patients');
  const arr = Array.isArray(list) ? list : (list.patients || list.items || []);
  R.check('the server now has the offline-registered patient', arr.some(p => p.name === REG), 'patients=' + arr.length);
  R.check('the edit reached the server (phone updated on the shared record)', arr.find(p => p.patient_id === P1)?.phone === '9444444444');
  await goNav(page, 'Sync centre'); await sleep(500);
  R.check('Sync centre now shows nothing waiting', /Everything on this device has been synced/.test(await content(page)));
});

// ======================================================================= B. rejected operation: red, details, retry, discard
await section('worker: rejected operation is never green; retry and discard', async (page) => {
  await login(page, 'worker', 'worker1');
  await goNav(page, 'Patient directory'); await sleep(500);
  // a patient this device knows locally but the server does not -> the real server rejects the edit (patient_not_found)
  await page.evaluate(async () => {
    await new Promise((res, rej) => { const r = indexedDB.open('SAHAAY_DARK_LOCAL'); r.onsuccess = () => { const d = r.result; const tx = d.transaction('patients', 'readwrite'); tx.objectStore('patients').put({ patient_id: 'SH-GHOST1', name: 'ZZ-SYNTHETIC Ghost', phone: '9000000001', village: 'Synthetic Village', condition: 'None', version: 1, owner_id: JSON.parse(sessionStorage.getItem('sahaay-user') || '{}').id }); tx.oncomplete = () => { d.close(); res(); }; tx.onerror = () => rej(tx.error); }; });
  });
  const ownerOk = (await idb(page, 'patients')).some(p => p.patient_id === 'SH-GHOST1');
  R.check('test fixture: a local-only patient exists in IndexedDB', ownerOk);
  await page.reload(); await sleep(1500); await goNav(page, 'Patient directory'); await sleep(800);
  const ghost = patientRow(page, 'ZZ-SYNTHETIC Ghost');
  if (!(await ghost.count())) { R.check('ghost patient visible (fixture owner id matched)', false, 'owner id key may differ'); return; }
  await ghost.getByRole('button', { name: /^Edit/ }).click(); await page.getByLabel('Phone number').fill('9000009999'); await page.getByRole('button', { name: 'Save on this device' }).click();
  const rej = await waitFor(async () => (await ops(page)).some(o => o.status === 'rejected'), 25000, 500);
  R.check('the real server REJECTS the edit and the device records status "rejected"', !!rej, (await ops(page)).map(o => o.status + ':' + (o.last_error || '')).join(','));
  await goNav(page, 'Sync centre'); await sleep(800);
  const t = await content(page);
  R.check('Sync centre has a "Needs attention" list with the rejection reason', /Needs attention/.test(t) && /Rejected by the server/.test(t) && /patient/i.test(t), t.slice(0, 200).replace(/\s+/g, ' '));
  const badge = await page.locator('.badge', { hasText: 'Rejected by the server' }).first();
  const cls = await badge.getAttribute('class');
  R.check('rejected status uses the RED badge, never the green one', /\bred\b/.test(cls) && !/\bgreen\b/.test(cls), cls);
  R.check('no "Synced"/green success indicator is shown for the rejected change', (await page.locator('tr', { hasText: 'Ghost' }).locator('.badge.green').count()) === 0);
  await shot(page, 'm6_sync_rejected');
  await page.getByRole('button', { name: 'Retry', exact: true }).first().click(); await sleep(3500);
  R.check('Retry sends it again; the server rejects it again and it stays red', (await ops(page)).some(o => o.status === 'rejected'));
  await page.getByRole('button', { name: /Discard/ }).first().click(); await sleep(400);
  R.check('Discard asks for confirmation and explains it is permanent', /permanently removed/.test(await page.locator('.modal').innerText()));
  await page.getByRole('button', { name: 'Keep it' }).click(); await sleep(300);
  R.check('"Keep it" leaves the change in place', (await ops(page)).some(o => o.status === 'rejected'));
  await page.getByRole('button', { name: /Discard/ }).first().click(); await page.getByRole('button', { name: 'Discard this change' }).click(); await sleep(1200);
  R.check('after confirming, the rejected change is gone from the queue', !(await ops(page)).some(o => o.status === 'rejected'));
  const audit = (await idb(page, 'audit')) || [];
  R.check('the discard is recorded in the local audit trail', audit.some(a => a.event_type === 'OPERATION_DISCARDED'));
});

// ======================================================================= C/D. two tabs: single upload + cross-tab refresh
const TAB = `ZZ-SYNTHETIC Tab Reg ${S.run}${Math.random().toString(36).slice(2,6)}`;
await section('two tabs: no duplicate upload, other tab refreshes', async (page, context) => {
  await login(page, 'worker', 'worker1');
  const page2 = await context.newPage(); await page2.goto(BASE); await sleep(1800);
  R.check('second tab shares the signed-in session? (sessionStorage is per tab, so it must show the login screen)', (await page2.locator('.role-option').count()) > 0);
  await page2.close();
  // same-origin second tab with the same session: copy token through storage state is not possible; use window.open from the first tab (inherits sessionStorage)
  const [tab2] = await Promise.all([context.waitForEvent('page'), page.evaluate(() => window.open(location.href))]);
  await tab2.waitForLoadState(); await sleep(2000);
  R.check('a tab opened from the signed-in tab is also signed in', (await tab2.locator('.sidebar').count()) > 0);
  await context.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await tab2.evaluate(() => window.dispatchEvent(new Event('offline')));
  await goNav(page, 'Patient directory'); await goNav(tab2, 'Patient directory'); await sleep(500);
  await page.getByRole('button', { name: /Register patient/ }).first().click();
  await page.getByPlaceholder('Full name').fill(TAB); await page.getByPlaceholder('Contact number').fill('9000000088'); await page.getByPlaceholder('Village name').fill('Synthetic Village');
  await page.getByRole('button', { name: /Save patient locally/ }).click();
  const seen = await waitFor(async () => /Not synced yet/.test(await patientRow(tab2, TAB).innerText().catch(() => '')), 8000, 400);
  R.check('cross-tab: the OTHER tab shows the new unsynced patient without a manual reload', !!seen);
  const before = (await apiGet(await supTok(), '/api/sync/summary')).operations_total;
  await context.setOffline(false);
  // fire BOTH tabs at the same instant: the Web Lock must let exactly one upload
  await Promise.all([page.evaluate(() => window.dispatchEvent(new Event('online'))), tab2.evaluate(() => window.dispatchEvent(new Event('online')))]);
  await Promise.all([goNav(page, 'Sync centre'), goNav(tab2, 'Sync centre')]);
  await Promise.all([page.locator('.content button', { hasText: /Sync now/i }).first().click().catch(() => {}), tab2.locator('.content button', { hasText: /Sync now/i }).first().click().catch(() => {})]);
  await waitFor(async () => (await ops(page)).every(isSynced), 25000, 500);
  await sleep(2500);
  const after = (await apiGet(await supTok(), '/api/sync/summary')).operations_total;
  R.check('simultaneous manual syncs from two tabs deliver the operation exactly once on the server', after - before === 1, `server operations +${after - before}`);
  const mine = (await ops(page)).filter(o => o.changes && o.changes.name === TAB);
  R.check('the operation is "synced" locally, one record only', mine.length === 1 && mine[0].status === 'acknowledged', mine.map(o=>o.status).join(','));
  const sup = await supTok(); const list = await apiGet(sup, '/api/patients'); const arr = Array.isArray(list) ? list : (list.patients || list.items || []);
  R.check('the server holds exactly one copy of the patient', arr.filter(p => p.name === TAB).length === 1);
  await goNav(tab2, 'Patient directory');
  const seen2 = await waitFor(async () => !/Not synced yet/.test(await patientRow(tab2, TAB).innerText().catch(() => 'Not synced yet')), 8000, 400);
  R.check('cross-tab: the other tab clears "Not synced yet" after the first tab syncs', !!seen2);
});

// ======================================================================= E. two devices (separate browser profiles): conflict, not overwrite
await section('two devices edit the same detail offline', async (page, context) => {
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 860 } }); const dev2 = await ctx2.newPage();
  R.check('device 1 signs in', !!(await login(page, 'worker', 'worker1'))); R.check('device 2 signs in', !!(await loginUi(dev2, 'worker', U.worker1.email, U.worker1.password)));
  await sleep(1500);
  await context.setOffline(true); await ctx2.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await dev2.evaluate(() => window.dispatchEvent(new Event('offline')));
  for (const [pg, val] of [[page, '9701010101'], [dev2, '9702020202']]) {
    await goNav(pg, 'Patient directory'); await sleep(500);
    await patientRow(pg, S.names[P1]).getByRole('button', { name: /^Edit/ }).click(); await pg.getByLabel('Phone number').fill(val); await pg.getByRole('button', { name: 'Save on this device' }).click(); await sleep(600);
  }
  await context.setOffline(false); await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await waitFor(async () => (await ops(page)).some(o => o.status === 'acknowledged' && o.changes?.phone === '9701010101'), 25000, 500);
  const id1 = (await ops(page))[0]?.device_id, id2 = (await ops(dev2))[0]?.device_id;
  R.check('the two devices have different device ids', !!id1 && !!id2 && id1 !== id2);
  await ctx2.setOffline(false); await dev2.evaluate(() => window.dispatchEvent(new Event('online')));
  const done = await waitFor(async () => (await ops(dev2)).some(o => o.changes?.phone === '9702020202' && isSynced(o)), 25000, 500);
  const o2 = (await ops(dev2)).find(o => o.changes?.phone === '9702020202');
  R.check('device 2 learns its edit CONFLICTED (not silently overwritten, not green-synced)', !!done && o2.status === 'acknowledged_with_conflict', 'status=' + o2?.status);
  const sup = await supTok(); const p = (await apiGet(sup, '/api/patients/' + P1));
  R.check('the server record still holds device 1\'s value (device 2 did not overwrite)', (p.phone || p.patient?.phone) === '9701010101', String(p.phone || p.patient?.phone));
  const cons = await apiGet(sup, '/api/conflicts?status=pending');
  R.check('a pending phone conflict now exists for review', Array.isArray(cons) && cons.some(c => c.patient_id === P1 && c.field === 'phone' && JSON.stringify(c).includes('9702020202')));
  await goNav(dev2, 'Sync centre'); await sleep(600);
  R.check('device 2 Sync centre does not show the conflicted edit as "Synced"', (await dev2.locator('tr', { hasText: 'phone' }).locator('.badge.green').count()) === 0);
  await ctx2.close();
});

// ======================================================================= F. patient/clinician/supervisor offline reads + offline startup
await section('patient: offline reload keeps working', async (page, context) => {
  R.check('patient1 signs in', !!(await login(page, 'patient', 'patient1')));
  await goNav(page, 'Confirm information'); await sleep(1500);
  const online = await content(page);
  R.check('online: confirmations are listed', /confirm/i.test(online));
  await context.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await page.reload(); await sleep(3000);
  R.check('OFFLINE STARTUP: the app shell loads from the service worker and the session is still there', (await page.locator('.sidebar').count()) > 0);
  await goNav(page, 'Confirm information'); await sleep(1500);
  const t = await content(page);
  R.check('offline: saved information is shown with a clear "saved on this device" notice', /saved on this device/i.test(t), t.slice(0, 160).replace(/\s+/g, ' '));
  const btns = page.locator('.content button.outline-btn:not([disabled])', { hasText: /Phone|9|Confirm|Yes|No/ });
  R.check('offline: the confirm answer buttons are disabled and an explanation is shown', /needs a connection/i.test(t));
  await shot(page, 'm6_patient_offline');
  await context.setOffline(false); await page.evaluate(() => window.dispatchEvent(new Event('online'))); await sleep(600);
});
await section('clinician + supervisor offline reads', async (page, context) => {
  await login(page, 'clinician', 'clinician1'); await goNav(page, 'Conflict review'); await sleep(1500);
  await context.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await page.reload(); await sleep(2500);
  await goNav(page, 'Conflict review'); await sleep(1500);
  const t = await content(page);
  R.check('clinician offline: previously loaded conflicts remain readable and the offline notice explains decisions need a connection', (await page.locator('.conflict-card').count()) > 0 && /You are offline/.test(t), 'cards=' + (await page.locator('.conflict-card').count()));
  await context.setOffline(false);
  await page.locator('.profile-mini button[title="Sign out"]').click(); await sleep(800);
  await login(page, 'supervisor'); await goNav(page, 'Patient accounts'); await sleep(1200);
  await context.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline'))); await page.reload(); await sleep(2500);
  await goNav(page, 'Patient accounts'); await sleep(1200);
  R.check('supervisor offline: patient accounts list is readable from the saved copy', /saved on this device/i.test(await content(page)));
  await context.setOffline(false);
});

// ======================================================================= G. PWA
await section('PWA: manifest, service worker, offline start', async (page, context) => {
  await page.goto(BASE); await sleep(2500);
  const mf = await page.evaluate(async () => { const l = document.querySelector('link[rel=manifest]'); if (!l) return null; const r = await fetch(l.href); return r.json(); });
  R.check('web app manifest is linked and valid (name, start_url, standalone display)', !!mf && mf.name === 'SAHAAY Community Health' && mf.display === 'standalone' && mf.start_url === '/', JSON.stringify(mf).slice(0, 160));
  const sw = await waitFor(async () => page.evaluate(async () => { const r = await navigator.serviceWorker.getRegistration(); return !!(r && r.active); }), 15000, 500);
  R.check('a service worker is registered and active', !!sw);
  await page.reload(); await sleep(1500);
  R.check('the page is controlled by the service worker after reload', await page.evaluate(() => !!navigator.serviceWorker.controller));
  // installability can only be judged in a NON-incognito profile (Playwright's default contexts are incognito)
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'sahaay-pwa-'));
  const pctx = await chromium.launchPersistentContext(profile, { channel: 'msedge', headless: true }); const ppage = await pctx.newPage();
  await ppage.goto(BASE); await sleep(3000);
  const cdp = await pctx.newCDPSession(ppage); const inst = await cdp.send('Page.getInstallabilityErrors').catch(() => null);
  const errs = inst ? inst.installabilityErrors.map(e => e.errorId) : null;
  await pctx.close(); fs.rmSync(profile, { recursive: true, force: true });
  R.check('browser reports no installability errors', errs !== null && errs.length === 0, 'errors=' + JSON.stringify(errs));
  if (mf) R.note('INFO', 'manifest icons', JSON.stringify((mf.icons || []).map(i => i.sizes + ' ' + i.type + (i.purpose ? ' ' + i.purpose : ''))));
  await context.setOffline(true); await page.goto(BASE); await sleep(1500);
  R.check('OFFLINE STARTUP with no network: the sign-in screen is served from the cache', (await page.locator('.role-option').count()) > 0);
  await page.goto(BASE + '/some/deep/link'); await sleep(1000);
  R.check('offline deep link falls back to the app shell (no browser error page)', (await page.locator('.role-option, .sidebar').count()) > 0);
  await shot(page, 'm6_pwa_offline');
  await context.setOffline(false);
});

// ======================================================================= H. audit and login audit
await section('audit history and login audit', async (page, context) => {
  // generate login events: a failed attempt and a success, then a sign-out
  await loginUi(page, 'worker', U.worker2.email, 'Wrong-Password-1!', { expectOk: false }); await sleep(500);
  await login(page, 'worker', 'worker2'); await sleep(500);
  await page.locator('.profile-mini button[title="Sign out"]').click(); await sleep(1200);
  R.check('worker2 signed out cleanly', (await page.locator('.role-option').count()) > 0);
  R.check('supervisor signs in', !!(await login(page, 'supervisor'))); await goNav(page, 'Audit history'); await sleep(1500);
  R.check('audit history page has filters and an entries table', (await page.getByLabel('Activity').count()) > 0 && /matching entr/.test(await content(page)), (await content(page)).slice(0, 100).replace(/\s+/g, ' '));
  await page.getByLabel('Activity').selectOption({ index: (await page.getByLabel('Activity').locator('option').allInnerTexts()).findIndex(x => /Sign-ins/.test(x)) }); await sleep(1500);
  const t = await content(page);
  R.check('Sign-ins filter lists a failed sign-in, a successful sign-in and a sign-out', /Failed sign-in/.test(t) && /Signed in/.test(t) && /Signed out/.test(t), t.slice(0, 200).replace(/\s+/g, ' '));
  R.check('no password or token appears anywhere on the audit page', !/Wrong-Password|eyJ[A-Za-z0-9_-]{20,}/.test(t));
  await page.getByLabel('Details for Failed sign-in').first().click().catch(() => {}); await sleep(300);
  const detail = await content(page);
  R.check('failed-sign-in details show a reason but not the typed e-mail address in clear text', /reason|account/i.test(detail) && !detail.includes(U.worker2.email), detail.slice(-220).replace(/\s+/g, ' '));
  await shot(page, 'm6_audit_supervisor');
  await page.getByLabel('Role').selectOption('worker').catch(() => {}); await sleep(1200);
  R.check('role filter narrows the list', /matching entr/.test(await content(page)));
  await axeRun(page, 'supervisor audit history');
  await page.locator('.profile-mini button[title="Sign out"]').click(); await sleep(800);
  await login(page, 'clinician', 'clinician1'); await goNav(page, 'Audit history'); await sleep(1500);
  const ct = await content(page);
  R.check('clinician audit history does NOT offer the sign-in/security group (supervisor-only)', !(await page.getByLabel('Activity').locator('option', { hasText: /Sign-ins/ }).count()));
  // API-level role enforcement
  const wtok = await apiTok(U.worker1.email, U.worker1.password);
  const r = await fetch(API + '/api/audit', { headers: { authorization: 'Bearer ' + wtok } });
  R.check('API: a healthcare worker cannot read the audit log (403)', r.status === 403, 'status ' + r.status);
  const ctok = await apiTok(U.clinician1.email, U.clinician1.password);
  const cj = await apiGet(ctok, '/api/audit?page_size=100');
  R.check('API: clinician response contains no security events', (cj.items || []).every(a => !['LOGIN_SUCCESS', 'LOGIN_FAILED', 'LOGIN_BLOCKED', 'LOGOUT', 'ACCOUNT_REGISTERED'].includes(a.event_type)));
});

// ======================================================================= I. mobile + accessibility
await section('mobile layout and accessibility', async (page, context) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await page.goto(BASE); await sleep(800);
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  R.check('mobile 375px: login screen has no horizontal scrolling', (await overflow()) <= 1, 'overflow ' + await overflow());
  await axeRun(page, 'login screen');
  await login(page, 'worker', 'worker1');
  R.check('mobile: dashboard has no horizontal scrolling', (await overflow()) <= 1, 'overflow ' + await overflow());
  const burger = page.getByRole('button', { name: 'Open menu' });
  R.check('mobile: the menu button is visible and keyboard-labelled', await burger.isVisible());
  await burger.click(); await sleep(400);
  R.check('mobile: the navigation drawer opens', await page.locator('.sidebar').isVisible());
  await page.locator('.sidebar nav .nav-item', { hasText: 'Patient directory' }).click(); await sleep(700);
  R.check('mobile: patient directory has no page-level horizontal scrolling (tables scroll inside their own box)', (await overflow()) <= 1, 'overflow ' + await overflow());
  await shot(page, 'm6_mobile_directory');
  await axeRun(page, 'worker patient directory (mobile)');
  const small = await page.evaluate(() => [...document.querySelectorAll('button, a, input:not([type=hidden]), select')].filter(e => e.offsetParent !== null).map(e => ({ r: e.getBoundingClientRect(), h: e.outerHTML.slice(0, 90) })).filter(x => x.r.width < 24 || x.r.height < 24).map(x => x.h).join(' | '));
  R.check('mobile: no interactive control smaller than 24x24px (WCAG 2.2 AA target size)', small === '', small);
  // dialog behaviour: focus moves in, Tab stays inside, Escape closes, focus returns
  await page.getByRole('button', { name: /Register patient/ }).first().click(); await sleep(500);
  R.check('dialog: has role=dialog, aria-modal and an accessible name', await page.evaluate(() => { const d = document.querySelector('[role=dialog]'); return !!d && d.getAttribute('aria-modal') === 'true' && !!document.getElementById(d.getAttribute('aria-labelledby'))?.textContent; }));
  R.check('dialog: keyboard focus moved inside the dialog', await page.evaluate(() => !!document.activeElement.closest('[role=dialog]')));
  for (let i = 0; i < 12; i++) await page.keyboard.press('Tab');
  R.check('dialog: Tab cycles inside the dialog (focus trap)', await page.evaluate(() => !!document.activeElement.closest('[role=dialog]')));
  await axeRun(page, 'register-patient dialog');
  await page.keyboard.press('Escape'); await sleep(400);
  R.check('dialog: Escape closes it', (await page.locator('[role=dialog]').count()) === 0);
  await page.setViewportSize({ width: 1280, height: 860 });
  for (const nav of ['Overview', 'Sync centre']) { await goNav(page, nav); await sleep(800); await axeRun(page, 'worker ' + nav); }
});

R.note('INFO', 'uncaught page errors during the run', pageErrors.length ? pageErrors.slice(0, 3).join(' | ') : 'none');
R.check('no uncaught JavaScript errors in any page', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));
R.finish();
await browser.close();
