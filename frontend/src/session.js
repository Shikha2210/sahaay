// Session + per-user local-data isolation.
//
// Rules enforced here:
//  * the token/profile live in sessionStorage (see api.js for limits of that protection);
//  * IndexedDB data on this device belongs to ONE user ("device owner"); another user can only use the device
//    after that data is synced or the previous user's unsynced changes are explicitly discarded;
//  * unsynced operations are never deleted without an explicit `discard: true` from a confirmed dialog;
//  * an expired token never wipes local data.
import { db } from './db';
import { getToken, setToken, isTokenExpired } from './api';

const PROFILE_KEY = 'sahaay-session';
const OWNER_KEY = 'device_owner';
const SYNCED = ['acknowledged', 'acknowledged_with_conflict'];

export function getSession() {
  try { return JSON.parse(sessionStorage.getItem(PROFILE_KEY) || 'null'); } catch { return null; }
}
export function startSession(user, token) {
  setToken(token);
  const profile = { id: user.id, role: user.role, name: user.name, email: user.email };
  sessionStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  return profile;
}
export function endSession() { setToken(null); sessionStorage.removeItem(PROFILE_KEY); }

// On page load: is there a usable session? `expired` means a profile exists but its token is gone/expired.
export function restoreSession() {
  const profile = getSession();
  if (!profile || !profile.id) return { session: null, expired: false };
  const token = getToken();
  return { session: profile, expired: !token || isTokenExpired(token) };
}

export async function getDeviceOwner() { return (await db.sync_state.get(OWNER_KEY))?.value || null; }
const setDeviceOwner = id => db.sync_state.put({ key: OWNER_KEY, value: id, updated_at: new Date().toISOString() });

export async function countUnsynced(ownerId) {
  const all = await db.operations.toArray();
  return all.filter(o => !SYNCED.includes(o.status) && (!ownerId || o.owner_id === ownerId)).length;
}

export async function wipeLocalData() {
  await db.transaction('rw', db.patients, db.operations, db.conflicts, db.audit, db.sync_state, async () => {
    await Promise.all([db.patients.clear(), db.operations.clear(), db.conflicts.clear(), db.audit.clear(), db.sync_state.clear()]);
  });
}

// Called right after a successful login. ok:false => a different user has unsynced changes on this device.
export async function claimDevice(userId) {
  const owner = await getDeviceOwner();
  if (!owner) {
    // First use (or data from before ownership existed): the signing-in user takes ownership of untagged operations.
    await db.operations.toCollection().modify(op => { if (!op.owner_id) op.owner_id = userId; });
    await setDeviceOwner(userId);
    return { ok: true };
  }
  if (owner === userId) return { ok: true };
  const pending = await countUnsynced(owner);
  if (pending > 0) return { ok: false, pending };
  await wipeLocalData();
  await setDeviceOwner(userId);
  return { ok: true };
}
export async function discardAndClaim(userId) {
  await wipeLocalData();
  await setDeviceOwner(userId);
}

// Logout is two steps so the UI can ask for confirmation.
export async function beginLogout() { return { pending: await countUnsynced() }; }
export async function completeLogout({ discard = false } = {}) {
  const pending = await countUnsynced();
  if (pending > 0 && !discard) { const e = new Error('UNSYNCED_OPERATIONS'); e.pending = pending; throw e; }
  await wipeLocalData();
  endSession();
}
// Token expired: stop using it but KEEP all local data (the user can sign in again and resume the queue).
export function expireSession() { setToken(null); }
