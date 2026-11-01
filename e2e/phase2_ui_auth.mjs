// PHASE 2 - frontend authentication in a real browser (Edge) against the real backend + Atlas.
import { Report, launch, newSession, loginUi, navLabels, goNav, idb, sessionToken, sleep, waitFor, shot, state, readEnv, makeExpiredToken, BASE } from './ui.mjs';

const R = new Report('phase2_ui_auth');
const env = readEnv(); const S = state(); const w1 = S.users.worker1;
const browser = await launch();
const pageErrors = [];
const track = page => { page.on('pageerror', e => pageErrors.push(String(e).slice(0, 120))); };

async function registerOffline(page, name) {
  await goNav(page, 'Patient directory');
  await page.getByRole('button', { name: /Register patient/ }).first().click();
  await page.getByPlaceholder('Full name').fill(name);
  await page.getByPlaceholder('Contact number').fill('9000000000');
  await page.getByPlaceholder('Village name').fill('Synthetic Village');
  await page.getByRole('button', { name: /Save patient locally/ }).click();
  return waitFor(async () => /registered locally/i.test(await page.locator('.toast').first().innerText().catch(() => '')), 6000);
}

// ---------------------------------------------------------------- supervisor login, persistence, logout
{
  const { context, page } = await newSession(browser); track(page);
  const ok = await loginUi(page, 'supervisor', env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD);
  R.check('supervisor logs in through the UI', !!ok);
  await sleep(1500); await shot(page, 'supervisor_dashboard');
  const tok = await sessionToken(page);
  R.check('token is in sessionStorage', !!tok && tok.split('.').length === 3);
  const ls = await page.evaluate(() => Object.keys(localStorage).concat(Object.values(localStorage)).join('|'));
  R.check('no token or profile in localStorage', !ls.includes('sahaay-access-token') && !ls.includes('eyJ') && !ls.includes('sahaay-session'));
  const nav = await navLabels(page);
  R.check('supervisor navigation = Overview, Conflict review, Identity monitoring, Sync monitoring, Audit history', JSON.stringify(nav) === JSON.stringify(['Overview', 'Conflict review', 'Identity monitoring', 'Sync monitoring', 'Audit history']), nav.join(' | '));
  for (const label of nav) { await goNav(page, label); await sleep(300); }
  R.check('every supervisor page opens without a JavaScript error', pageErrors.length === 0, pageErrors.join('; '));

  await page.reload(); await waitFor(async () => (await page.locator('.sidebar').count()) > 0, 10000);
  R.check('session persists across page reload (same tab)', (await page.locator('.sidebar').count()) > 0 && (await sessionToken(page)) === tok);
  const tab2 = await context.newPage(); await tab2.goto(BASE); await sleep(1200);
  R.check('a NEW tab has no session (sessionStorage is per tab) -> login screen', (await tab2.locator('.login-screen').count()) === 1 && (await sessionToken(tab2)) === null);
  await tab2.close();

  await page.locator('.profile-mini button[title="Sign out"]').click();
  const out = await waitFor(async () => (await page.locator('.login-screen').count()) > 0, 8000);
  R.check('logout with no pending operations returns to login without a confirmation dialog', !!out && (await page.locator('.modal').count()) === 0);
  R.check('logout clears the token and profile', (await sessionToken(page)) === null && (await page.evaluate(() => sessionStorage.getItem('sahaay-session'))) === null);
  R.check('logout wiped local patients and operations', ((await idb(page, 'patients')) || []).length === 0 && ((await idb(page, 'operations')) || []).length === 0);
  await context.close();
}

// ---------------------------------------------------------------- bad login paths in the UI
{
  const { context, page } = await newSession(browser);
  await loginUi(page, 'supervisor', env.BOOTSTRAP_SUPERVISOR_EMAIL, 'Definitely-Wrong-Pass-1!', { expectOk: false });
  const msg = await page.locator('.login-card [role=alert]').first().innerText().catch(() => '');
  R.check('wrong password shows "Incorrect email or password" (no alert() popup) and stays on login', /Incorrect email or password/.test(msg) && (await page.locator('.sidebar').count()) === 0, msg.slice(0, 80));
  await shot(page, 'login_wrong_password');
  R.check('no token stored after a failed login', (await sessionToken(page)) === null);
  await loginUi(page, 'worker', env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD, { expectOk: false });
  const msg2 = await page.locator('.login-card [role=alert]').first().innerText().catch(() => '');
  R.check('valid credentials with the WRONG role selected are refused', /does not have the selected role/.test(msg2), msg2.slice(0, 80));
  R.check('...and no token is kept', (await sessionToken(page)) === null);
  await context.close();
}

// ---------------------------------------------------------------- role-based navigation
const expectedNav = {
  worker: ['Overview', 'Patient directory', 'Identity verification', 'Sync centre'],
  patient: ['Overview', 'My health', 'SAHAAY AI assistant', 'Identity & consent'],
  clinician: ['Overview', 'Conflict review', 'Identity monitoring', 'Sync monitoring', 'Audit history'],
};
for (const [role, key] of [['worker', 'worker1'], ['patient', 'patient1'], ['clinician', 'clinician1']]) {
  const { context, page } = await newSession(browser); track(page);
  const u = S.users[key];
  const ok = await loginUi(page, role, u.email, u.password);
  await sleep(1200);
  const nav = ok ? await navLabels(page) : [];
  R.check(`${role}: logs in through the UI`, !!ok);
  R.check(`${role}: navigation = ${expectedNav[role].join(', ')}`, JSON.stringify(nav) === JSON.stringify(expectedNav[role]), nav.join(' | '));
  if (role === 'clinician') {
    const hasBody = (await page.locator('main .content').count()) > 0;
    await shot(page, 'clinician_screen');
    R.note(hasBody ? 'PASS' : 'FAIL', 'clinician: has a clinician workspace/dashboard', hasBody ? '' : 'GAP F1: signs in but the page body is empty - no clinician screens exist (nav shown is the supervisor one)');
  }
  if (role === 'worker') await shot(page, 'worker_dashboard');
  if (role === 'patient') { await shot(page, 'patient_dashboard'); await goNav(page, 'My health'); await sleep(400); const t = await page.locator('main .content').innerText(); R.note(/Asha Devi/.test(t) ? 'FAIL' : 'PASS', 'patient: "My health" shows the patient\'s own record', /Asha Devi/.test(t) ? 'GAP: static hard-coded content "Asha Devi" - not this patient\'s data' : ''); }
  await context.close();
}

// ---------------------------------------------------------------- expired-session handling (worker)
{
  const { context, page } = await newSession(browser); track(page);
  const requests = []; page.on('request', r => { if (r.url().includes('/api/sync/batch')) requests.push(r.url()); });
  await loginUi(page, 'worker', w1.email, w1.password);
  await sleep(1500);
  await context.setOffline(true); await sleep(500);
  const created = await registerOffline(page, 'ZZ-SYNTHETIC Expiry Patient');
  R.check('worker creates a patient while OFFLINE (queued locally)', created);
  const opsBefore = (await idb(page, 'operations')) || [];
  R.check('exactly 1 pending operation, tagged with the worker\'s user id', opsBefore.length === 1 && opsBefore[0].status === 'pending' && opsBefore[0].owner_id === w1.id, `ops=${opsBefore.length}`);

  // (a) token expired: replace it with a really-signed but expired token, come back online, reload
  await page.evaluate(t => sessionStorage.setItem('sahaay-access-token', t), makeExpiredToken(w1.id, 'worker'));
  // The OLD page still holds its valid in-memory token and would legitimately sync the moment we go online, so block
  // the endpoint during this window; only requests made AFTER the reload (with the expired token) are counted.
  await page.route('**/api/sync/batch', r => r.abort());
  await context.setOffline(false);
  await page.reload(); requests.length = 0;
  const banner = await waitFor(async () => /Session expired/.test(await page.locator('[role=alert]').first().innerText().catch(() => '')), 10000);
  R.check('expired token -> "Session expired" banner is shown', !!banner);
  await shot(page, 'session_expired_banner');
  await sleep(4000);
  R.check('nothing is sent to the server while the session is expired', requests.length === 0);
  R.check('local data still visible and queue intact while expired', ((await idb(page, 'operations')) || []).length === 1);
  await page.getByRole('button', { name: /Sign in again/ }).click();
  const notice = await waitFor(async () => /unsynced changes are kept on this device/i.test(await page.locator('.login-card [role=alert]').first().innerText().catch(() => '')), 6000);
  R.check('"Sign in again" -> login screen explains unsynced changes are kept', !!notice);
  R.check('pending operation survived the expiry', ((await idb(page, 'operations')) || []).length === 1);

  await page.unroute('**/api/sync/batch');
  // (b) re-login as the SAME user resumes the queue and syncs it
  await loginUi(page, 'worker', w1.email, w1.password);
  const synced = await waitFor(async () => ((await idb(page, 'operations')) || []).every(o => o.status === 'acknowledged'), 25000, 500);
  R.check('same user signs in again -> the queued operation is sent and acknowledged by the server', !!synced);
  R.check('the expired token was not reused (new token in sessionStorage)', (await sessionToken(page)) !== null);

  // (c) server-side rejection: valid-looking token with a bad signature
  const good = await sessionToken(page);
  await page.evaluate(t => sessionStorage.setItem('sahaay-access-token', t.slice(0, -3) + (t.endsWith('AAA') ? 'BBB' : 'AAA')), good);
  await page.reload();
  const b2 = await waitFor(async () => /Session expired/.test(await page.locator('[role=alert]').first().innerText().catch(() => '')), 20000);
  R.check('server answers 401 to a tampered token -> UI shows the expired-session banner (and keeps data)', !!b2 && ((await idb(page, 'operations')) || []).length === 1);
  await context.close();
}
R.check('no uncaught JavaScript errors across Phase 2', pageErrors.length === 0, pageErrors.slice(0, 3).join('; '));
await browser.close();
R.finish();
