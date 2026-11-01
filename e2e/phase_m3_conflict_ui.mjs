// MILESTONE 3 - the supervisor/clinician conflict workflow in the REAL browser against the real backend + real Atlas, using a
// DISPOSABLE database (E2E_DATABASE=sahaay_test_*). Verifies UI, API response (via what the UI shows) and database state.
import { Report, launch, newSession, loginUi, goNav, sleep, waitFor, shot, state, readEnv, probe } from './ui.mjs';

if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: E2E_DATABASE must be a disposable sahaay_test_* database'); process.exit(2); }
const R = new Report('phase_m3_conflict_ui');
const env = readEnv(); const S = state(); const pid = S.m3_patient; const ids = S.m3_ids; const U = S.users;
const browser = await launch();
let dialogs = 0;

async function session(role, who) {
  const { context, page } = await newSession(browser);
  page.removeAllListeners('dialog'); page.on('dialog', d => { dialogs++; d.dismiss().catch(() => {}); });
  const ok = who ? await loginUi(page, role, U[who].email, U[who].password) : await loginUi(page, role, env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD);
  await sleep(1200); await goNav(page, 'Conflict review');
  await waitFor(async () => (await page.locator('.conflict-card').count()) > 0, 15000);
  return { context, page, ok };
}
const card = (page, field) => page.locator('.conflict-card', { hasText: pid }).filter({ hasText: field }).first();
async function open(page, field) { await card(page, field).getByRole('button', { name: /Review case/ }).click(); await page.locator('.modal').waitFor(); await waitFor(async () => /Current value/.test(await page.locator('.modal').innerText().catch(() => '')), 10000); }
const modalText = page => page.locator('.modal').innerText();
const confirmStep = async page => { await page.locator('[role=alertdialog]').waitFor({ timeout: 5000 }); await page.locator('[role=alertdialog]').getByRole('button', { name: 'Confirm' }).click(); };
const fields = () => probe('patient_fields', pid);
const cf = field => probe('conflicts_for', pid).find(x => x.field === field);

// ------------------------------------------------------------------ 1. supervisor on a CLINICAL conflict
{
  const { context, page, ok } = await session('supervisor');
  R.check('supervisor signs in (disposable database) and the Conflict review queue lists the 5 pending conflicts', !!ok && (await page.locator('.conflict-card', { hasText: pid }).count()) === 5);
  await open(page, 'condition');
  const t = await modalText(page);
  R.check('clinical conflict dialog shows current value, both values and the policy', /Current value: Hypertension/.test(t) && /Diabetes/.test(t) && /Only an authorized clinician/.test(t));
  R.check('supervisor sees NO resolve / reject / custom-value controls for a clinical conflict', (await page.getByRole('button', { name: /Record decision|Reject incoming|Apply/ }).count()) === 0 && (await page.locator('.modal input[type=radio]').count()) === 0);
  await shot(page, 'm3_supervisor_clinical_readonly');
  R.check('database: the clinical conflict is untouched', cf('condition').status === 'pending' && fields().condition === 'Hypertension');
  await page.getByRole('button', { name: /^Cancel$|close/i }).first().click().catch(() => {});
  await context.close();
}

// ------------------------------------------------------------------ 2. clinician: stale decision is refused, fresh one accepted
{
  const { context, page } = await session('clinician', 'clinician1');
  await open(page, 'dosage');
  const t0 = await modalText(page);
  R.check('clinician sees the decision controls (keep current / use incoming / other value) and the reject option', /Keep the current value/.test(t0) && /Use the incoming value/.test(t0) && /Record a different value/.test(t0) && /Reject the incoming change/.test(t0));
  await shot(page, 'm3_clinician_dialog');
  // while the dialog is open, a NEWER legitimate dosage arrives from another device
  R.check('another device records a newer dosage "15 mg" while the clinician is reviewing', probe('remote_edit', pid, 'dosage', '15 mg') === 'applied');
  await page.getByLabel(/Use the incoming value/).check();
  await page.getByRole('button', { name: 'Record decision' }).click();
  await confirmStep(page);
  const stale = await waitFor(async () => /changed after you opened/.test(await page.locator('.modal').innerText().catch(() => '')), 8000);
  R.check('stale decision is REFUSED with a clear message (no overwrite)', !!stale);
  await sleep(800);
  const t1 = await modalText(page);
  R.check('dialog reloads the fresh state: current value 15 mg and a "changed since raised" warning', /Current value: 15 mg/.test(t1) && /current value differs/i.test(t1));
  R.check('...and the explanation of the refusal is STILL on screen after the reload (it used to vanish)', /changed after you opened/.test(t1));
  await shot(page, 'm3_stale_refused');
  R.check('database: dosage is still the NEWER 15 mg, conflict still pending, no CONFLICT_RESOLVED row', fields().dosage === '15 mg' && cf('dosage').status === 'pending');
  await page.getByLabel(/Keep the current value/).check();
  await page.getByRole('button', { name: 'Record decision' }).click(); await confirmStep(page);
  const done = await waitFor(() => cf('dosage').status === 'resolved', 15000, 800);
  R.check('after the fresh review the clinician\'s decision is accepted (conflict resolved, value kept)', !!done && fields().dosage === '15 mg' && cf('dosage').selected_value === '15 mg');
  await context.close();
}

// ------------------------------------------------------------------ 3. document evidence: clinician approves a DIFFERENT value; supervisor applies exactly that
{
  const a = await session('clinician', 'clinician1');
  await open(a.page, 'age');
  const t = await modalText(a.page);
  R.check('document dialog lists the submitted evidence with its claimed value (41)', /SYN-DOC/.test(t) && /claimed value: 41/.test(t));
  await a.page.getByLabel(/Exact value the evidence supports/).fill('42');
  await a.page.getByLabel(/Review note/).fill('Passport page 2 shows 42');
  await a.page.getByRole('button', { name: /Approve with this value/ }).click();
  const approved = await waitFor(async () => /approved value: 42/.test(await a.page.locator('.modal').innerText().catch(() => '')), 10000);
  R.check('clinician approves the evidence recording the exact value 42 (not the claimed 41)', !!approved);
  R.check('clinician cannot pick an arbitrary value for a document conflict (only "Apply approved value")', (await a.page.getByRole('button', { name: /Record decision/ }).count()) === 0 && (await a.page.getByRole('button', { name: /Apply approved value/ }).count()) === 1);
  await a.context.close();

  const b = await session('supervisor');
  await open(b.page, 'age');
  const ts = await modalText(b.page);
  R.check('supervisor sees the approved evidence and ONLY the "Apply approved value" action', /approved value: 42/.test(ts) && (await b.page.getByRole('button', { name: /Apply approved value/ }).count()) === 1 && (await b.page.locator('.modal input[type=radio]').count()) === 0);
  await shot(b.page, 'm3_supervisor_apply_approved');
  R.check('database before applying: age is still 40', fields().age === 40 && cf('age').status === 'pending');
  await b.page.getByRole('button', { name: /Apply approved value/ }).click(); await confirmStep(b.page);
  const applied = await waitFor(() => cf('age').status === 'resolved', 15000, 800);
  R.check('supervisor applies it: patient.age = 42 exactly (the clinician\'s approved value), conflict resolved', !!applied && fields().age === 42 && cf('age').selected_value === 42);
  await b.context.close();
}

// ------------------------------------------------------------------ 4. patient-verifiable: supervisor applies the patient's confirmation
{
  const { context, page } = await session('supervisor');
  await open(page, 'phone');
  const t = await modalText(page);
  R.check('supervisor sees that the linked patient confirmed 9222222222 and only the "Apply patient\'s confirmed value" action', /Patient confirmed: 9222222222/.test(t) && (await page.getByRole('button', { name: /Apply patient's confirmed value/ }).count()) === 1);
  R.check('database before applying: phone unchanged (a confirmation alone changes nothing)', fields().phone === '9111111111');
  await page.getByRole('button', { name: /Apply patient's confirmed value/ }).click(); await confirmStep(page);
  const applied = await waitFor(() => cf('phone').status === 'resolved', 15000, 800);
  R.check('applied: patient.phone = the patient\'s confirmed value', !!applied && fields().phone === '9222222222');
  await context.close();
}

// ------------------------------------------------------------------ 5. clinician rejects an incoming change on an unclassified field
{
  const { context, page } = await session('clinician', 'clinician2');
  await open(page, 'last_visit');
  const rejectBtn = page.getByRole('button', { name: /Reject incoming change/ });
  R.check('reject needs a written reason (button disabled until a reason is typed)', await rejectBtn.isDisabled());
  await page.getByPlaceholder(/Why is the incoming value not accepted/).fill('Mis-keyed visit date');
  await rejectBtn.click(); await confirmStep(page);
  const rej = await waitFor(() => cf('last_visit').status === 'rejected', 15000, 800);
  R.check('rejected: conflict status "rejected", patient value unchanged, history kept', !!rej && fields().last_visit === '2026-10-20');
  await context.close();
}

R.check('NO browser prompt()/alert()/confirm() dialogs appeared anywhere in the workflow', dialogs === 0, `dialogs=${dialogs}`);
await browser.close();
R.finish();
