// Offline behaviour for READ-ONLY screens (Milestone 6). A successful read is remembered on this device (per signed-in user); if the
// network is unavailable later, the last-known data is shown with a clear "saved on this device" notice. Actions that need the server
// (decisions, confirmations, evidence, linking, AI) are never queued or faked offline: they explain that a connection is needed.
import { db } from './db';
import { api } from './api';
import { getSession } from './session';

export async function cachedApi(path) {
  const me = getSession();
  try {
    const data = await api(path);
    if (me?.id) await db.cache.put({ key: `${me.id}:${path}`, owner_id: me.id, path, data, fetched_at: new Date().toISOString() }).catch(() => {});
    return { data, stale: false, fetchedAt: null };
  } catch (e) {
    if ((e.code === 'network' || (typeof navigator !== 'undefined' && navigator.onLine === false)) && me?.id) {
      const row = await db.cache.get(`${me.id}:${path}`);
      if (row && row.owner_id === me.id) return { data: row.data, stale: true, fetchedAt: row.fetched_at };
    }
    throw e;
  }
}

export function isOfflineError(e) { return !!e && (e.code === 'network' || e.code === 'offline'); }
// One sentence for any failure; offline failures explain what to do instead of showing "Cannot reach the server."
export function friendlyError(e) {
  if (isOfflineError(e)) return 'You appear to be offline or the server cannot be reached. This needs a connection so the server can check it. Nothing was changed.';
  return (e && e.message) || 'Something went wrong. Please try again.';
}
