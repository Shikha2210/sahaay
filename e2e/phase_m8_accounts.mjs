// Account creation in the real UI: patient self-sign-up messages, and a supervisor creating a health worker who then signs in. Disposable DB only.
import { Report, launch, newSession, loginUi, goNav, sleep, waitFor, readEnv, BASE } from './ui.mjs';
if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: disposable database only'); process.exit(2); }
const R = new Report('phase_m8_accounts'); const env = readEnv(); const b = await launch(); const rnd = Math.random().toString(36).slice(2, 7);
const fresh = async () => { const { page } = await newSession(b); await page.goto(BASE); await page.evaluate(() => sessionStorage.clear()); await page.goto(BASE); await sleep(700); return page; };
const msg = async p => (await p.locator('.login-card [role=alert]').first().innerText().catch(() => '')).trim();
async function signup(page, n, e, pw) { await page.getByPlaceholder('Enter your name').fill(n); await page.getByPlaceholder('you@example.com').fill(e); await page.getByPlaceholder('Enter password').fill(pw); await page.getByRole('button', { name: 'Create patient account' }).click(); await sleep(1500); }
let p = await fresh(); await signup(p, 'ZZ Synthetic Patient', `zz.p.${rnd}@gmail.com`, 'abcd1234');
R.check('short password: the message names the password and the 10-character minimum', /Password is too short.*10/.test(await msg(p)), await msg(p));
await p.locator('input[placeholder="you@example.com"]').fill('not-an-email'); await p.getByPlaceholder('Enter password').fill('Strong-Pass-2026!'); await p.getByRole('button', { name: 'Create patient account' }).click(); await sleep(1200);
R.check('bad email: the message asks for a valid email address', /valid email/i.test(await msg(p)), await msg(p));
R.check('the 10-character hint is visible before submitting', /at least 10 characters/.test(await p.locator('.login-card').innerText()));
const pe = `zz.p2.${rnd}@gmail.com`; p = await fresh(); await signup(p, 'ZZ Synthetic Patient', pe, 'Strong-Pass-2026!');
R.check('valid patient self-registration signs the patient in', (await p.locator('.sidebar').count()) > 0);
p = await fresh(); await signup(p, 'ZZ Synthetic Patient', pe, 'Strong-Pass-2026!');
R.check('registering the same email again says it is already registered', /already registered/i.test(await msg(p)), await msg(p));

const { context, page } = await newSession(b);
R.check('supervisor signs in', !!(await loginUi(page, 'supervisor', env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD))); await sleep(1200);
R.check('"Staff accounts" is in the supervisor menu', (await page.locator('.sidebar nav .nav-item', { hasText: 'Staff accounts' }).count()) === 1);
await goNav(page, 'Staff accounts'); await sleep(600);
const we = `zz.worker.${rnd}@gmail.com`;
await page.getByLabel('Full name').fill('ZZ Synthetic Worker'); await page.getByLabel('Email address').fill(we);
R.check('the create button stays disabled until a 10+ character password exists', await page.getByRole('button', { name: 'Create account' }).isDisabled());
await page.getByRole('button', { name: 'Generate a strong password' }).click();
const pw = await page.getByLabel('Password', { exact: true }).inputValue();
R.check('"Generate" fills a 16-character mixed password', pw.length === 16 && /[a-z]/.test(pw) && /[A-Z]/.test(pw) && /\d/.test(pw) && /[^A-Za-z0-9]/.test(pw));
await page.getByRole('button', { name: 'Create account' }).click(); await sleep(1500);
const t = await page.locator('main .content').innerText();
R.check('success is shown once with the email and the password', t.includes(we) && t.includes(pw) && /shown once/i.test(t));
await page.getByLabel('Full name').fill('ZZ Synthetic Worker'); await page.getByLabel('Email address').fill(we); await page.getByLabel('Password', { exact: true }).fill(pw); await page.getByRole('button', { name: 'Create account' }).click(); await sleep(1200);
R.check('creating the same staff email again is refused with a clear message', /already has an account/.test(await page.locator('[role=alert]').first().innerText().catch(() => '')));
await page.getByLabel('Role').selectOption('clinician'); await page.getByLabel('Full name').fill('ZZ Synthetic Clinician'); const ce = `zz.clin.${rnd}@gmail.com`; await page.getByLabel('Email address').fill(ce); await page.getByLabel('Password', { exact: true }).fill('Another-Strong-1!'); await page.getByRole('button', { name: 'Create account' }).click(); await sleep(1200);
R.check('a clinician account can be created the same way', /clinician/.test(await page.locator('main .content').innerText()));
await goNav(page, 'Audit history'); await sleep(1500);
R.check('the creations appear in the audit history', /Provisioned worker account|Provisioned clinician account/i.test(await page.locator('main .content').innerText()));
await context.close();

const w = await newSession(b);
R.check('the NEW health worker signs in with the generated password and sees the worker menu', !!(await loginUi(w.page, 'worker', we, pw)) && /Patient directory/.test(await w.page.locator('.sidebar').innerText()));
await w.context.close();
const c = await newSession(b);
R.check('the NEW clinician signs in', !!(await loginUi(c.page, 'clinician', ce, 'Another-Strong-1!')));
R.finish(); await b.close();
