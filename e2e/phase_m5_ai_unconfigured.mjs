// MILESTONE 5 - what users see when the backend has NO Groq key (the real state of this project today). Run against a backend started
// WITHOUT GROQ_API_KEY, on the same disposable database as phase_m5_ai.mjs. Nothing may be sent anywhere or audited.
import fs from 'node:fs';
import path from 'node:path';
import { Report, launch, newSession, loginUi, goNav, sleep, waitFor, shot, state, readEnv, probe, here } from './ui.mjs';

if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: E2E_DATABASE must be a disposable sahaay_test_* database'); process.exit(2); }
const R = new Report('phase_m5_ai_unconfigured');
const env = readEnv(); const S = state(); const U = S.users; const { P1, ids } = S;
const LOG = path.join(here, 'results', 'fake_groq_requests.jsonl');
fs.rmSync(LOG, { force: true });
const sentToService = () => (fs.existsSync(LOG) ? fs.readFileSync(LOG, 'utf8').split('\n').filter(Boolean).length : 0);
const auditBefore = probe('ai_audit', P1).length;
const browser = await launch(); let dialogs = 0;
const { context, page } = await newSession(browser);
page.removeAllListeners('dialog'); page.on('dialog', d => { dialogs++; d.dismiss().catch(() => {}); });
const bubble = async () => ((await page.locator('.chat-messages .bubble').last().innerText().catch(() => '')) || '').replace(/\s+/g, ' ');

await loginUi(page, 'patient', U.patient1.email, U.patient1.password); await sleep(1500);
await goNav(page, 'SAHAAY AI assistant');
const badge = async () => ((await page.locator('.page-head .badge').first().innerText().catch(() => '')) || '').trim();
R.check('badge honestly says the assistant is not set up (it is no longer a hard-coded "Assistant available")', !!(await waitFor(async () => /Not set up on this server/.test(await badge()), 8000)));
await page.getByLabel('Your question').fill('What does anemia mean?'); await page.getByLabel('Your question').press('Enter');
await waitFor(async () => /not set up/.test(await bubble()), 8000, 200);
const b = await bubble();
R.check('asking shows a clear "not set up on this server yet" message', /not set up on this server yet/.test(b) && /administrator/.test(b));
R.check('no pointless "Try again" button for a configuration problem, and no technical text', (await page.getByRole('button', { name: 'Try again' }).count()) === 0 && !/503|ai_not_configured|traceback/i.test(b));
await page.getByLabel('Your question').fill('I have chest pain'); await page.getByLabel('Your question').press('Enter');
await waitFor(async () => /112/.test(await bubble()), 8000, 200);
R.check('emergency guidance still works without any AI configured (fixed message, English + Hindi)', /112/.test(await bubble()) && /आपातकालीन/.test(await bubble()));
const box = page.locator('[aria-label="AI record summary"]');
await box.locator('input[type=checkbox]').check(); await box.getByRole('button', { name: /Create summary/ }).click();
R.check('record summary: the same clear message, and nothing was sent', !!(await waitFor(async () => /not set up on this server yet/.test(await box.innerText()), 8000, 200)));
await shot(page, 'm5_unconfigured');
R.check('nothing reached any AI service', sentToService() === 0);
R.check('database: no AI sharing was audited (nothing was shared)', probe('ai_audit', P1).length === auditBefore);
await context.close();
const c2 = await newSession(browser); c2.page.removeAllListeners('dialog'); c2.page.on('dialog', d => { dialogs++; d.dismiss().catch(() => {}); });
await loginUi(c2.page, 'supervisor', env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD); await sleep(1500); await goNav(c2.page, 'Conflict review');
await c2.page.locator('.conflict-card', { hasText: P1 }).filter({ hasText: 'condition' }).getByRole('button', { name: /Review case/ }).click();
await waitFor(async () => /Current value/.test(await c2.page.locator('.modal').innerText().catch(() => '')), 10000);
await c2.page.getByRole('button', { name: /Explain this conflict \(AI\)/ }).click();
R.check('conflict explanation: clear "not set up" message; the review dialog itself keeps working', !!(await waitFor(async () => /not set up on this server yet/.test(await c2.page.locator('.modal').innerText()), 8000, 200)) && /Clinician decision|Only an authorized clinician/.test(await c2.page.locator('.modal').innerText()));
R.check('conflict still pending; no request reached an AI service; no audit row added', probe('conflicts_for', P1).find(c => c.field === 'condition').status === 'pending' && sentToService() === 0 && probe('ai_audit', P1).length === auditBefore);
R.check('NO browser dialogs', dialogs === 0, `dialogs=${dialogs}`);
await browser.close(); R.finish();
