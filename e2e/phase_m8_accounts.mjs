// Account creation in the real UI: patient self-sign-up messages, and a supervisor creating a health worker who then signs in. Disposable DB only.
import { Report, launch, newSession, loginUi, goNav, sleep, waitFor, readEnv, BASE } from './ui.mjs';
if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: disposable database only'); process.exit(2); }
const R = new Report('phase_m8_accounts'); const env = readEnv(); const b = await launch(); const rnd = Math.random().toString(36).slice(2, 7);
const fresh = async () => { const { page } = await newSession(b); await page.goto(BASE); await page.evaluate(() => sessionStorage.clear()); await page.goto(BASE); await sleep(700); return page; };
const msg = async p => (await p.locator('.login-card [role=alert]').first().innerText().catch(() => '')).trim();
async function signup(page, n, e, pw) { await page.getByPlaceholder('Enter your name').fill(n); await page.getByPlaceholder('you@example.com').fill(e); await page.getByPlaceholder('Enter password').fill(pw); await page.getByRole('button', { name: 'Create patient account' }).click(); await sleep(1500); }
let p = await fresh(); await signup(p, 'ZZ Synthetic Patient', `zz.p.${rnd}@gmail.com`, 'abcd1234');
R.check('short password: the message names the password and the 10-character minimum', /Password is too short.*10/.test(await msg(p)), await msg(p));
await p.getByPlaceholder('you@example.com').fill('not-an-email'); await p.getByPlaceholder('Enter password').fill('Strong-Pass-2026!'); await p.getByRole('button', { name: 'Create patient account' }).click(); await sleep(1200);
R.check('bad email: the message asks for a valid email address', /valid email/i.test(await msg(p)), await msg(p));
R.check('the 10-character hint is visible before submitting', /at least 10 characters/.test(await p.locator('.login-card').innerText()));
const pe = `zz.p2.${rnd}@gmail.com`; p = await fresh(); await signup(p, 'ZZ Synthetic Patient', pe, 'Strong-Pass-2026!');
R.check('valid patient self-registration signs the patient in', (await p.locator('.sidebar').count()) > 0);
p = await fresh(); await signup(p, 'ZZ Synthetic Patient', pe, 'Strong-Pass-2026!');
R.check('registering the same email again says it is already registered', /already registered/i.test(await msg(p)), await msg(p));

const sp = await fresh();
R.check('the sign-in screen offers Patient, Healthcare Worker and Supervisor only (no clinician)', JSON.stringify(await sp.locator('.role-option').allInnerTexts().then(a => a.map(x => x.trim()))) === JSON.stringify(['Patient', 'Healthcare Worker', 'Professional Supervisor']));
R.check('the sign-up form has no Patient ID / phone claim fields', (await sp.getByPlaceholder('e.g. SH-1234').count()) === 0);
const own = await fresh(); await signup(own, 'ZZ Synthetic Auto Patient', `zz.auto.${rnd}@gmail.com`, 'Strong-Pass-2026!'); await sleep(2200);
const ot = (await own.locator('main .content').innerText()).replace(/\s+/g, ' ');
R.check('a new patient is given an ID automatically and sees their own record (no supervisor approval)', /Your record SH-[0-9A-F]{8}/.test(ot) && /Linked to your account/.test(ot) && !/not linked/i.test(ot), ot.slice(0, 120));

const { context, page } = await newSession(b);
R.check('supervisor signs in', !!(await loginUi(page, 'supervisor', env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD))); await sleep(1200);
await goNav(page, 'Staff accounts'); await sleep(600);
R.check('staff role choices are Health worker and Supervisor (no clinician)', JSON.stringify(await page.getByLabel('Role').locator('option').allInnerTexts()) === JSON.stringify(['Health worker', 'Supervisor']));
await page.getByLabel('Full name').fill('ZZ Synthetic Worker');
R.check('email is optional: the form can be completed with name + password only', !(await page.getByLabel('Email address (optional)').evaluate(e => e.required)));
await page.getByRole('button', { name: 'Generate a strong password' }).click();
const pw = await page.getByLabel('Password', { exact: true }).inputValue();
await page.getByRole('button', { name: 'Create account' }).click(); await sleep(1500);
const t = await page.locator('main .content').innerText();
const lid = (t.match(/Login ID:\s*(HW-[A-Z0-9]{6})/) || [])[1];
R.check('a random login ID like HW-XXXXXX is generated and shown once with the password', !!lid && t.includes(pw), lid);
await goNav(page, 'Audit history'); await sleep(1500);
R.check('the creation is in the audit history', /Provisioned worker account/i.test(await page.locator('main .content').innerText()));
await context.close();

const w = await newSession(b);
R.check('the health worker signs in with ONLY the generated ID (no email)', !!(await loginUi(w.page, 'worker', lid, pw)) && /Patient directory/.test(await w.page.locator('.sidebar').innerText()));
await w.context.close();
const w2 = await newSession(b); await w2.page.goto(BASE); await w2.page.locator('.role-option', { hasText: 'Healthcare Worker' }).click();
await w2.page.getByPlaceholder('Enter your name').fill('x'); await w2.page.getByPlaceholder('you@example.com').fill(lid.toLowerCase()); await w2.page.getByPlaceholder('Enter password').fill(pw); await w2.page.locator('button.sign-in').click();
R.check('the ID works in lower case too', !!(await waitFor(async () => (await w2.page.locator('.sidebar').count()) > 0, 12000)));
R.finish(); await b.close();
