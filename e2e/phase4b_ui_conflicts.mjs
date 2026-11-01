// PHASE 4b - what each role actually SEES and can DO about conflicts in the browser (real backend + Atlas).
import { Report, launch, newSession, loginUi, goNav, sleep, waitFor, shot, state, readEnv, probe, navLabels } from './ui.mjs';

const R = new Report('phase4b_ui_conflicts');
const env = readEnv(); const S = state(); const pid = S.c_patient; const ids = S.c_ids;
const browser = await launch();

// ---------------------------------------------------------------- supervisor: queue + resolve attempts
{
  const { context, page } = await newSession(browser);
  const prompts = []; let answers = ['Diabetes'];
  page.removeAllListeners('dialog');
  page.on('dialog', async d => { prompts.push(`${d.type()}: ${d.message().slice(0, 60)}`); if (d.type() === 'prompt') await d.accept(answers.shift() ?? ''); else await d.dismiss(); });
  await loginUi(page, 'supervisor', env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD); await sleep(1500);
  await goNav(page, 'Conflict review');
  const cards = await waitFor(async () => (await page.locator('.conflict-card').count()) >= 6, 15000);
  R.check('supervisor "Conflict review" lists the pending conflicts from Atlas', !!cards, `cards=${await page.locator('.conflict-card').count()}`);
  const text = await page.locator('.conflict-card').allInnerTexts();
  const mine = text.filter(t => t.includes(pid));
  R.check('all six categories/fields of the synthetic patient are visible with their category badge', mine.length === 6 && ['DOCUMENT_VERIFIABLE', 'PATIENT_VERIFIABLE', 'CRITICAL_CLINICAL', 'REQUIRES_REVIEW'].every(c => mine.some(t => t.includes(c))), `${mine.length} cards`);
  R.check('each card shows both competing values (value A -> value B)', mine.some(t => t.includes('Hypertension') && t.includes('Diabetes')) && mine.some(t => t.includes('10 mg') && t.includes('20 mg')));
  await shot(page, 'supervisor_conflict_queue');
  R.note('FAIL', 'UI shows the evidence / patient-confirmation / audit trail for a conflict', 'GAP: cards show field + two values + one button only; no evidence, no confirmation, no history, no reject');

  // Try to resolve the CRITICAL_CLINICAL (condition) conflict from the UI as the supervisor
  const card = page.locator('.conflict-card', { hasText: pid }).filter({ hasText: 'condition' });
  await card.getByRole('button', { name: /Review case/ }).click();
  await sleep(2500);
  const toast = await page.locator('.toast').first().innerText().catch(() => '(no toast)');
  R.check('UI resolve uses browser prompt() dialogs (no proper form)', prompts.some(p => p.startsWith('prompt')), prompts.join(' | '));
  R.note(/403|clinician/i.test(toast) ? 'PASS' : 'FAIL', 'UI: a supervisor trying to resolve a clinical conflict gets a clear refusal', `toast="${toast.replace(/\s+/g, ' ').slice(0, 110)}"  (the UI sends an invalid "resolution" value, so the server answers 400 before it can answer 403 - bug C2)`);
  const cf = probe('conflicts_for', pid).find(x => x.field === 'condition');
  R.check('database: the clinical conflict is still pending and the patient value unchanged (nothing silently resolved)', cf.status === 'pending');
  await shot(page, 'supervisor_resolve_attempt');
  await context.close();
}

// ---------------------------------------------------------------- worker, clinician, patient views
for (const [role, key, label] of [['worker', 'worker1', 'worker'], ['clinician', 'clinician1', 'clinician'], ['patient', 'patient1', 'patient']]) {
  const { context, page } = await newSession(browser);
  const u = S.users[key];
  await loginUi(page, role, u.email, u.password); await sleep(2000);
  const nav = await navLabels(page);
  const body = (await page.locator('main .content').innerText().catch(() => '')) || '';
  const hasConflictScreen = nav.some(n => /conflict/i.test(n)) && /Conflict review queue/i.test(body);
  const visibleConflicts = /conflict/i.test(body);
  if (role === 'worker') R.note('FAIL', 'worker is notified about conflicts caused by their own sync', `GAP S11: no conflict list/notice on any worker screen (nav: ${nav.join(', ')}); the sync toast only prints a count`);
  if (role === 'clinician') R.note('FAIL', 'clinician can review evidence and resolve conflicts in the UI', 'GAP F1: no clinician screens; only the API works');
  if (role === 'patient') R.note('FAIL', 'patient sees/confirms a conflict about their record', 'GAP C1/X8: no confirmation endpoint or screen; patients cannot read their own record');
  await context.close();
}
await browser.close();
R.finish();
