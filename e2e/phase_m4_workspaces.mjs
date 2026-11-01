// MILESTONE 4 - the four role workspaces in a REAL browser (Edge) against the real backend + real Atlas, using a DISPOSABLE
// database (E2E_DATABASE=sahaay_test_*) and synthetic data only. Checks the screen, the API effect and the database state.
import { Report, launch, newSession, loginUi, goNav, navLabels, sleep, waitFor, shot, state, readEnv, probe } from './ui.mjs';

if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: E2E_DATABASE must be a disposable sahaay_test_* database'); process.exit(2); }
const R = new Report('phase_m4_workspaces');
const env = readEnv(); const S = state(); const U = S.users; const { P1, P2, names, ids } = S;
const browser = await launch();
let dialogs = 0; const pageErrors = [];
const FAKE = ['Asha Devi', 'Ravi Kumar', 'Meena Sharma', 'SH-2041', 'On track', '04 Nov', 'Demo activity', 'Demo pending', 'synthetic demo data, not a real'];

async function section(title, fn) {
  console.log(`\n--- ${title}`);
  const { context, page } = await newSession(browser);
  page.removeAllListeners('dialog'); page.on('dialog', d => { dialogs++; d.dismiss().catch(() => {}); });
  page.on('pageerror', e => pageErrors.push(String(e).slice(0, 140)));
  try { await fn(page, context); } catch (e) { R.check(`${title}: finished without an unexpected error`, false, String(e).slice(0, 240)); await shot(page, 'm4_error_' + title.replace(/\W+/g, '_')); }
  await context.close();
}
const login = async (page, role, who) => { const ok = who ? await loginUi(page, role, U[who].email, U[who].password) : await loginUi(page, role, env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD); await sleep(1500); return ok; };
const content = async page => (await page.locator('main .content').innerText().catch(() => '')) || '';
const metric = async (page, name) => (await page.locator('.metric-card', { hasText: name }).first().innerText().catch(() => '')).replace(/\s+/g, ' ');
const bell = async page => (await page.locator('.round-action').first().innerText().catch(() => '')).trim();
const toast = page => waitFor(async () => (await page.locator('.toast').first().innerText().catch(() => '')) || false, 8000);
const noFake = text => FAKE.filter(f => text.includes(f));
const closeModal = page => page.locator('.modal-title button').click();
const approvedOk = () => probe('evidence_rows', P1).find(e => e.evidence_reference === 'REG-M4-1');

// ======================================================================= PATIENT (not linked yet)
await section('patient: account not linked yet', async page => {
  R.check('patient1 signs in', !!(await login(page, 'patient', 'patient1')));
  R.check('navigation: Overview, My health, Confirm information, SAHAAY AI assistant, Identity & consent', JSON.stringify(await navLabels(page)) === JSON.stringify(['Overview', 'My health', 'Confirm information', 'SAHAAY AI assistant', 'Identity & consent']));
  const t = await content(page);
  R.check('overview says the account is not linked and shows NO record, appointment or demo data', /not linked to a health record yet/.test(t) && noFake(t).length === 0 && !t.includes(names[P1]), noFake(t).join(','));
  await goNav(page, 'My health'); await sleep(500);
  R.check('"My health" shows the same honest empty state (no hard-coded "Asha Devi")', /not linked/.test(await content(page)) && noFake(await content(page)).length === 0);
  await shot(page, 'm4_patient_unlinked');
});

// ======================================================================= SUPERVISOR: link the patient account
await section('supervisor: patient-account linking', async page => {
  R.check('supervisor signs in', !!(await login(page, 'supervisor')));
  const nav = await navLabels(page);
  R.check('navigation includes "Patient accounts"', nav.includes('Patient accounts'), nav.join(' | '));
  R.check('dashboard counts the unlinked patient account from real data (1) and shows no demo numbers', /Unlinked patient accounts.*\b1\b/.test(await metric(page, 'Unlinked patient accounts')) && noFake(await content(page)).length === 0);
  await goNav(page, 'Patient accounts');
  await waitFor(async () => (await page.locator('tbody tr').count()) >= 2, 10000);
  const row1 = page.locator('tbody tr', { hasText: U.patient1.email }), row2 = page.locator('tbody tr', { hasText: U.patient2.email });
  R.check('list shows patient1 "not linked" and patient2 linked to its record', /not linked/.test(await row1.innerText()) && (await row2.innerText()).includes(P2));
  await shot(page, 'm4_supervisor_accounts');
  await row1.getByRole('button', { name: /Link to a record/ }).click();
  const submit = page.locator('.modal').getByRole('button', { name: /^Link account$/ });
  R.check('submit is disabled until a record, a note and the identity attestation are provided', await submit.isDisabled());
  await page.locator('.modal select').first().selectOption(P2);
  await page.locator('.modal input[required]').first().fill('Checked in person at the clinic');
  const box = page.locator('.modal input[type=checkbox]');
  R.check('attestation checkbox is required (still disabled without it)', await submit.isDisabled());
  await box.check();
  R.check('enabled once everything is provided', await submit.isEnabled());
  await submit.click();
  const refused = await waitFor(async () => /already linked/.test(await page.locator('.modal [role=alert]').innerText().catch(() => '')), 8000);
  R.check('linking patient1 to a record that is ALREADY linked is refused with a clear message', !!refused);
  R.check('database: patient1 account still unlinked', probe('account', U.patient1.email).linked_patient_id === null);
  await page.locator('.modal select').first().selectOption(P1);
  await page.locator('.modal').getByRole('button', { name: /^Link account$/ }).click();
  const linked = await waitFor(() => probe('account', U.patient1.email).linked_patient_id === P1, 10000, 700);
  R.check('linking to the correct record succeeds', !!linked);
  const acc = probe('account', U.patient1.email);
  R.check('database: identity-check method, note and the checking supervisor are recorded', acc.method === 'in_person_document' && acc.has_note && acc.checked_by_supervisor && acc.linked_by);
  R.check('database: audit row PATIENT_ACCOUNT_LINKED written', probe('audit', P1).includes('PATIENT_ACCOUNT_LINKED'));
  await sleep(800);
  R.check('list now shows patient1 linked to its record', (await page.locator('tbody tr', { hasText: U.patient1.email }).innerText()).includes(P1));
  await goNav(page, 'Overview'); await sleep(1200);
  R.check('dashboard "Unlinked patient accounts" drops to 0', /Unlinked patient accounts.*\b0\b/.test(await metric(page, 'Unlinked patient accounts')));
});

// ======================================================================= PATIENT (linked): own record + confirmation
await section('patient: own record and confirmation', async page => {
  await login(page, 'patient', 'patient1');
  const t = await content(page);
  R.check('overview shows the patient\'s OWN record id and "needs your confirmation = 1"', t.includes(P1) && /Needs your confirmation.*1/.test(await metric(page, 'Needs your confirmation')));
  R.check('overview has no hard-coded demo content and none of patient two\'s data', noFake(t).length === 0 && !t.includes(names[P2]) && !t.includes('9555555555'));
  R.check('notification bell shows the real count (1)', (await bell(page)) === '1');
  await goNav(page, 'My health'); await sleep(800);
  const rec = await content(page);
  R.check('"My health" shows the patient\'s real name, phone and condition', rec.includes(names[P1]) && rec.includes('9111111111') && rec.includes('Hypertension'));
  R.check('...and nothing belonging to patient two', !rec.includes(names[P2]) && !rec.includes('9555555555') && !rec.includes('9666666666'));
  await shot(page, 'm4_patient_record');
  await goNav(page, 'Confirm information'); await sleep(800);
  const c = await content(page);
  R.check('Confirm page lists ONLY the patient-verifiable phone conflict (no clinical details)', /Which phone number is correct/.test(c) && !c.includes('Diabetes') && !/condition/i.test(c));
  await page.getByRole('button', { name: /Also reported/ }).click();
  R.check('choosing a value asks for an in-page confirmation (no browser dialog)', (await page.locator('[role=alertdialog]').count()) === 1);
  await page.locator('[role=alertdialog]').getByRole('button', { name: /Yes, this is correct/ }).click();
  const done = await waitFor(async () => /You confirmed 9222222222/.test(await content(page)), 10000);
  R.check('the confirmation is recorded and shown', !!done);
  const conf = probe('confirmations', P1);
  R.check('database: one confirmation by patient1 for 9222222222', conf.length === 1 && conf[0].by_patient1 && conf[0].confirmed_value === '9222222222');
  R.check('database: the record itself is NOT changed by a confirmation (phone still 9111111111)', probe('patient_fields', P1).phone === '9111111111');
  await sleep(800);
  R.check('notification bell count drops to nothing straight away', (await bell(page)) === '');
  await shot(page, 'm4_patient_confirmed');
});

await section('patient two: sees only their own data', async page => {
  await login(page, 'patient', 'patient2');
  await goNav(page, 'My health'); await sleep(800);
  const rec = await content(page);
  R.check('patient two sees their own record', rec.includes(names[P2]) && rec.includes('9555555555'));
  R.check('...and nothing of patient one', !rec.includes(names[P1]) && !rec.includes('9111111111') && !rec.includes('Hypertension'));
  await goNav(page, 'Confirm information'); await sleep(800);
  R.check('their confirmation list holds only their own phone conflict', /Which phone number is correct/.test(await content(page)) && !(await content(page)).includes('9222222222'));
});

// ======================================================================= WORKER: notifications + evidence
await section('worker one: conflict notifications and evidence submission', async page => {
  R.check('worker1 signs in', !!(await login(page, 'worker', 'worker1')));
  R.check('navigation includes "Sync conflicts"', (await navLabels(page)).includes('Sync conflicts'));
  const dash = await content(page);
  R.check('dashboard shows the real open-conflict count (3) and no demo numbers', /Open conflicts.*\b3\b/.test(await metric(page, 'Open conflicts')) && noFake(dash).length === 0 && !dash.includes('2 pending'));
  R.check('notification bell shows 3', (await bell(page)) === '3');
  await goNav(page, 'Sync conflicts');
  await waitFor(async () => (await page.locator('.conflict-card').count()) >= 3, 10000);
  const t = await content(page);
  R.check('Sync conflicts lists their three conflicts on patient one', (await page.locator('.conflict-card').count()) === 3 && ['Age', 'Phone number', 'Condition'].every(f => t.includes(f)) && t.includes(P1));
  R.check('...and none of worker two\'s conflicts (patient two)', !t.includes(P2));
  R.check('only the document conflict offers "Submit evidence"', (await page.getByRole('button', { name: /Submit evidence/ }).count()) === 1);
  await shot(page, 'm4_worker_conflicts');
  await page.getByRole('button', { name: /Submit evidence/ }).click();
  await page.getByLabel(/Document reference/).fill('REG-M4-1');
  await page.getByLabel(/What the document shows/).fill('Register page 12 shows the age');
  await page.getByLabel(/Value the document supports/).fill('41');
  await page.locator('.modal').getByRole('button', { name: /^Submit evidence$/ }).click();
  const ok = await waitFor(async () => /waiting for clinician review/.test(await content(page)), 10000);
  R.check('evidence submitted: the card shows "waiting for clinician review"', !!ok);
  const ev = approvedOk();
  R.check('database: evidence stored pending_review with the claimed value 41, submitted by worker1', ev && ev.status === 'pending_review' && ev.claimed_value === 41 && ev.submitted_by_worker1 && ev.submitted_role === 'worker');
  await page.getByRole('button', { name: /Submit evidence/ }).click();
  await page.getByLabel(/Document reference/).fill('REG-M4-1');
  await page.getByLabel(/What the document shows/).fill('Same reference again');
  await page.getByLabel(/Value the document supports/).fill('41');
  await page.locator('.modal').getByRole('button', { name: /^Submit evidence$/ }).click();
  const dup = await waitFor(async () => /NEW reference/.test(await page.locator('.modal [role=alert]').innerText().catch(() => '')), 8000);
  R.check('re-using the same reference shows a clear message (no crash)', !!dup);
  R.check('database: still exactly one evidence record', probe('evidence_rows', P1).length === 1);
});

await section('worker two: only their own notifications', async page => {
  await login(page, 'worker', 'worker2');
  await goNav(page, 'Sync conflicts');
  await waitFor(async () => (await page.locator('.conflict-card').count()) >= 1, 10000);
  const t = await content(page);
  R.check('worker two sees only the conflict from their own change (patient two)', (await page.locator('.conflict-card').count()) === 1 && t.includes(P2) && !t.includes(P1));
  R.check('a patient-verifiable conflict offers no evidence form', (await page.getByRole('button', { name: /Submit evidence/ }).count()) === 0);
});

// ======================================================================= CLINICIAN
await section('clinician: dashboard, evidence review, resolution, history', async page => {
  R.check('clinician1 signs in', !!(await login(page, 'clinician', 'clinician1')));
  R.check('dedicated navigation: Overview, Conflict review, Evidence review, Resolution history', JSON.stringify(await navLabels(page)) === JSON.stringify(['Overview', 'Conflict review', 'Evidence review', 'Resolution history']));
  const dash = await content(page);
  R.check('dashboard: real counts - 1 clinical conflict, 1 evidence item, 4 open', /Clinical conflicts.*\b1\b/.test(await metric(page, 'Clinical conflicts')) && /Evidence to review.*\b1\b/.test(await metric(page, 'Evidence to review')) && /Total open.*\b4\b/.test(await metric(page, 'Total open')));
  R.check('dashboard is the clinician\'s own (not the supervisor page) and has no demo content', /AUTHORIZED CLINICIAN/i.test(dash) && !/PROFESSIONAL SUPERVISOR/i.test(dash) && noFake(dash).length === 0);
  R.check('bell shows 5 (4 conflicts + 1 evidence item)', (await bell(page)) === '5');
  await shot(page, 'm4_clinician_dashboard');
  await goNav(page, 'Evidence review'); await sleep(1200);
  const q = await content(page);
  R.check('evidence queue shows REG-M4-1 with the claimed value 41', /REG-M4-1/.test(q) && /claimed value\s*41/.test(q));
  await page.getByRole('button', { name: /Review evidence/ }).click();
  await waitFor(async () => /Exact value the evidence supports/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
  await page.getByLabel(/Exact value the evidence supports/).fill('42');
  await page.getByLabel(/Review note/).fill('Register page 12 reads 42');
  await page.getByRole('button', { name: /Approve with this value/ }).click();
  const appr = await waitFor(async () => /approved value: 42/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
  R.check('clinician approves the evidence recording the exact value 42 (claimed was 41)', !!appr);
  const ev = approvedOk();
  R.check('database: evidence approved, approved_value 42, claimed value kept 41', ev.status === 'approved' && ev.approved_value === 42 && ev.claimed_value === 41);
  await closeModal(page); await sleep(1500);
  R.check('evidence queue is now empty', /No evidence is waiting/.test(await content(page)));
  await goNav(page, 'Overview'); await sleep(1500);
  await page.locator('.activity-row', { hasText: 'condition' }).getByRole('button', { name: 'Review' }).click();
  await waitFor(async () => /Clinician decision/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
  await page.getByLabel(/Keep the current value/).check();
  await page.getByRole('button', { name: 'Record decision' }).click();
  await page.locator('[role=alertdialog]').getByRole('button', { name: 'Confirm' }).click();
  const res = await waitFor(() => probe('conflicts_for', P1).find(c => c.field === 'condition').status === 'resolved', 15000, 800);
  R.check('clinician resolves the clinical conflict from the dashboard (keeps the current value)', !!res && probe('patient_fields', P1).condition === 'Hypertension');
  await goNav(page, 'Resolution history'); await sleep(1500);
  const h = await content(page);
  R.check('Resolution history lists the closed conflict with its outcome and value', /Condition/.test(h) && /resolved/.test(h) && /Hypertension/.test(h) && /clinician/.test(h));
  await shot(page, 'm4_clinician_history');
});

// ======================================================================= SUPERVISOR applies approved value + confirmation; others see outcomes
await section('supervisor applies the approved value and the patient confirmation', async page => {
  await login(page, 'supervisor'); await goNav(page, 'Conflict review');
  await waitFor(async () => (await page.locator('.conflict-card').count()) >= 1, 10000);
  for (const [field, btn, expected] of [['age', /Apply approved value/, 42], ['phone', /Apply patient's confirmed value/, '9222222222']]) {
    await page.locator('.conflict-card', { hasText: P1 }).filter({ hasText: field }).first().getByRole('button', { name: /Review case/ }).click();
    await waitFor(async () => /Current value/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
    await page.getByRole('button', { name: btn }).click();
    await page.locator('[role=alertdialog]').getByRole('button', { name: 'Confirm' }).click();
    const ok = await waitFor(() => probe('conflicts_for', P1).find(c => c.field === field).status === 'resolved', 15000, 800);
    R.check(`supervisor applies the ${field} value; database now holds exactly ${expected}`, !!ok && probe('patient_fields', P1)[field] === expected);
    await sleep(600);
  }
});

await section('worker and patient see the outcomes', async page => {
  await login(page, 'worker', 'worker1'); await goNav(page, 'Sync conflicts'); await sleep(1200);
  await page.getByRole('button', { name: /^Closed$/ }).click(); await sleep(500);
  const t = await content(page);
  R.check('worker one\'s Closed tab reports the outcomes (age now 42, phone now 9222222222)', /record now shows 42/.test(t) && /record now shows 9222222222/.test(t));
  R.check('...including the approved evidence', /REG-M4-1/.test(t) && /approved value 42/.test(t));
  R.check('bell for the worker is empty (nothing open)', (await bell(page)) === '' || (await bell(page)) === '0');
});
await section('patient sees the applied confirmation', async page => {
  await login(page, 'patient', 'patient1'); await goNav(page, 'Confirm information'); await sleep(1000);
  R.check('Confirm page lists the closed item as "Applied" with the new value', /Applied/.test(await content(page)) && /9222222222/.test(await content(page)));
  await goNav(page, 'My health'); await sleep(800);
  const t = await content(page);
  R.check('My health shows the updated phone number and real activity ("Your care team updated your phone number")', t.includes('9222222222') && /Your care team updated your phone number/.test(t));
});

R.check('NO browser prompt()/alert()/confirm() dialogs appeared anywhere', dialogs === 0, `dialogs=${dialogs}`);
R.check('no uncaught JavaScript errors', pageErrors.length === 0, pageErrors.slice(0, 3).join('; '));
await browser.close();
R.finish();
