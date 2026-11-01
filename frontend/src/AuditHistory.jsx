// Audit history (Milestone 6): filterable, paginated list of who did what and when, from GET /api/audit. Supervisors see everything including
// sign-in events; clinicians do not see security events (the server enforces this; the filter just hides the group). Needs a connection.
import React, { useCallback, useEffect, useState } from 'react';
import { ClipboardList, Info } from 'lucide-react';
import { api } from './api';
import { friendlyError } from './offlineCache';
import { fmtDate } from './fieldLabels';

const GROUPS = [
  ['', 'All activity', null],
  ['security', 'Sign-ins and security', 'LOGIN_SUCCESS,LOGIN_FAILED,LOGIN_BLOCKED,LOGOUT,ACCOUNT_REGISTERED'],
  ['conflicts', 'Conflicts and evidence', 'CONFLICT_RESOLVED,CONFLICT_REJECTED,CONFLICT_ACTION_DENIED,EVIDENCE_SUBMITTED,EVIDENCE_REVIEWED,PATIENT_CONFIRMATION'],
  ['records', 'Patient records and sync', 'PATIENT_CREATED,PATIENT_CREATED_SYNC,SYNC_OPERATION'],
  ['accounts', 'Accounts and identity', 'USER_PROVISIONED,PATIENT_ACCOUNT_LINKED,IDENTITY_ATTEMPT_RECORDED'],
  ['ai', 'AI assistance', 'AI_ASSIST'],
];
const LABEL = {
  LOGIN_SUCCESS: 'Signed in', LOGIN_FAILED: 'Failed sign-in', LOGIN_BLOCKED: 'Sign-in blocked', LOGOUT: 'Signed out', ACCOUNT_REGISTERED: 'Account registered',
  CONFLICT_RESOLVED: 'Conflict resolved', CONFLICT_REJECTED: 'Incoming change rejected', CONFLICT_ACTION_DENIED: 'Decision refused', EVIDENCE_SUBMITTED: 'Evidence submitted',
  EVIDENCE_REVIEWED: 'Evidence reviewed', PATIENT_CONFIRMATION: 'Patient confirmed a value', PATIENT_CREATED: 'Patient created', PATIENT_CREATED_SYNC: 'Patient created (synced)',
  SYNC_OPERATION: 'Change synced', USER_PROVISIONED: 'Staff account created', PATIENT_ACCOUNT_LINKED: 'Patient account linked', IDENTITY_ATTEMPT_RECORDED: 'Identity attempt recorded', AI_ASSIST: 'AI assistance used',
};
const TONE = { LOGIN_FAILED: 'amber', LOGIN_BLOCKED: 'red', CONFLICT_ACTION_DENIED: 'amber', LOGIN_SUCCESS: 'green', CONFLICT_RESOLVED: 'green' };
const PAGE = 25;

function Details({ meta }) {
  const entries = Object.entries(meta || {});
  if (!entries.length) return <small>No further details.</small>;
  return <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '4px 12px', fontSize: 11 }}>
    {entries.map(([k, v]) => [<dt key={k + 'k'} style={{ opacity: .7 }}>{k.replace(/_/g, ' ')}</dt>, <dd key={k + 'v'} style={{ margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>])}
  </dl>;
}

export default function AuditHistory({ role, ui }) {
  const { Head, Panel, Badge } = ui;
  const groups = GROUPS.filter(g => g[0] !== 'security' || role === 'supervisor');
  const [f, setF] = useState({ group: '', actor_role: '', patient_id: '', since: '', until: '' }), [page, setPage] = useState(1), [data, setData] = useState(null), [err, setErr] = useState(''), [loading, setLoading] = useState(true), [open, setOpen] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    const q = new URLSearchParams({ page, page_size: PAGE });
    const types = GROUPS.find(g => g[0] === f.group)?.[2]; if (types) q.set('event_type', types);
    for (const k of ['actor_role', 'patient_id', 'since', 'until']) if (f[k].trim()) q.set(k, f[k].trim());
    try { setData(await api('/api/audit?' + q)); setErr(''); } catch (e) { setErr(friendlyError(e)); } finally { setLoading(false); }
  }, [f, page]);
  useEffect(() => { load(); }, [load]);
  const set = (k, v) => { setPage(1); setF(prev => ({ ...prev, [k]: v })); };
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;

  return <div className="content">
    <Head eyebrow="ACCOUNTABILITY" title="Audit history" sub="A traceable record of who did what and when. Entries cannot be edited from the app." />
    {role !== 'supervisor' && <div className="info-card"><Info /><p>Sign-in and security events are visible to supervisors only.</p></div>}
    <div className="toolbar" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'end' }}>
      <label style={{ fontSize: 11 }}>Activity<select aria-label="Activity" value={f.group} onChange={e => set('group', e.target.value)} style={sel}>{groups.map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select></label>
      <label style={{ fontSize: 11 }}>Who<select aria-label="Role" value={f.actor_role} onChange={e => set('actor_role', e.target.value)} style={sel}><option value="">Anyone</option>{['patient', 'worker', 'supervisor', 'clinician', ...(role === 'supervisor' ? ['anonymous'] : [])].map(r => <option key={r} value={r}>{r}</option>)}</select></label>
      <label style={{ fontSize: 11 }}>Patient ID<input aria-label="Patient ID" value={f.patient_id} maxLength={40} onChange={e => set('patient_id', e.target.value)} style={sel} placeholder="e.g. SH-1234" /></label>
      <label style={{ fontSize: 11 }}>From<input aria-label="From date" type="date" value={f.since} onChange={e => set('since', e.target.value)} style={sel} /></label>
      <label style={{ fontSize: 11 }}>To<input aria-label="To date" type="date" value={f.until} onChange={e => set('until', e.target.value)} style={sel} /></label>
      <button className="outline-btn" onClick={() => { setPage(1); setF({ group: '', actor_role: '', patient_id: '', since: '', until: '' }); }}>Clear filters</button>
    </div>
    {err && <div className="disclaimer" role="alert">{err}</div>}
    <Panel title="Entries" sub={data ? `${data.total} matching ${data.total === 1 ? 'entry' : 'entries'} · page ${page} of ${pages}` : 'Loading…'}>
      <div className="table-wrap"><table><caption style={{ position: 'absolute', left: -9999 }}>Audit history entries, newest first</caption>
        <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Patient</th><th scope="col">Summary</th><th scope="col"><span style={{ position: 'absolute', left: -9999 }}>Details</span></th></tr></thead>
        <tbody>{(data?.items || []).map((a, i) => {
          const key = a.created_at + a.event_type + i;
          return [<tr key={key}><td>{fmtDate(a.created_at)}</td><td><b>{a.actor_name || (a.actor_role === 'anonymous' ? 'Not signed in' : '—')}</b><small>{a.actor_role}</small></td>
            <td><Badge tone={TONE[a.event_type] || 'neutral'}>{LABEL[a.event_type] || a.event_type}</Badge></td><td>{a.patient_id || '—'}</td><td style={{ whiteSpace: 'normal', maxWidth: 280 }}>{a.description}</td>
            <td><button className="text-btn" aria-expanded={open === key} aria-label={`Details for ${LABEL[a.event_type] || a.event_type}`} onClick={() => setOpen(open === key ? null : key)}>{open === key ? 'Hide' : 'Details'}</button></td></tr>,
            open === key && <tr key={key + 'd'}><td colSpan={6} style={{ background: '#ffffff06' }}><Details meta={a.metadata} /></td></tr>];
        })}</tbody></table>
        {data && data.items.length === 0 && !loading && <div className="info-card" style={{ margin: 12 }}><ClipboardList /><p>No audit entries match these filters.</p></div>}</div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
        <button className="outline-btn" disabled={page <= 1 || loading} onClick={() => setPage(p => p - 1)}>← Newer</button>
        <small aria-live="polite">{loading ? 'Loading…' : `Page ${page} of ${pages}`}</small>
        <button className="outline-btn" disabled={!data?.has_more || loading} onClick={() => setPage(p => p + 1)}>Older →</button>
      </div>
    </Panel>
  </div>;
}
const sel = { display: 'block', background: '#141218', color: '#eee', border: '1px solid #ffffff1b', borderRadius: 8, padding: '7px 8px', fontSize: 12, marginTop: 3, minWidth: 130 };
