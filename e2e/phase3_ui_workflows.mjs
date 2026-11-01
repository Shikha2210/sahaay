// PHASE 3 - real workflows in the browser against the real backend + Atlas. Synthetic data only (ZZ-SYNTHETIC ...).
import { Report, launch, newSession, loginUi, goNav, idb, sessionToken, sleep, waitFor, shot, state, probe, registerPatientUi, syncNow, opByName, clickSignOut, BASE } from './ui.mjs';

const R = new Report('phase3_ui_workflows');
const S = state(); const w1 = S.users.worker1, w2 = S.users.worker2;
const tag = `${S.run}-${Math.random().toString(16).slice(2, 6)}`;
const N = label => `ZZ-SYNTHETIC ${label} ${tag}`;
const browser = await launch();
const pageErrors = [];

async function scenario(title, fn) {
  if (process.env.ONLY && !title.includes(process.env.ONLY)) return;   // e.g. ONLY="worker flows"
  console.log(`\n--- ${title}`);
  const { context, page } = await newSession(browser);
  page.on('pageerror', e => pageErrors.push(String(e).slice(0, 140)));
  try { await fn(context, page); } catch (e) { R.check(`${title}: scenario completed without an unexpected error`, false, String(e).slice(0, 220)); await shot(page, 'error_' + title.replace(/\W+/g, '_')); }
  await context.close();
}
const atlas = name => probe('patients_named', name);
const allAck = async page => ((await idb(page, 'operations')) || []).length > 0 && ((await idb(page, 'operations')) || []).every(o => o.status === 'acknowledged');
async function holdSyncAndLeave(context, page) {
  // Simulates closing the tab with unsynced work: block uploads, come back online, wipe the tab's session, reload.
  await page.route('**/api/sync/batch', r => r.abort());
  await context.setOffline(false); await sleep(1500);
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await page.unroute('**/api/sync/batch');
  return waitFor(async () => (await page.locator('.login-screen').count()) > 0, 10000);
}

// ======================================================================= A+B+C+D: online / offline / reconnect / pull / lost ack
await scenario('worker flows', async (context, page) => {
  await loginUi(page, 'worker', w1.email, w1.password); await sleep(1500);

  // A. patient created while ONLINE
  const nameA = N('Online Patient');
  R.check('A: worker registers a patient while online', await registerPatientUi(page, nameA));
  await syncNow(page);
  const rowA = await waitFor(() => { const r = atlas(nameA); return r.length === 1 ? r : false; }, 30000, 1500);
  R.check('A: patient reached MongoDB Atlas after sync', !!rowA);
  const pA = rowA && rowA[0];
  R.check('A: stored UNVERIFIED, version 1, created_by = worker1', pA && pA.identity_status === 'UNVERIFIED' && pA.version === 1 && pA.created_by === w1.id);
  R.check('A: server audit has PATIENT_CREATED_SYNC; 1 receipt', pA && probe('audit', pA.patient_id).includes('PATIENT_CREATED_SYNC') && probe('receipts', pA.patient_id) === 1);
  R.check('A: local operation marked acknowledged only after the server answered', await waitFor(async () => (await opByName(page, nameA))?.status === 'acknowledged', 10000));

  // B. patient created while OFFLINE, then reconnect
  await context.setOffline(true);
  await waitFor(async () => /Offline/.test(await page.locator('.connection-pill').innerText()), 6000);
  const nameB = N('Offline Patient');
  R.check('B: worker registers a patient while OFFLINE', await registerPatientUi(page, nameB));
  const opB = await opByName(page, nameB);
  R.check('B: operation is queued locally as pending (persisted in IndexedDB)', opB && opB.status === 'pending' && opB.owner_id === w1.id && opB.base_version === 0 && opB.changes.__create__ === true);
  R.check('B: NOT in MongoDB while offline', atlas(nameB).length === 0);
  await goNav(page, 'Sync centre'); await sleep(500);
  const rowText = (await page.locator('table tbody tr').allInnerTexts()).join('\n');
  R.check('B: Sync centre lists the pending operation', /pending/.test(rowText));
  await shot(page, 'sync_centre_offline_pending');
  await context.setOffline(false);
  const okB = await waitFor(async () => (await opByName(page, nameB))?.status === 'acknowledged', 40000, 1000);
  R.check('B: after reconnect the queue syncs automatically (acknowledged)', !!okB);
  const rowB = atlas(nameB);
  R.check('B: patient now in Atlas, exactly once, created_by worker1', rowB.length === 1 && rowB[0].created_by === w1.id && probe('receipts', rowB[0].patient_id) === 1);

  // C. pull feed updates the frontend (GET /api/patients blocked so ONLY /api/sync/pull can deliver the change)
  await page.route('**/api/patients', r => r.abort());
  const cursor0 = (await idb(page, 'sync_state')).find(r => r.key === 'global_cursor')?.value ?? 0;
  const edit = probe('remote_edit', pA.patient_id, 'village', 'Pulled-From-Second-Device');
  R.check('C: a second device (worker2) edits the patient through the API', edit === 'applied', String(edit));
  await syncNow(page);
  const pulled = await waitFor(async () => ((await idb(page, 'patients')) || []).find(p => p.patient_id === pA.patient_id)?.village === 'Pulled-From-Second-Device', 25000, 1000);
  R.check('C: pull feed delivered the other device\'s change into the browser\'s local DB', !!pulled);
  const cursor1 = (await idb(page, 'sync_state')).find(r => r.key === 'global_cursor')?.value ?? 0;
  R.check('C: local pull cursor advanced', cursor1 > cursor0, `${cursor0} -> ${cursor1}`);
  await goNav(page, 'Patient directory'); await sleep(500);
  R.check('C: directory screen shows the pulled value', /Pulled-From-Second-Device/.test(await page.locator('table').innerText()));
  await page.unroute('**/api/patients');

  // D. lost acknowledgement: server applies the batch, the response never reaches the browser
  const nameD = N('Lost-Ack Patient');
  // While the trap is active EVERY upload (manual click or the app's own 15-second timer, even two at once) is
  // forwarded to the real server and then its response is dropped, so the browser can never see an acknowledgement.
  let trapping = true, trapped = 0;
  await page.route('**/api/sync/batch', async route => { if (trapping) { trapped++; await (await route.fetch()).body(); await route.abort('failed'); } else await route.continue(); });
  await registerPatientUi(page, nameD);
  await syncNow(page);
  const applied = await waitFor(() => atlas(nameD).length === 1, 25000, 1000);
  R.check('D: server DID apply the operation even though the browser never saw the answer', !!applied);
  const opD1 = await waitFor(async () => { const o = await opByName(page, nameD); return o && o.status === 'pending' && o.attempts >= 1 ? o : false; }, 10000);
  R.check('D: browser keeps the operation pending (never assumes success), counts the attempt, records the error and schedules a retry', !!opD1 && opD1.status === 'pending' && opD1.attempts >= 1 && !!opD1.last_error && opD1.next_retry_at > 0, opD1 ? `attempts=${opD1.attempts}, error="${opD1.last_error}", trapped uploads=${trapped}` : '');
  if (trapped > 1) R.note('NOTE', 'D: two uploads of the same operation were in flight at once (manual "Sync now" + auto-sync timer)', 'no in-tab sync mutex (gap S9); harmless here because the server is idempotent - Atlas ends with one record');
  trapping = false;
  await waitFor(async () => { const o = await opByName(page, nameD); return o && o.next_retry_at <= Date.now(); }, 15000, 500);
  await syncNow(page);
  const opD2 = await waitFor(async () => (await opByName(page, nameD))?.status === 'acknowledged' ? await opByName(page, nameD) : false, 25000, 1000);
  R.check('D: retry -> server answers "duplicate" -> acknowledged', !!opD2 && opD2.server_response.status === 'duplicate', opD2 ? opD2.server_response.status : '');
  const rowD = atlas(nameD);
  R.check('D: Atlas still has exactly ONE patient and ONE receipt (no duplicate record)', rowD.length === 1 && probe('receipts', rowD[0].patient_id) === 1);
  await page.unroute('**/api/sync/batch');
});

// ======================================================================= E: network failure + retry back-off
await scenario('network failure and retry', async (context, page) => {
  await loginUi(page, 'worker', w1.email, w1.password); await sleep(1500);
  const name = N('Retry Patient');
  await registerPatientUi(page, name);
  await page.route('**/api/sync/batch', r => r.abort('failed'));
  await syncNow(page);
  const failed = await waitFor(async () => { const o = await opByName(page, name); return o && o.attempts >= 1 && o.last_error ? o : false; }, 15000);
  R.check('E: failed upload -> operation stays pending, attempts counted, error recorded, back-off scheduled', !!failed && failed.status === 'pending' && failed.next_retry_at > Date.now() - 1000, failed ? `attempts=${failed.attempts}, error="${failed.last_error}"` : '');
  R.check('E: nothing reached Atlas', atlas(name).length === 0);
  await page.unroute('**/api/sync/batch');
  await sleep(4500); await syncNow(page);
  R.check('E: after the network returns the retry succeeds', !!(await waitFor(async () => (await opByName(page, name))?.status === 'acknowledged', 25000, 1000)));
  R.check('E: patient in Atlas exactly once', atlas(name).length === 1);
});

// ======================================================================= F: server-rejected operation
await scenario('rejected operation', async (context, page) => {
  await loginUi(page, 'worker', w1.email, w1.password); await sleep(1500);
  const longName = 'ZZ-SYNTHETIC ' + 'R'.repeat(130);
  await registerPatientUi(page, longName);
  await syncNow(page);
  const rej = await waitFor(async () => { const o = await opByName(page, longName); return o?.status === 'rejected' ? o : false; }, 20000, 1000);
  R.check('F: server rejects an invalid operation (130-char name) and the client records it as rejected', !!rej && /invalid_value/.test(rej.last_error || ''), rej ? rej.last_error : '');
  R.check('F: nothing stored in Atlas for it', atlas(longName).length === 0);
  await goNav(page, 'Sync centre'); await sleep(500);
  const badge = await page.locator('table tbody tr', { hasText: 'rejected' }).first().locator('.badge').getAttribute('class').catch(() => 'none');
  R.note(/amber|red/.test(badge) ? 'PASS' : 'FAIL', 'F: Sync centre visibly flags a rejected operation as a problem', `badge class="${badge}" - rejected ops are shown like successes; there is no failed-operations list or retry/discard action (gap S5)`);
  await clickSignOut(page);
  const dlg = await waitFor(async () => (await page.locator('.modal').count()) > 0, 5000);
  const txt = dlg ? await page.locator('.modal').innerText() : '';
  R.check('F: signing out counts the rejected (never-synced) change and warns before discarding', !!dlg && /1 change/.test(txt) && /permanently remove/.test(txt), txt.replace(/\s+/g, ' ').slice(0, 90));
  await shot(page, 'logout_confirmation_dialog');
  const discardBtn = page.getByRole('button', { name: /Discard changes and sign out/ });
  R.check('F: "Discard" button is disabled until the user ticks the confirmation checkbox', await discardBtn.isDisabled());
  await page.getByRole('button', { name: 'Cancel' }).click();
  R.check('F: Cancel keeps the user signed in and the change intact', (await page.locator('.sidebar').count()) === 1 && ((await idb(page, 'operations')) || []).length === 1);
});

// ======================================================================= G: user switching / ownership protection
await scenario('user switching and ownership', async (context, page) => {
  await loginUi(page, 'worker', w1.email, w1.password); await sleep(1500);
  await context.setOffline(true);
  const name = N('Ownership Patient');
  await registerPatientUi(page, name);
  await clickSignOut(page);
  R.check('G: logout with 1 unsynced change shows the warning dialog', !!(await waitFor(async () => /1 change/.test(await page.locator('.modal').innerText().catch(() => '')), 5000)));
  await page.getByRole('button', { name: 'Cancel' }).click();
  R.check('G: worker1 still signed in after Cancel', (await page.locator('.sidebar').count()) === 1);
  const left = await holdSyncAndLeave(context, page);
  R.check('G: tab "closed" with unsynced work -> next visitor sees the login screen', !!left && (await sessionToken(page)) === null);
  R.check('G: worker1\'s operation is still on the device, tagged worker1', (await opByName(page, name))?.owner_id === w1.id);

  await loginUi(page, 'worker', w2.email, w2.password);
  const blocked = await waitFor(async () => /Another user's changes are on this device/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
  R.check('G: worker2 signing in is BLOCKED with a clear dialog about worker1\'s unsynced change', !!blocked);
  await shot(page, 'user_switch_blocked_dialog');
  R.check('G: no session was opened for worker2 (no token stored, no workspace)', (await sessionToken(page)) === null && (await page.locator('.sidebar').count()) === 0);
  R.check('G: worker1\'s change untouched; device still owned by worker1', ((await idb(page, 'operations')) || []).length === 1 && (await idb(page, 'sync_state')).find(r => r.key === 'device_owner')?.value === w1.id);
  R.check('G: worker1\'s change was NOT uploaded under worker2', atlas(name).length === 0);
  R.check('G: "Discard and continue" is disabled until the checkbox is ticked', await page.getByRole('button', { name: /Discard and continue/ }).isDisabled());
  await page.getByRole('button', { name: 'Cancel' }).click();

  await loginUi(page, 'worker', w1.email, w1.password);
  const synced = await waitFor(async () => (await opByName(page, name))?.status === 'acknowledged', 40000, 1000);
  R.check('G: worker1 signs back in -> own queued change syncs', !!synced);
  const row = atlas(name);
  R.check('G: Atlas record created_by worker1 (never worker2)', row.length === 1 && row[0].created_by === w1.id);
  await clickSignOut(page);
  R.check('G: worker1 logs out cleanly (nothing pending -> no dialog)', !!(await waitFor(async () => (await page.locator('.login-screen').count()) > 0, 8000)) && (await page.locator('.modal').count()) === 0);
  await loginUi(page, 'worker', w2.email, w2.password);
  R.check('G: worker2 then signs in normally', !!(await waitFor(async () => (await page.locator('.sidebar').count()) > 0, 15000)) && (await page.locator('.modal').count()) === 0);
  await sleep(1500);
  const ops = (await idb(page, 'operations')) || [];
  R.check('G: worker2 sees none of worker1\'s operations on this device', ops.length === 0);
  R.check('G: device is now owned by worker2', (await idb(page, 'sync_state')).find(r => r.key === 'device_owner')?.value === w2.id);
});

// ======================================================================= H: explicit discard
await scenario('explicit discard of another user\'s changes', async (context, page) => {
  await loginUi(page, 'worker', w1.email, w1.password); await sleep(1500);
  await context.setOffline(true);
  const name = N('Discard Patient');
  await registerPatientUi(page, name);
  await holdSyncAndLeave(context, page);
  await loginUi(page, 'worker', w2.email, w2.password);
  await waitFor(async () => /Another user's changes/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
  await page.locator('.modal input[type=checkbox]').check();
  await page.getByRole('button', { name: /Discard and continue/ }).click();
  const inside = await waitFor(async () => (await page.locator('.sidebar').count()) > 0, 15000);
  R.check('H: after explicit confirmation worker2 can continue', !!inside);
  await sleep(1500);
  R.check('H: the discarded change is gone from the device and was never sent to Atlas', ((await idb(page, 'operations')) || []).length === 0 && atlas(name).length === 0);
});

R.check('no uncaught JavaScript errors across Phase 3', pageErrors.length === 0, pageErrors.slice(0, 3).join('; '));
await browser.close();
R.finish();
