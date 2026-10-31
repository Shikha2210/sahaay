// Session, logout, per-user isolation, queue ownership and expiry (Milestone 2).
// `fetch` is replaced by a test double: this verifies CLIENT logic only, not the backend.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { db } from './db';
import { api, setToken, getToken, purgeLegacyStorage, SESSION_EXPIRED_EVENT } from './api';
import {
  startSession, endSession, restoreSession, beginLogout, completeLogout, claimDevice, discardAndClaim,
  getDeviceOwner, expireSession, countUnsynced,
} from './session';
import { queueOperation, synchronize } from './syncEngine';
import { makeToken } from './test/helpers';

const A = { id: 'user-a', role: 'worker', name: 'Alice', email: 'alice@example.com' };
const B = { id: 'user-b', role: 'worker', name: 'Bob', email: 'bob@example.com' };

let sent;
function stubServer({ batchStatus = 'applied' } = {}) {
  sent = [];
  vi.stubGlobal('fetch', vi.fn(async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/sync/batch')) {
      const ops = JSON.parse(opts.body).operations;
      ops.forEach(o => sent.push(o.operation_id));
      return { ok: true, status: 200, json: async () => ({ results: ops.map(o => ({ operation_id: o.operation_id, patient_id: o.patient_id, status: batchStatus, server_version: 1, conflicts: [] })) }) };
    }
    return { ok: true, status: 200, json: async () => ({ changes: [], next_seq: 0, has_more: false }) };
  }));
}

beforeEach(async () => {
  await db.delete(); await db.open();
  localStorage.clear(); sessionStorage.clear(); setToken(null);
  vi.restoreAllMocks(); vi.unstubAllGlobals();
});

const signIn = (user, secs = 600) => startSession(user, makeToken(secs));
const addPatient = (id = 'SH-1') => db.patients.put({ patient_id: id, name: 'Test Patient', version: 1 });

describe('token storage', () => {
  it('keeps the token in sessionStorage, never localStorage', () => {
    signIn(A);
    expect(sessionStorage.getItem('sahaay-access-token')).toBeTruthy();
    expect(localStorage.getItem('sahaay-access-token')).toBeNull();
    expect(JSON.stringify({ ...localStorage })).not.toContain('Alice');
  });
  it('removes tokens and profiles that older versions left in localStorage', () => {
    localStorage.setItem('sahaay-access-token', 'old'); localStorage.setItem('sahaay-session', '{"role":"supervisor"}');
    purgeLegacyStorage();
    expect(localStorage.getItem('sahaay-access-token')).toBeNull();
    expect(localStorage.getItem('sahaay-session')).toBeNull();
  });
});

describe('session restoration and expiry', () => {
  it('restores a session whose token is still valid', () => {
    signIn(A, 600);
    const r = restoreSession();
    expect(r.session.id).toBe('user-a'); expect(r.expired).toBe(false);
  });
  it('reports an expired token as expired but still returns the profile (local data stays usable)', () => {
    signIn(A, -5);
    const r = restoreSession();
    expect(r.session.id).toBe('user-a'); expect(r.expired).toBe(true);
  });
  it('has no session after the tab is closed (sessionStorage gone)', async () => {
    signIn(A);
    sessionStorage.clear();
    vi.resetModules();
    const fresh = await import('./session');
    expect(fresh.restoreSession()).toEqual({ session: null, expired: false });
  });
  it('a 401 from the server clears the token, keeps local data and raises a session-expired event', async () => {
    signIn(A); await addPatient();
    const events = []; window.addEventListener(SESSION_EXPIRED_EVENT, e => events.push(e.detail));
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401, json: async () => ({ detail: { code: 'token_expired', message: 'Your session has expired. Please sign in again.' } }) })));
    await expect(api('/api/patients')).rejects.toMatchObject({ status: 401, code: 'token_expired' });
    expect(getToken()).toBeNull();
    expect(events).toEqual([{ code: 'token_expired' }]);
    expect(await db.patients.count()).toBe(1);
  });
  it('a failed LOGIN (401) is not treated as an expired session', async () => {
    const events = []; window.addEventListener(SESSION_EXPIRED_EVENT, e => events.push(e));
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 401, json: async () => ({ detail: 'Incorrect email or password' }) })));
    await expect(api('/api/auth/login', { method: 'POST', body: '{}' })).rejects.toThrow('Incorrect email or password');
    expect(events).toHaveLength(0);
  });
  it('expired token: sync sends nothing and keeps every operation queued', async () => {
    signIn(A, 600);
    const id = await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    stubServer();
    signIn(A, -60);
    const result = await synchronize();
    expect(result.authExpired).toBe(true);
    expect(fetch).not.toHaveBeenCalled();
    expect((await db.operations.get(id)).status).toBe('pending');
  });
  it('expireSession drops the token but never touches local data', async () => {
    signIn(A); await addPatient(); await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    expireSession();
    expect(getToken()).toBeNull();
    expect(await db.patients.count()).toBe(1); expect(await db.operations.count()).toBe(1);
  });
});

describe('logout', () => {
  it('with no pending operations: wipes local data and ends the session without needing confirmation', async () => {
    signIn(A); await claimDevice(A.id); await addPatient();
    await db.operations.put({ operation_id: 'done-1', owner_id: A.id, status: 'acknowledged', patient_id: 'SH-1' });
    expect((await beginLogout()).pending).toBe(0);
    await completeLogout();
    expect(await db.patients.count()).toBe(0); expect(await db.operations.count()).toBe(0);
    expect(getToken()).toBeNull(); expect(restoreSession().session).toBeNull();
    expect(await getDeviceOwner()).toBeNull();
  });
  it('with pending operations: reports the count and refuses to delete anything without explicit discard', async () => {
    signIn(A); await claimDevice(A.id); await addPatient();
    await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    await queueOperation({ patient_id: 'SH-1', changes: { village: 'X' }, base_version: 1 });
    expect((await beginLogout()).pending).toBe(2);
    await expect(completeLogout()).rejects.toMatchObject({ message: 'UNSYNCED_OPERATIONS', pending: 2 });
    expect(await db.operations.count()).toBe(2); expect(await db.patients.count()).toBe(1);
    expect(restoreSession().session.id).toBe('user-a'); // still signed in
  });
  it('with pending operations and explicit discard: wipes everything and ends the session', async () => {
    signIn(A); await claimDevice(A.id); await addPatient();
    await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    await completeLogout({ discard: true });
    expect(await db.operations.count()).toBe(0); expect(await db.patients.count()).toBe(0);
    expect(restoreSession().session).toBeNull();
  });
  it('rejected operations also count as unsynced (not silently dropped)', async () => {
    signIn(A);
    await db.operations.put({ operation_id: 'rej-0001', owner_id: A.id, status: 'rejected', patient_id: 'SH-1' });
    expect(await countUnsynced()).toBe(1);
  });
});

describe('two users on one device', () => {
  it('User A logs out, User B logs in: B sees none of A\'s records or operations', async () => {
    signIn(A); await claimDevice(A.id); await addPatient('SH-A');
    await completeLogout();
    signIn(B);
    expect(await claimDevice(B.id)).toEqual({ ok: true });
    expect(await db.patients.count()).toBe(0); expect(await db.operations.count()).toBe(0);
    expect(await getDeviceOwner()).toBe('user-b');
  });
  it('A closes the tab without logging out (all synced): B signing in clears A\'s cached records', async () => {
    signIn(A); await claimDevice(A.id); await addPatient('SH-A');
    endSession();
    expect(await claimDevice(B.id)).toEqual({ ok: true });
    expect(await db.patients.count()).toBe(0);
  });
  it('A left unsynced changes: B is BLOCKED, nothing is deleted, until B explicitly discards', async () => {
    signIn(A); await claimDevice(A.id); await addPatient('SH-A');
    await queueOperation({ patient_id: 'SH-A', changes: { phone: '1' }, base_version: 1 });
    endSession();
    const claim = await claimDevice(B.id);
    expect(claim).toEqual({ ok: false, pending: 1 });
    expect(await db.operations.count()).toBe(1); expect(await db.patients.count()).toBe(1);
    expect(await getDeviceOwner()).toBe('user-a');
    await discardAndClaim(B.id);
    expect(await db.operations.count()).toBe(0); expect(await getDeviceOwner()).toBe('user-b');
  });
  it('A signing back in after being blocked-out still has their queue', async () => {
    signIn(A); await claimDevice(A.id);
    await queueOperation({ patient_id: 'SH-A', changes: { phone: '1' }, base_version: 1 });
    endSession();
    expect((await claimDevice(B.id)).ok).toBe(false);
    expect(await claimDevice(A.id)).toEqual({ ok: true });
    expect(await countUnsynced(A.id)).toBe(1);
  });
  it('untagged operations from before ownership existed are claimed by the first user to sign in', async () => {
    await db.operations.put({ operation_id: 'legacy-0001', status: 'pending', patient_id: 'SH-1', changes: { phone: '1' } });
    await claimDevice(A.id);
    expect((await db.operations.get('legacy-0001')).owner_id).toBe('user-a');
  });
});

describe('queued operations are bound to their creator', () => {
  it('tags every queued operation with the creating user id', async () => {
    signIn(A);
    const id = await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    expect((await db.operations.get(id)).owner_id).toBe('user-a');
  });
  it('refuses to queue changes when nobody is signed in', async () => {
    await expect(queueOperation({ patient_id: 'SH-1', changes: { phone: '1' } })).rejects.toThrow(/Sign in/);
  });
  it('User A\'s operations are NOT sent under User B\'s session', async () => {
    signIn(A);
    const aOp = await queueOperation({ patient_id: 'SH-1', changes: { phone: 'A-secret-change' }, base_version: 1 });
    signIn(B); // simulates B getting a session without the device-claim step (worst case)
    const bOp = await queueOperation({ patient_id: 'SH-2', changes: { phone: 'B-change' }, base_version: 1 });
    stubServer();
    await synchronize();
    expect(sent).toEqual([bOp]);
    expect(sent).not.toContain(aOp);
    expect((await db.operations.get(aOp)).status).toBe('pending');
    expect((await db.operations.get(bOp)).status).toBe('acknowledged');
  });
  it('A\'s operations are sent once A signs in again', async () => {
    signIn(A);
    const aOp = await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    endSession(); signIn(B); stubServer(); await synchronize();
    expect(sent).toEqual([]);
    signIn(A); await synchronize();
    expect(sent).toEqual([aOp]);
  });
  it('an operation is not marked synchronized before the server acknowledges it', async () => {
    signIn(A);
    const id = await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network down'); }));
    await synchronize().catch(() => {});
    const op = await db.operations.get(id);
    expect(op.status).toBe('pending'); expect(op.attempts).toBeGreaterThan(0);
  });
});
