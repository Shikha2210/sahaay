// Mobile EMULATION (not a real phone): Edge with a phone viewport, touch input, mobile user agent and device pixel ratio.
import { chromium } from 'playwright-core';
import { Report, sleep, waitFor, state, BASE, idb, shot } from './ui.mjs';
if (!(process.env.E2E_DATABASE || '').startsWith('sahaay_test_')) { console.error('Refusing: disposable database only'); process.exit(2); }
const R = new Report('phase_m7_mobile_emulation'); const S = state(); const W = S.users.worker1;
const browser = await chromium.launch({ channel: 'msedge', headless: true });
for (const dev of [{ name: 'Android-class 393x851 @2.75x', viewport: { width: 393, height: 851 }, dpr: 2.75, ua: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36' },
                   { name: 'small phone 320x568 @2x', viewport: { width: 320, height: 568 }, dpr: 2, ua: 'Mozilla/5.0 (Linux; Android 9; Small) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36' }]) {
  const ctx = await browser.newContext({ viewport: dev.viewport, deviceScaleFactor: dev.dpr, isMobile: true, hasTouch: true, userAgent: dev.ua });
  const page = await ctx.newPage(); const tag = dev.name;
  await page.goto(BASE); await sleep(1500);
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  R.check(`${tag}: login has no horizontal scroll`, (await overflow()) <= 1, 'overflow ' + await overflow());
  await page.locator('.role-option', { hasText: 'Healthcare Worker' }).tap();
  await page.getByPlaceholder('Enter your name').tap(); await page.keyboard.type('Synthetic Tester'); await page.getByPlaceholder('you@example.com').tap(); await page.keyboard.type(W.email);
  await page.getByPlaceholder('Enter password').tap(); await page.keyboard.type(W.password); await page.locator('button.sign-in').tap();
  R.check(`${tag}: sign in using touch taps`, !!(await waitFor(async () => (await page.locator('.hamburger, .sidebar').count()) > 0, 15000))); await sleep(1500);
  R.check(`${tag}: dashboard fits the screen`, (await overflow()) <= 1, 'overflow ' + await overflow());
  await page.getByRole('button', { name: 'Open menu' }).tap(); await sleep(400);
  await page.locator('.sidebar nav .nav-item', { hasText: 'Patient directory' }).tap(); await sleep(800);
  R.check(`${tag}: drawer navigation works by touch and the directory fits`, /Patient directory/i.test(await page.locator('main').innerText()) && (await overflow()) <= 1, 'overflow ' + await overflow());
  const name = `ZZ-SYNTHETIC Mobile ${tag.slice(0, 4)} ${Math.random().toString(36).slice(2, 5)}`;
  await ctx.setOffline(true); await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await page.getByRole('button', { name: /Register patient/ }).first().tap();
  const dlgFits = await page.evaluate(() => { const r = document.querySelector('[role=dialog]').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth + 1; });
  R.check(`${tag}: registration dialog fits within the screen width`, dlgFits);
  await page.getByPlaceholder('Full name').tap(); await page.keyboard.type(name); await page.getByPlaceholder('Contact number').tap(); await page.keyboard.type('9000000066'); await page.getByPlaceholder('Village name').tap(); await page.keyboard.type('Synthetic Village');
  const saveBtn = page.getByRole('button', { name: /Save patient locally/ }); await saveBtn.scrollIntoViewIfNeeded(); await saveBtn.tap(); await sleep(800);
  R.check(`${tag}: offline registration saved by touch`, /Not synced yet/.test(await page.locator('tr', { hasText: name }).first().innerText().catch(() => '')));
  await ctx.setOffline(false); await page.evaluate(() => window.dispatchEvent(new Event('online')));
  R.check(`${tag}: reconnect syncs it`, !!(await waitFor(async () => ((await idb(page, 'operations')) || []).filter(o => o.changes?.name === name).every(o => o.status === 'acknowledged') && ((await idb(page, 'operations')) || []).some(o => o.changes?.name === name), 25000, 500)));
  const small = await page.evaluate(() => [...document.querySelectorAll('button, a, input:not([type=hidden]), select')].filter(e => e.offsetParent !== null).filter(e => { const r = e.getBoundingClientRect(); return r.width < 24 || r.height < 24; }).length);
  const which = await page.evaluate(() => [...document.querySelectorAll('button, a, input:not([type=hidden]), select')].filter(e => e.offsetParent !== null).filter(e => { const r = e.getBoundingClientRect(); return r.width < 24 || r.height < 24; }).map(e => e.outerHTML.slice(0, 110)).join(' | '));
  R.check(`${tag}: no touch target smaller than 24x24 px`, small === 0, which);
  await shot(page, 'm7_mobile_' + dev.viewport.width);
  await ctx.close();
}
R.note('INFO', 'scope', 'emulation in Edge only; NOT a real phone (no real touch hardware, OS keyboard, install-to-home-screen or network switching)');
R.finish(); await browser.close();
