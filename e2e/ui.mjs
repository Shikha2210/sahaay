// Helpers for driving the real SAHAAY UI with Playwright (Microsoft Edge). Reads backend/.env and .e2e_state.json at
// run time; never prints credentials. Screenshots go to e2e/results/shots (git-ignored).
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(here, '..');
export const BASE = 'http://localhost:5173';
export const API = 'http://127.0.0.1:8000';
export const PY = path.join(ROOT, 'backend', '.venv', 'Scripts', 'python.exe');
const SHOTS = path.join(here, 'results', 'shots');
fs.mkdirSync(SHOTS, { recursive: true });

export function readEnv() {
  const out = {};
  for (const line of fs.readFileSync(path.join(ROOT, 'backend', '.env'), 'utf8').split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/); if (!m) continue;
    let v = m[2]; if (v.startsWith("'") && v.endsWith("'")) v = v.slice(1, -1).replace(/\\(['\\])/g, '$1');
    out[m[1]] = v;
  }
  return out;
}
export const state = () => JSON.parse(fs.readFileSync(process.env.E2E_STATE || path.join(here, '.e2e_state.json'), 'utf8'));

export class Report {
  constructor(name) { this.name = name; this.rows = []; }
  check(label, ok, detail = '') { this.rows.push([ok ? 'PASS' : 'FAIL', label, detail]); console.log(`${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? '  -- ' + detail : ''}`); return ok; }
  note(status, label, detail = '') { this.rows.push([status, label, detail]); console.log(`${status.padEnd(5)} ${label}${detail ? '  -- ' + detail : ''}`); }
  finish() {
    fs.mkdirSync(path.join(here, 'results'), { recursive: true });
    fs.writeFileSync(path.join(here, 'results', `${this.name}.json`), JSON.stringify(this.rows, null, 1));
    const p = this.rows.filter(r => r[0] === 'PASS').length, f = this.rows.filter(r => r[0] === 'FAIL').length;
    console.log(`\n${this.name}: ${p} PASS, ${f} FAIL, ${this.rows.length - p - f} other`);
  }
}

export async function launch() {
  return chromium.launch({ channel: 'msedge', headless: true });
}
export async function newSession(browser) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  page.on('dialog', d => d.dismiss().catch(() => {}));   // any stray alert()/prompt() is dismissed (and recorded below)
  return { context, page };
}
export const sleep = ms => new Promise(r => setTimeout(r, ms));
export async function waitFor(fn, ms = 15000, step = 250) {
  const end = Date.now() + ms; let last;
  while (Date.now() < end) { try { last = await fn(); if (last) return last; } catch { /* retry */ } await sleep(step); }
  return false;
}
export const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, name + '.png') }).catch(() => {});

const ROLE_LABEL = { patient: 'Patient', worker: 'Healthcare Worker', supervisor: 'Professional Supervisor', clinician: 'Authorized Clinician' };
export async function loginUi(page, role, email, password, { expectOk = true } = {}) {
  await page.goto(BASE);
  await page.locator('.role-option', { hasText: ROLE_LABEL[role] }).click();
  await page.getByPlaceholder('Enter your name').fill('Synthetic Tester');
  await page.getByPlaceholder('you@example.com').fill(email);
  await page.getByPlaceholder('Enter password').fill(password);
  await page.locator('button.sign-in').click();
  if (!expectOk) return waitFor(async () => (await page.locator('.login-card [role=alert]').count()) > 0, 8000);
  return waitFor(async () => (await page.locator('.sidebar').count()) > 0, 15000);
}
export const navLabels = page => page.locator('.sidebar nav .nav-item').allInnerTexts().then(a => a.map(s => s.trim()));
export const goNav = (page, label) => page.locator('.sidebar nav .nav-item', { hasText: label }).click();

export async function idb(page, store) {
  return page.evaluate(s => new Promise((res, rej) => {
    const r = indexedDB.open('SAHAAY_DARK_LOCAL');
    r.onsuccess = () => {
      const d = r.result;
      if (!d.objectStoreNames.contains(s)) { d.close(); res(null); return; }
      const q = d.transaction(s).objectStore(s).getAll();
      q.onsuccess = () => { d.close(); res(q.result); };
      q.onerror = () => rej(q.error);
    };
    r.onerror = () => rej(r.error);
  }), store);
}
export const sessionToken = page => page.evaluate(() => sessionStorage.getItem('sahaay-access-token'));
export const toastText = page => page.locator('.toast').first().innerText().catch(() => '');

// Token for a given user id, signed with the REAL backend secret but already expired (printed only to this process).
export function makeExpiredToken(userId, role) {
  return execFileSync(PY, [path.join(here, 'make_token.py'), 'expired', userId, role], { encoding: 'utf8' }).trim();
}

// ---- more helpers (workflows) -------------------------------------------------------------------------------
export function probe(...args) {
  return JSON.parse(execFileSync(PY, [path.join(here, 'probe.py'), ...args.map(String)], { encoding: 'utf8', cwd: here }).trim().split('\n').pop());
}
export async function registerPatientUi(page, name, { phone = '9000000000', village = 'Synthetic Village' } = {}) {
  await goNav(page, 'Patient directory');
  await page.getByRole('button', { name: /Register patient/ }).first().click();
  await page.getByPlaceholder('Full name').fill(name);
  await page.getByPlaceholder('Contact number').fill(phone);
  await page.getByPlaceholder('Village name').fill(village);
  await page.getByRole('button', { name: /Save patient locally/ }).click();
  return waitFor(async () => /registered locally/i.test(await page.locator('.toast').first().innerText().catch(() => '')), 6000);
}
export const syncNow = page => page.locator('.sync-mini button').click();
export const opByName = async (page, name) => ((await idb(page, 'operations')) || []).find(o => o.changes && o.changes.name === name);
export async function clickSignOut(page) { await page.locator('.profile-mini button[title="Sign out"]').click(); }
