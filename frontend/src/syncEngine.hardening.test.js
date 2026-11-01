// Milestone 6 - offline reliability. `fetch` is a test double for OUR backend (client logic only); IndexedDB is an in-memory fake.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { db } from './db';
import { queueOperation, synchronize, retryOperation, retryAllFailed, discardOperation, operationInfo, describeOperationError, mergeRemotePatients, applyPending, onOtherTabChange } from './syncEngine';
import { cachedApi, friendlyError } from './offlineCache';
import { startSession, endSession, wipeLocalData } from './session';
import { setToken } from './api';
import { makeToken } from './test/helpers';

const A = { id: 'user-a', role: 'worker', name: 'A', email: 'a@example.com' }, B = { id: 'user-b', role: 'worker', name: 'B', email: 'b@example.com' };
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
let batches;
function server({ onBatch, delay = 0 } = {}) {
  batches = [];
  vi.stubGlobal('fetch', vi.fn(async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/sync/batch')) {
      const ops = JSON.parse(opts.body).operations; batches.push(ops.map(o => o.operation_id));
      if (delay) await new Promise(r => setTimeout(r, delay));
      return json(200, { results: ops.map(o => onBatch?.(o) ?? { operation_id: o.operation_id, patient_id: o.patient_id, status: 'applied', server_version: 2, conflicts: [] }) });
    }
    return json(200, { changes: [], next_seq: 0, has_more: false });
  }));
}
const sentIds = () => batches.flat();
const signIn = (u = A) => startSession(u, makeToken(600));
beforeEach(async () => { await db.delete(); await db.open(); localStorage.clear(); sessionStorage.clear(); setToken(null); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete navigator.locks; Object.defineProperty(navigator, 'onLine', { value: true, configurable: true }); signIn(); });
afterEach(() => { delete navigator.locks; });

// A minimal stand-in for the browser's Web Locks API (shared by every "tab" in the test), with the `ifAvailable` semantics we rely on.
function installWebLocks() {
  const held = new Set();
  navigator.locks = { request: async (name, opts, cb) => { if (held.has(name)) return opts?.ifAvailable ? cb(null) : undefined; held.add(name); try { return await cb({ name }); } finally { held.delete(name); } } };
}

describe('only one sync runs at a time - manual, automatic and other tabs can never send the same operation twice', () => {
  for (const [label, setup] of [['without the Web Locks API (in-page guard)', () => {}], ['with the Web Locks API (shared across tabs)', installWebLocks]]) {
    it(`two simultaneous syncs: one runs, the other is told it is already running - ${label}`, async () => {
      setup(); server({ delay: 60 });
      const id = await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
      const [first, second] = await Promise.all([synchronize(), synchronize()]);
      const results = [first, second];
      expect(results.filter(r => r.skipped).length).toBe(1);
      expect(results.find(r => r.skipped)).toMatchObject({ reason: 'already_running', uploaded: 0 });
      expect(sentIds()).toEqual([id]);                                               // exactly ONE upload of the operation
      expect((await db.operations.get(id)).status).toBe('acknowledged');
    });
    it(`many simultaneous callers still send each operation once - ${label}`, async () => {
      setup(); server({ delay: 40 });
      const ids = []; for (let i = 0; i < 5; i++) ids.push(await queueOperation({ patient_id: 'SH-1', changes: { phone: String(i) }, base_version: 1 }));
      await Promise.all([synchronize(), synchronize(), synchronize(), synchronize()]);
      expect(sentIds()).toEqual(ids);
    });
    it(`a later sync runs normally once the first has finished - ${label}`, async () => {
      setup(); server();
      await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
      expect((await synchronize()).skipped).toBeUndefined();
      const id2 = await queueOperation({ patient_id: 'SH-1', changes: { phone: '2' }, base_version: 1 });
      const r = await synchronize();
      expect(r.skipped).toBeUndefined(); expect(sentIds().at(-1)).toBe(id2);
    });
  }
  it('the lock is released even when a sync fails (the next one can run)', async () => {
    installWebLocks();
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network down'); }));
    await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    await synchronize().catch(() => {});
    server(); await db.operations.toCollection().modify(o => { o.next_retry_at = 0; });
    expect((await synchronize()).skipped).toBeUndefined();
  });
});

describe('failed operations: retry and discard', () => {
  async function rejected(extra = {}) {
    server({ onBatch: o => ({ operation_id: o.operation_id, patient_id: o.patient_id, status: 'rejected', reason: 'invalid_value: phone must be text up to 40 characters' }) });
    const id = await queueOperation({ patient_id: 'SH-1', changes: { phone: 'x' }, base_version: 1, ...extra });
    await synchronize(); return id;
  }
  it('a rejected operation is kept, flagged as failed (red) and its reason explained', async () => {
    const id = await rejected(); const op = await db.operations.get(id);
    expect(op.status).toBe('rejected');
    expect(operationInfo(op)).toMatchObject({ tone: 'red', kind: 'failed', label: 'Rejected by the server' });
    expect(describeOperationError(op)).toBe('A value is not allowed: phone must be text up to 40 characters');
  });
  it('retry puts the SAME operation back in the queue and the next sync sends it again with the same id', async () => {
    const id = await rejected();
    server();                                                                                 // the server now accepts it
    await retryOperation(id);
    expect((await db.operations.get(id))).toMatchObject({ status: 'pending', next_retry_at: 0, retry_count: 1 });
    await synchronize();
    expect(sentIds()).toEqual([id]); expect((await db.operations.get(id)).status).toBe('acknowledged');
  });
  it('retry-all re-queues every rejected or stalled change and nothing else', async () => {
    const r1 = await rejected();
    server(); const ok = await queueOperation({ patient_id: 'SH-2', changes: { phone: '2' }, base_version: 1 }); await synchronize();
    const stuck = await queueOperation({ patient_id: 'SH-3', changes: { phone: '3' }, base_version: 1 });
    await db.operations.update(stuck, { attempts: 2, last_error: 'Cannot reach the server.', next_retry_at: Date.now() + 99999 });
    expect(await retryAllFailed()).toBe(2);
    expect((await db.operations.get(r1)).status).toBe('pending'); expect((await db.operations.get(stuck)).next_retry_at).toBe(0);
    expect((await db.operations.get(ok)).status).toBe('acknowledged');
  });
  it('only rejected changes can be discarded; waiting and synced ones can never be deleted from the screen', async () => {
    const id = await rejected();
    server(); const waiting = await queueOperation({ patient_id: 'SH-2', changes: { phone: '2' }, base_version: 1 });
    await expect(discardOperation(waiting)).rejects.toThrow(/never deleted/);
    expect(await db.operations.get(waiting)).toBeTruthy();
    await discardOperation(id);
    expect(await db.operations.get(id)).toBeUndefined();
    const log = await db.audit.toArray();
    expect(log.some(a => a.event_type === 'OPERATION_DISCARDED' && a.patient_id === 'SH-1')).toBe(true);   // the discard itself is recorded
    await synchronize();
    await expect(discardOperation(waiting)).rejects.toThrow();                                // synced now: not discardable either
  });
  it('discarding a rejected registration removes the never-synced local copy, but not when other changes still depend on it', async () => {
    await db.patients.put({ patient_id: 'SH-9', name: 'Local only' });
    server({ onBatch: o => ({ operation_id: o.operation_id, patient_id: o.patient_id, status: 'rejected', reason: 'unsupported_fields' }) });
    const c = await queueOperation({ patient_id: 'SH-9', operation_type: 'create', changes: { name: 'Local only' } }); await synchronize();
    await discardOperation(c);
    expect(await db.patients.get('SH-9')).toBeUndefined();
    await db.patients.put({ patient_id: 'SH-8', name: 'Local two' });
    const c2 = await queueOperation({ patient_id: 'SH-8', operation_type: 'create', changes: { name: 'Local two' } });
    await queueOperation({ patient_id: 'SH-8', changes: { phone: '1' } });
    await db.operations.update(c2, { status: 'rejected', last_error: 'unsupported_fields' });
    await discardOperation(c2);
    expect(await db.patients.get('SH-8')).toBeTruthy();                                       // a later change still refers to it: keep it
  });
  it('a user cannot retry or discard another user\'s changes', async () => {
    const id = await rejected(); endSession(); signIn(B);
    await expect(retryOperation(id)).rejects.toThrow(/does not belong/);
    await expect(discardOperation(id)).rejects.toThrow(/does not belong/);
  });
  it('status presentation: only genuine successes are green', () => {
    const now = Date.now();
    expect(operationInfo({ status: 'acknowledged' })).toMatchObject({ tone: 'green' });
    expect(operationInfo({ status: 'acknowledged_with_conflict' })).toMatchObject({ tone: 'purple', kind: 'synced' });
    expect(operationInfo({ status: 'rejected' })).toMatchObject({ tone: 'red' });
    expect(operationInfo({ status: 'pending', attempts: 0 })).toMatchObject({ tone: 'amber', kind: 'waiting', label: 'Waiting to sync' });
    expect(operationInfo({ status: 'pending', attempts: 2, last_error: 'x', next_retry_at: now + 5000 }, now)).toMatchObject({ tone: 'amber', kind: 'retrying', label: 'Retrying' });
    for (const s of ['pending', 'rejected', 'unknown']) expect(operationInfo({ status: s }).tone).not.toBe('green');
  });
  it('error text is plain language for every server reason and for network failures', () => {
    expect(describeOperationError({ last_error: 'Cannot reach the server.' })).toMatch(/No connection/);
    expect(describeOperationError({ last_error: 'patient_not_found' })).toMatch(/does not know this patient/);
    expect(describeOperationError({ last_error: 'identity_status_not_allowed' })).toMatch(/UNVERIFIED/);
    expect(describeOperationError({ server_response: { reason: 'unsupported_fields' } })).toMatch(/cannot be changed/);
    expect(describeOperationError({})).toBe('');
  });
});

describe('unsynced local edits survive server data arriving', () => {
  const remote = { patient_id: 'SH-1', name: 'N', phone: 'server-phone', village: 'server-village', version: 5 };
  it('a refresh keeps this user\'s pending field values and takes everything else (and the version) from the server', async () => {
    await db.patients.put({ patient_id: 'SH-1', name: 'N', phone: 'old', village: 'old', version: 2 });
    await queueOperation({ patient_id: 'SH-1', changes: { phone: 'my-unsynced-phone' } });
    await mergeRemotePatients([remote]);
    expect(await db.patients.get('SH-1')).toMatchObject({ phone: 'my-unsynced-phone', village: 'server-village', version: 5 });
  });
  it('only PENDING changes of THIS user are re-applied (not synced, rejected or someone else\'s)', async () => {
    const synced = await queueOperation({ patient_id: 'SH-1', changes: { phone: 'synced-value' }, base_version: 2 });
    const rej = await queueOperation({ patient_id: 'SH-1', changes: { village: 'rejected-value' }, base_version: 2 });
    await db.operations.update(synced, { status: 'acknowledged' }); await db.operations.update(rej, { status: 'rejected' });
    await db.operations.add({ operation_id: 'other-user-op', seq: 99, patient_id: 'SH-1', owner_id: 'user-b', status: 'pending', changes: { name: 'someone else' }, created_at: new Date().toISOString() });
    await mergeRemotePatients([remote]);
    expect(await db.patients.get('SH-1')).toMatchObject({ phone: 'server-phone', village: 'server-village', name: 'N' });
  });
  it('several pending edits apply in the order they were made', async () => {
    await db.patients.put({ patient_id: 'SH-1', version: 2 });
    await queueOperation({ patient_id: 'SH-1', changes: { phone: 'first' } }); await queueOperation({ patient_id: 'SH-1', changes: { phone: 'second', village: 'v' } });
    await mergeRemotePatients([remote]);
    expect(await db.patients.get('SH-1')).toMatchObject({ phone: 'second', village: 'v' });
  });
  it('the pull feed does not overwrite a field with a pending local edit either', async () => {
    await db.patients.put({ patient_id: 'SH-1', name: 'N', phone: 'old', village: 'old', version: 2 });
    vi.stubGlobal('fetch', vi.fn(async (url, opts) => String(url).includes('/api/sync/batch')
      ? json(200, { results: JSON.parse(opts.body).operations.map(o => ({ operation_id: o.operation_id, patient_id: o.patient_id, status: 'retryable_error', reason: 'database_transaction_failed' })) })
      : json(200, { changes: [{ seq: 1, patient_id: 'SH-1', version: 3, changes: { phone: 'from-other-device', village: 'from-other-device' } }], next_seq: 1, has_more: false })));
    await queueOperation({ patient_id: 'SH-1', changes: { phone: 'mine-still-waiting' } });
    await synchronize();
    expect(await db.patients.get('SH-1')).toMatchObject({ phone: 'mine-still-waiting', village: 'from-other-device', version: 3 });
  });
  it('applyPending ignores operations for other patients and the __create__ marker', () => {
    const out = applyPending({ patient_id: 'SH-1', name: 'N' }, [{ patient_id: 'SH-2', changes: { name: 'X' } }, { patient_id: 'SH-1', changes: { __create__: true, age: 3 } }]);
    expect(out).toEqual({ patient_id: 'SH-1', name: 'N', age: 3 });
  });
});

describe('last sync is recorded for the Sync centre', () => {
  it('after a sync, and as a failure when the server cannot be reached', async () => {
    server(); await queueOperation({ patient_id: 'SH-1', changes: { phone: '1' }, base_version: 1 });
    await synchronize();
    expect((await db.sync_state.get('last_sync')).value).toMatchObject({ ok: true, uploaded: 1, errors: 0 });
    vi.stubGlobal('fetch', vi.fn(async () => json(500, { detail: 'boom' })));
    await synchronize().catch(() => {});
    expect((await db.sync_state.get('last_sync')).value).toMatchObject({ ok: false });
  });
  it('offline or signed-out attempts do not pretend a sync happened', async () => {
    server(); Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    expect((await synchronize()).offline).toBe(true); expect(await db.sync_state.get('last_sync')).toBeUndefined();
  });
});

describe('offline cache for read-only screens', () => {
  it('remembers the last good answer and serves it (marked stale) when the network fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200, { items: [1, 2] })));
    expect(await cachedApi('/api/x')).toMatchObject({ data: { items: [1, 2] }, stale: false });
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const r = await cachedApi('/api/x');
    expect(r.stale).toBe(true); expect(r.data).toEqual({ items: [1, 2] }); expect(Date.parse(r.fetchedAt)).toBeGreaterThan(0);
  });
  it('never invents data: no cache + offline = the error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    await expect(cachedApi('/api/never-loaded')).rejects.toMatchObject({ code: 'network' });
  });
  it('does not hide real server answers (403/500) behind old data', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200, { ok: 1 }))); await cachedApi('/api/y');
    vi.stubGlobal('fetch', vi.fn(async () => json(403, { detail: { code: 'role_not_permitted', message: 'no' } })));
    await expect(cachedApi('/api/y')).rejects.toMatchObject({ status: 403 });
  });
  it('is private to the signed-in user and is wiped on logout / user switch', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200, { secret: 'A-only' }))); await cachedApi('/api/z');
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    endSession(); signIn(B);
    await expect(cachedApi('/api/z')).rejects.toBeTruthy();                                    // B cannot read A's saved data
    endSession(); signIn(A);
    expect((await cachedApi('/api/z')).stale).toBe(true);
    await wipeLocalData();
    await expect(cachedApi('/api/z')).rejects.toBeTruthy();
    expect(await db.cache.count()).toBe(0);
  });
  it('offline failures get one clear explanatory sentence', () => {
    expect(friendlyError({ code: 'network' })).toMatch(/offline or the server cannot be reached/);
    expect(friendlyError({ code: 'offline' })).toMatch(/needs a connection/);
    expect(friendlyError({ message: 'Validation failed' })).toBe('Validation failed');
    expect(friendlyError(null)).toMatch(/try again/);
  });
});

describe('other tabs are told when something changed', () => {
  it.skipIf(typeof BroadcastChannel === 'undefined')('a change in one tab notifies the others', async () => {
    const cb = vi.fn(); const off = onOtherTabChange(cb);
    const other = new BroadcastChannel('sahaay-sync'); other.postMessage('changed');
    await new Promise(r => setTimeout(r, 50)); other.close(); off();
    expect(cb).toHaveBeenCalled();
  });
});
