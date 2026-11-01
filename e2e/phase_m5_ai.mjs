// MILESTONE 5 - the AI features in a REAL browser (Edge) against the real backend + real Atlas (DISPOSABLE database, synthetic data).
// The external AI service is the local TEST DOUBLE e2e/fake_groq.py (no Groq key exists); it records every request it receives, so we can
// prove exactly what the backend would have sent to Groq. Everything else is real.
import fs from 'node:fs';
import path from 'node:path';
import { Report, launch, newSession, loginUi, goNav, sleep, waitFor, shot, state, readEnv, probe, here } from './ui.mjs';

if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: E2E_DATABASE must be a disposable sahaay_test_* database'); process.exit(2); }
const R = new Report('phase_m5_ai');
const env = readEnv(); const S = state(); const U = S.users; const { P1, P2, names, ids } = S;
const LOG = path.join(here, 'results', 'fake_groq_requests.jsonl');
fs.rmSync(LOG, { force: true });
const groq = () => (fs.existsSync(LOG) ? fs.readFileSync(LOG, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l)) : []);
const lastUser = () => { const r = groq().at(-1); return r ? r.messages.at(-1).content : ''; };
const lastSystem = () => { const r = groq().at(-1); return r ? r.messages[0].content : ''; };
const browser = await launch();
let dialogs = 0; const pageErrors = [], providerHits = [], aiBodies = [];

async function section(title, fn) {
  console.log(`\n--- ${title}`);
  const { context, page } = await newSession(browser);
  page.removeAllListeners('dialog'); page.on('dialog', d => { dialogs++; d.dismiss().catch(() => {}); });
  page.on('pageerror', e => pageErrors.push(String(e).slice(0, 140)));
  page.on('request', r => { if (/:8765|groq/i.test(r.url())) providerHits.push(r.url()); });
  page.on('response', async r => { if (/\/api\/(ai|chatbot)/.test(r.url())) aiBodies.push(await r.text().catch(() => '')); });
  try { await fn(page, context); } catch (e) { R.check(`${title}: finished without an unexpected error`, false, String(e).slice(0, 240)); await shot(page, 'm5_error_' + title.replace(/\W+/g, '_')); }
  await context.close();
}
const login = async (page, role, who) => { const ok = who ? await loginUi(page, role, U[who].email, U[who].password) : await loginUi(page, role, env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD); await sleep(1500); return ok; };
const bubbles = page => page.locator('.chat-messages .bubble');
const lastBubble = async page => ((await bubbles(page).last().innerText().catch(() => '')) || '').replace(/\s+/g, ' ');
const input = page => page.getByLabel('Your question');
async function ask(page, text, { wait = true } = {}) {
  const before = await bubbles(page).count();
  await input(page).fill(text); await input(page).press('Enter');
  if (wait) await waitFor(async () => (await bubbles(page).count()) >= before + 2 && !/Thinking/.test(await lastBubble(page)), 15000, 200);
  return lastBubble(page);
}
const badge = async page => ((await page.locator('.page-head .badge').first().innerText().catch(() => '')) || '').trim();

// ======================================================================= PATIENT: assistant
await section('patient one: assistant', async (page, context) => {
  R.check('patient1 signs in', !!(await login(page, 'patient', 'patient1')));
  await goNav(page, 'SAHAAY AI assistant');
  R.check('availability badge comes from the server status ("Assistant available")', !!(await waitFor(async () => /Assistant available/.test(await badge(page)), 8000)));
  R.check('greeting explains the limits (no diagnosis, no medicine advice)', /can't diagnose/.test(await lastBubble(page)));
  R.check('quick prompts are general, not personal medical questions', (await page.locator('.suggestions button').allInnerTexts()).join('|') === 'Explain a health term|Prepare for appointment|Healthy habits');
  await shot(page, 'm5_assistant_start');

  // loading state
  await input(page).fill('[[slow]] What does anemia mean?'); await input(page).press('Enter');
  const thinking = await waitFor(async () => /Thinking/.test(await lastBubble(page)), 3000, 100);
  R.check('while waiting: a "Thinking…" indicator is shown and the input is disabled', !!thinking && (await input(page).isDisabled()));
  await shot(page, 'm5_assistant_loading');
  const answered = await waitFor(async () => /TEST-DOUBLE ANSWER/.test(await lastBubble(page)), 12000, 200);
  const b1 = await lastBubble(page);
  R.check('the real backend answered (text from the test double) and the answer is labelled as AI-generated, not advice', !!answered && /AI-generated general information/.test(b1) && /not medical advice or a diagnosis/.test(b1));
  R.check('input is usable again afterwards', await input(page).isEnabled());

  // language
  await page.locator('.chat-top select').selectOption('hi');
  const hi = await ask(page, 'What is anemia?');
  R.check('Hindi selected: the language instruction reached the AI service and the reply is in that language setting', /\[Hindi \(Devanagari script\)\]/.test(hi) && /Reply entirely in Hindi/.test(lastSystem()));
  await page.reload(); await sleep(1500); await goNav(page, 'SAHAAY AI assistant'); await sleep(800);
  R.check('the language choice is remembered after a reload', (await page.locator('.chat-top select').inputValue()) === 'hi');

  // privacy: identifiers removed before sending
  const pii = await ask(page, 'My Aadhaar is 2345 6789 0123 and phone 9876543210. What is anemia?');
  R.check('the user is told which identifiers were removed before sending', /removed before sending: Aadhaar-like number, phone number/.test(pii));
  R.check('what reached the AI service contains NO Aadhaar-like or phone digits', !/2345|6789|0123|9876543210/.test(JSON.stringify(groq().at(-1).messages)) && /\[Aadhaar-like number removed\]/.test(lastUser()));

  // record summary (consent gated)
  const sumBox = page.locator('[aria-label="AI record summary"]');
  const create = sumBox.getByRole('button', { name: /Create summary/ });
  R.check('record summary: the button stays disabled until the patient agrees', await create.isDisabled());
  R.check('the notice states exactly what is and is not sent', /age range/.test(await sumBox.innerText()) && /never sends your name, phone number, address, patient ID/i.test(await sumBox.innerText()));
  await sumBox.locator('input[type=checkbox]').check();
  const nBefore = groq().length;
  await create.click();
  const sum = await waitFor(async () => /TEST-DOUBLE SUMMARY/.test(await sumBox.innerText()), 12000, 200);
  R.check('after consent the summary is created and lists exactly which details were shared', !!sum && /age range, condition, medications, dosage, last visit date/.test(await sumBox.innerText()));
  const sentSummary = JSON.stringify(groq().at(-1).messages);
  R.check('summary payload: age RANGE + clinical details only', groq().length === nBefore + 1 && /30-39/.test(sentSummary) && /Hypertension/.test(sentSummary) && /Amlodipine/.test(sentSummary) && /5 mg/.test(sentSummary));
  R.check('summary payload: NO name, phone, village, patient ID, identity status or exact age', ![names[P1], '9000000001', 'Synthetic Village', P1, 'UNVERIFIED', '"age": 34', '34'].some(x => sentSummary.includes(x)), sentSummary.slice(0, 200));
  R.check('summary payload: a phone number hidden in a clinical note was removed', /\[phone number removed\]/.test(sentSummary) && !/9876543210/.test(sentSummary));
  const audit = probe('ai_audit', P1);
  R.check('database: the sharing was audited (who, which fields) before sending - without any content', audit.length === 1 && audit[0].actor_role === 'patient' && audit[0].metadata.mode === 'patient_summary' && audit[0].metadata.fields_shared.includes('condition') && !JSON.stringify(audit).includes('Hypertension'));
  await shot(page, 'm5_patient_summary');

  // safety guard + upstream failures
  const dg = await ask(page, '[[diagnose]] please check');
  R.check('diagnosis wording from the AI is replaced by a safe message and the user is told', /can't give a diagnosis/.test(dg) && /replaced because it contained clinical advice/.test(dg) && !/probably have diabetes/.test(dg));
  const e500 = await ask(page, '[[500]] hello');
  R.check('upstream server error: clear message and a "Try again" button (no technical text)', /temporarily unavailable/.test(e500) && (await page.getByRole('button', { name: 'Try again' }).count()) >= 1 && !/500|boom|traceback/i.test(e500));
  const e429 = await ask(page, '[[429]] hello');
  R.check('upstream rate limit: friendly message with the wait time', /busy/.test(e429) && /in 6 seconds/.test(e429) && !/about/.test(e429), e429.slice(0, 120));
  R.check('upstream failures were not shown as answers (no AI label on error bubbles)', !/AI-generated general information/.test(e500 + e429));
  const eight = await ask(page, 'Tell me about drinking enough water.');
  R.check('request 8 of 8 is still answered', /TEST-DOUBLE ANSWER/.test(eight));
  const count = groq().length;
  const limited = await ask(page, 'one more please');
  R.check('request 9 is blocked by OUR rate limit with a clear wait time, and never reaches the AI service', /sending requests too quickly/.test(limited) && /wait \d+ seconds/.test(limited) && !/about/.test(limited) && groq().length === count, limited.slice(0, 140));
  await shot(page, 'm5_rate_limited');

  // emergency
  const em = await ask(page, 'I have chest pain and cannot breathe');
  R.check('an emergency gets a fixed safety message (112, English + Hindi) even while rate-limited, and the AI is not called', /112/.test(em) && /आपातकालीन/.test(em) && /Safety message from SAHAAY/.test(em) && groq().length === count);

  // offline
  await context.setOffline(true); await sleep(800);
  R.check('offline: badge says Offline, the input is disabled and nothing is sent', /Offline/.test(await badge(page)) && (await input(page).isDisabled()) && groq().length === count);
  await sumBox.locator('input[type=checkbox]').check().catch(() => {});
  await sumBox.getByRole('button', { name: /Create summary|Explaining|Writing/ }).click().catch(() => {});
  R.check('offline: the summary button explains the connection is needed instead of failing silently', !!(await waitFor(async () => /offline/i.test(await sumBox.innerText()), 4000, 200)) && groq().length === count);
  await context.setOffline(false); await sleep(800);
  R.check('back online: the assistant is available again', !!(await waitFor(async () => /Assistant available/.test(await badge(page)), 6000)) && (await input(page).isEnabled()));
});

// ======================================================================= PATIENT 2: conflict explanation on their own confirmation
await section('patient two: explain my conflict', async page => {
  await login(page, 'patient', 'patient2'); await goNav(page, 'Confirm information'); await sleep(1200);
  const btn = page.getByRole('button', { name: /Explain this conflict \(AI\)/ });
  R.check('the Confirm page offers an AI explanation', (await btn.count()) === 1);
  const n = groq().length; await btn.click();
  const out = await waitFor(async () => /TEST-DOUBLE EXPLANATION/.test(await page.locator('main').innerText()), 10000, 200);
  R.check('explanation shown with the "cannot approve, reject or resolve anything" notice', !!out && /cannot approve, reject or resolve anything/.test(await page.locator('main').innerText()));
  const sent = JSON.stringify(groq().at(-1).messages);
  R.check('what was sent: NO phone values, NO names, NO patient or conflict ids', groq().length === n + 1 && !['9555555555', '9666666666', P2, names[P2], ids.p2_phone, 'SH-'].some(x => sent.includes(x)), sent.slice(0, 160));
  R.check('what was sent: only the shape of the situation and what the patient may do', /phone number/.test(sent) && /Confirm information/.test(sent) && /Do NOT approve, reject or resolve/.test(sent));
  R.check('database: the conflict is still pending (the AI changed nothing)', probe('conflicts_for', P2).find(c => c.field === 'phone').status === 'pending');
  R.check('database: the explanation request was audited without content', probe('ai_audit', P2).some(a => a.metadata.mode === 'conflict_explanation') && !JSON.stringify(probe('ai_audit', P2)).includes('TEST-DOUBLE'));
  await shot(page, 'm5_patient_explain');
});

// ======================================================================= WORKER
await section('worker one: explain my conflict', async page => {
  await login(page, 'worker', 'worker1'); await goNav(page, 'Sync conflicts'); await sleep(1500);
  const btns = page.getByRole('button', { name: /Explain this conflict \(AI\)/ });
  R.check('each conflict card offers an explanation (their own two conflicts)', !!(await waitFor(async () => (await btns.count()) === 2, 10000, 300)));
  const n = groq().length; await btns.first().click();
  const ok = await waitFor(async () => /TEST-DOUBLE EXPLANATION/.test(await page.locator('main').innerText()), 10000, 200);
  const sent = JSON.stringify(groq().at(-1).messages);
  R.check('worker gets an explanation; nothing identifying or any values were sent', !!ok && groq().length === n + 1 && !['9111111111', '9222222222', 'Hypertension', 'Diabetes', P1, names[P1]].some(x => sent.includes(x)));
  R.check('the worker is told what they may do (and that reviewers decide)', /Nothing is needed from you|submit a reference/.test(sent));
});

// ======================================================================= CLINICIAN + SUPERVISOR: dialog
await section('clinician: explanation and record summary in the conflict dialog', async page => {
  await login(page, 'clinician', 'clinician1'); await goNav(page, 'Conflict review'); await sleep(1500);
  await page.locator('.conflict-card', { hasText: P1 }).filter({ hasText: 'condition' }).getByRole('button', { name: /Review case/ }).click();
  await waitFor(async () => /Current value/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
  const m = page.locator('.modal');
  await m.locator('[aria-label="Explanation language"]').selectOption('ta');
  await m.getByRole('button', { name: /Explain this conflict \(AI\)/ }).click();
  const ok = await waitFor(async () => /TEST-DOUBLE EXPLANATION/.test(await m.innerText()), 10000, 200);
  R.check('clinician: explanation in the chosen language (Tamil reached the service)', !!ok && /Reply entirely in Tamil/.test(lastSystem()));
  R.check('clinician: facts say they can review and decide, and that stale decisions are refused', /review evidence and record a decision/.test(lastUser()) && /out-of-date/.test(lastUser()));
  const sumBox = m.locator('[aria-label="AI record summary"]');
  R.check('clinician: the record summary section is available, consent-gated', (await sumBox.count()) === 1 && (await sumBox.getByRole('button', { name: /Create summary/ }).isDisabled()));
  await sumBox.locator('input[type=checkbox]').check(); await sumBox.getByRole('button', { name: /Create summary/ }).click();
  const sum = await waitFor(async () => /TEST-DOUBLE SUMMARY/.test(await m.innerText()), 10000, 200);
  const sent = JSON.stringify(groq().at(-1).messages);
  R.check('clinician summary: clinical details only - no name, phone, village or patient id', !!sum && /Hypertension/.test(sent) && ![names[P1], '9000000001', 'Synthetic Village', P1].some(x => sent.includes(x)));
  R.check('clinician summary prompt addresses the clinician and forbids filling gaps', /for the clinician/.test(lastSystem()) && /Use ONLY the facts/.test(lastSystem()));
  R.check('every AI-written text in the dialog is labelled as AI-generated, not a diagnosis', /not a diagnosis/.test(await m.innerText()) && /AI-generated explanation/.test(await m.innerText()));
  await shot(page, 'm5_clinician_dialog');
  R.check('the dialog still offers the existing decision controls (M3 unchanged) and nothing was decided by the AI', /Clinician decision/.test(await m.innerText()) && probe('conflicts_for', P1).find(c => c.field === 'condition').status === 'pending');
  const rows = probe('ai_audit', P1);
  R.check('database: clinician explanation + summary audited with the clinician as actor', rows.filter(r => r.actor_role === 'clinician').length === 2);
});
await section('supervisor: explanation only (no record summary)', async page => {
  await login(page, 'supervisor'); await goNav(page, 'Conflict review'); await sleep(1500);
  await page.locator('.conflict-card', { hasText: P1 }).filter({ hasText: 'condition' }).getByRole('button', { name: /Review case/ }).click();
  await waitFor(async () => /Current value/.test(await page.locator('.modal').innerText().catch(() => '')), 10000);
  const m = page.locator('.modal');
  R.check('supervisor: the AI record summary is NOT offered (clinician/patient only)', (await m.locator('[aria-label="AI record summary"]').count()) === 0);
  await m.getByRole('button', { name: /Explain this conflict \(AI\)/ }).click();
  const ok = await waitFor(async () => /TEST-DOUBLE EXPLANATION/.test(await m.innerText()), 10000, 200);
  R.check('supervisor: explanation says only a clinician can resolve this (from server policy facts)', !!ok && /cannot resolve this; only a clinician can/.test(lastUser()));
  R.check('database: still pending after every AI action in this run', probe('conflicts_for', P1).every(c => c.status === 'pending'));
});

// ======================================================================= cross-cutting
R.check('the browser never contacted the AI provider or the test double directly', providerHits.length === 0, providerHits.join(','));
R.check('no AI response body ever contained the API key', !aiBodies.some(b => /test-double-key/.test(b)) && aiBodies.length > 10, `${aiBodies.length} AI responses inspected`);
R.check('the request that reached the AI service carried the key only server-to-server (header present on every call)', groq().length > 10 && groq().every(r => r.auth_prefix.startsWith('Bearer test-do')));
R.check('database: nothing the user typed or the AI wrote was stored anywhere', Object.keys(probe('scan', 'TEST-DOUBLE')).length === 0 && Object.keys(probe('scan', 'drinking enough water')).length === 0 && Object.keys(probe('scan', 'one more please')).length === 0);
R.check('NO browser prompt()/alert()/confirm() dialogs appeared', dialogs === 0, `dialogs=${dialogs}`);
R.check('no uncaught JavaScript errors', pageErrors.length === 0, pageErrors.slice(0, 3).join('; '));
await browser.close();
R.finish();
