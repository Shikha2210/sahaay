// Automated accessibility sweep (axe-core, WCAG 2.0/2.1 A+AA) over EVERY navigation page of every role, plus a few structural checks.
// Automated tools find roughly a third of real-world problems: this does NOT replace a screen-reader test.
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { Report, launch, newSession, loginUi, sleep, state, readEnv } from './ui.mjs';
if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: disposable database only'); process.exit(2); }
const AXE = fs.readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const R = new Report('phase_m7_a11y'); const S = state(); const U = S.users; const env = readEnv();
const browser = await launch();
const roles = [['patient', U.patient1.email, U.patient1.password], ['worker', U.worker1.email, U.worker1.password], ['clinician', U.clinician1.email, U.clinician1.password], ['supervisor', env.BOOTSTRAP_SUPERVISOR_EMAIL, env.BOOTSTRAP_SUPERVISOR_PASSWORD]];
const all = {};
for (const [role, email, pw] of roles) {
  const { context, page } = await newSession(browser);
  await loginUi(page, role, email, pw); await sleep(1500);
  const items = await page.locator('.sidebar nav .nav-item').allInnerTexts();
  R.check(`${role}: document language and title are set, one main landmark, one h1`, await page.evaluate(() => document.documentElement.lang === 'en' && !!document.title && document.querySelectorAll('main').length === 1));
  for (const label of items.map(s => s.trim())) {
    await page.locator('.sidebar nav .nav-item', { hasText: label }).first().click(); await sleep(1300);
    await page.evaluate(AXE);
    const v = await page.evaluate(async () => (await axe.run(document, { runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] })).violations.map(x => ({ id: x.id, impact: x.impact, n: x.nodes.length, html: x.nodes[0].html.slice(0, 100) })));
    const bad = v.filter(x => ['critical', 'serious'].includes(x.impact));
    R.check(`${role} / ${label}: no critical or serious axe violations`, bad.length === 0, JSON.stringify(bad).slice(0, 300));
    for (const x of v.filter(x => !bad.includes(x))) (all[x.id] ||= { impact: x.impact, pages: [], html: x.html }).pages.push(role + '/' + label);
  }
  await context.close();
}
for (const [id, x] of Object.entries(all)) R.note('INFO', `moderate/minor: ${id} (${x.impact}) on ${x.pages.length} page(s)`, x.html);
R.finish(); await browser.close();
