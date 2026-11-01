// Sync centre (Milestone 6): what is waiting, what failed and why, with real Retry and a confirmed Discard.
// Only genuine successes are green. Nothing is ever deleted silently: waiting changes cannot be discarded at all, and a rejected change is
// removed only after an explicit confirmation (and the removal is recorded in the local audit trail).
import React, { useCallback, useEffect, useState } from 'react';
import { Cloud, CloudOff, RefreshCw, ShieldCheck, AlertTriangle, ArrowDownUp, Info } from 'lucide-react';
import { db } from './db';
import { retryOperation, retryAllFailed, discardOperation, operationInfo, describeOperationError } from './syncEngine';
import { friendlyError } from './offlineCache';
import { fmtDate } from './fieldLabels';

const fieldsOf = op => Object.keys(op.changes || {}).filter(k => k !== '__create__').join(', ') || '—';
const kindLabel = op => (op.operation_type === 'create' ? 'New registration' : 'Update');

export default function SyncCentre({ ops, patients, online, sync, refresh, notify, ui }) {
  const { Head, Metric, Panel, Badge, Modal } = ui;
  const [filter, setFilter] = useState('active'), [busy, setBusy] = useState(false), [confirm, setConfirm] = useState(null), [last, setLast] = useState(null), [now, setNow] = useState(Date.now());
  const nameOf = id => patients.find(p => p.patient_id === id)?.name || id;
  const loadLast = useCallback(async () => setLast((await db.sync_state.get('last_sync'))?.value || null), []);
  useEffect(() => { loadLast(); }, [loadLast, ops]);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 5000); return () => clearInterval(t); }, []);

  const rows = ops.map(o => ({ o, info: operationInfo(o, now) })).sort((a, b) => (a.o.seq ?? 0) - (b.o.seq ?? 0) || (a.o.created_at < b.o.created_at ? -1 : 1)).reverse();
  const failed = rows.filter(r => r.info.kind === 'failed'), retrying = rows.filter(r => r.info.kind === 'retrying'), waiting = rows.filter(r => r.info.kind === 'waiting');
  const shown = rows.filter(r => filter === 'all' || (filter === 'active' && r.info.kind !== 'synced') || (filter === 'failed' && r.info.kind === 'failed') || (filter === 'synced' && r.info.kind === 'synced')).slice(0, 200);

  async function run(fn, done) {
    setBusy(true);
    try { await fn(); if (done) notify(done); await refresh(); } catch (e) { notify(friendlyError(e)); } finally { setBusy(false); }
  }
  async function syncNow() { setBusy(true); try { await sync(); } finally { setBusy(false); await loadLast(); } }

  const lastText = !last ? 'Not yet' : new Date(last.at).toLocaleTimeString();
  const lastNote = !last ? 'No sync on this device yet' : last.ok ? `${last.uploaded} confirmed${last.conflicts ? ` · ${last.conflicts} conflict(s)` : ''}` : (last.message ? 'Last attempt failed' : `${last.errors} problem(s)`);
  return <div className="content">
    <Head eyebrow="OFFLINE-FIRST ENGINE" title="Synchronization centre" sub="Changes stay on this device until the server confirms them. Nothing is marked as synced before that." action={<button className="gradient-btn" disabled={busy || !online} onClick={syncNow}><RefreshCw size={15} /> {busy ? 'Syncing…' : 'Sync now'}</button>} />
    {!online && <div className="info-card" role="status"><CloudOff /><p>You are offline. Changes you make are saved on this device and will be sent automatically when the connection returns.</p></div>}
    <div className="metric-grid">
      <Metric label="Waiting to sync" value={waiting.length + retrying.length} change={retrying.length ? `${retrying.length} being retried` : 'Stored on this device'} I={ArrowDownUp} />
      <Metric label="Needs attention" value={failed.length} change={failed.length ? 'Rejected by the server' : 'Nothing rejected'} I={AlertTriangle} tone={failed.length ? 'red' : 'green'} />
      <Metric label="Last sync" value={lastText} change={lastNote} I={ShieldCheck} tone="blue" />
      <Metric label="Connection" value={online ? 'Online' : 'Offline'} change={online ? 'Sync runs automatically' : 'Working offline'} I={online ? Cloud : CloudOff} tone="violet" />
    </div>

    {(failed.length > 0 || retrying.length > 0) && <Panel title="Needs attention" sub="Changes that were refused or are still being retried" action={<button className="outline-btn" disabled={busy} onClick={() => run(async () => { const n = await retryAllFailed(); notify(`${n} change${n === 1 ? '' : 's'} put back in the queue.`); if (online) await sync(); }, null)}>Retry all</button>}>
      <div className="table-wrap"><table><thead><tr><th>Change</th><th>What went wrong</th><th>Tries</th><th>Actions</th></tr></thead><tbody>
        {[...failed, ...retrying].map(({ o, info }) => <tr key={o.operation_id}>
          <td><b>{kindLabel(o)} · {nameOf(o.patient_id)}</b><small>{o.patient_id} · fields: {fieldsOf(o)}</small></td>
          <td><Badge tone={info.tone}>{info.label}</Badge><small style={{ display: 'block', marginTop: 4, whiteSpace: 'normal' }}>{describeOperationError(o) || 'No details were recorded.'}</small>
            {info.kind === 'retrying' && o.next_retry_at > now && <small>Next automatic try at {new Date(o.next_retry_at).toLocaleTimeString()}</small>}</td>
          <td>{(o.attempts || 0) + (o.retry_count || 0) || '—'}</td>
          <td style={{ whiteSpace: 'nowrap' }}>
            <button className="outline-btn" disabled={busy} onClick={() => run(async () => { await retryOperation(o.operation_id); if (online) await sync(); }, 'Change put back in the queue.')}>Retry</button>{' '}
            {info.kind === 'failed' && <button className="outline-btn" disabled={busy} onClick={() => setConfirm(o)}>Discard…</button>}
          </td></tr>)}
      </tbody></table></div>
      <div className="modal-note" style={{ marginTop: 10 }}><Info size={13} /> A rejected change is never retried automatically: the server said no. Retry it after the cause is fixed, or discard it if it is not needed.</div>
    </Panel>}

    <Panel title="Operation queue" sub="Every change made on this device">
      <div className="toolbar" role="group" aria-label="Filter the queue" style={{ gap: 8, flexWrap: 'wrap' }}>
        {[['active', `Not yet synced (${waiting.length + retrying.length + failed.length})`], ['failed', `Rejected (${failed.length})`], ['synced', 'Synced'], ['all', 'All']].map(([k, t]) => <button key={k} className={filter === k ? 'gradient-btn' : 'outline-btn'} aria-pressed={filter === k} onClick={() => setFilter(k)}>{t}</button>)}
      </div>
      <div className="table-wrap"><table><thead><tr><th>Status</th><th>Change</th><th>Patient</th><th>Created</th></tr></thead><tbody>
        {shown.map(({ o, info }) => <tr key={o.operation_id}>
          <td><Badge tone={info.tone}>{info.label}</Badge></td>
          <td>{kindLabel(o)}<small>{fieldsOf(o)}</small></td><td>{nameOf(o.patient_id)}<small>{o.patient_id}</small></td><td>{fmtDate(o.created_at)}</td></tr>)}
      </tbody></table>{shown.length === 0 && <p style={{ fontSize: 12, padding: 12 }}>{filter === 'active' ? 'Everything on this device has been synced.' : 'Nothing to show here.'}</p>}</div>
    </Panel>

    {confirm && <Modal title="Discard this change?" close={() => setConfirm(null)}>
      <div className="modal-form">
        <div className="modal-note"><b>{kindLabel(confirm)} · {nameOf(confirm.patient_id)}</b><br />Fields: {fieldsOf(confirm)}<br />Why the server refused it: {describeOperationError(confirm) || 'no details recorded'}</div>
        <p style={{ fontSize: 12 }}>If you discard it, this change is <b>permanently removed from this device and will never be sent</b>.{confirm.operation_type === 'create' ? ' The local copy of this new registration is removed too.' : ''} Changes that are still waiting are not affected.</p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="outline-btn" onClick={() => setConfirm(null)}>Keep it</button>
          <button className="gradient-btn" disabled={busy} onClick={async () => { const op = confirm; setConfirm(null); await run(() => discardOperation(op.operation_id), 'Change discarded and recorded.'); }}>Discard this change</button>
        </div></div></Modal>}
  </div>;
}
