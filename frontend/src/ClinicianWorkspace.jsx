// Clinician workspace (Milestone 4): dashboard, conflict queue, evidence review queue and resolution history.
// All actions (approve/reject evidence, resolve, reject incoming) happen in the existing ConflictDialog, which only offers what the
// backend allows and sends expected_field_version, so the M3 policy and stale-data protection are unchanged.
//   GET /api/conflicts?status=..., GET /api/evidence?status=...
import React, { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, ClipboardList, FileCheck2, Info, ShieldCheck, Stethoscope } from 'lucide-react';
import { api } from './api';
import { cachedApi, friendlyError } from './offlineCache';
import { StaleNotice } from './Notices';
import ConflictReview, { ConflictDialog } from './ConflictReview';
import AuditHistory from './AuditHistory';
import { label, show, fmtDate, CATEGORY_LABEL } from './fieldLabels';

export default function ClinicianWorkspace({ page, role, conflicts, refresh, notify, ui }) {
  const { Head, Metric, Panel, Badge, Modal } = ui;
  const [openId, setOpenId] = useState(null), [queue, setQueue] = useState(null), [history, setHistory] = useState(null), [err, setErr] = useState(''), [stale, setStale] = useState(null);

  const loadQueue = useCallback(async () => { try { const r = await cachedApi('/api/evidence?status=pending_review'); setQueue(r.data); if (r.stale) setStale(r); else setStale(null); setErr(''); } catch (e) { setErr(friendlyError(e)); } }, []);
  const loadHistory = useCallback(async () => {
    try { const [a, b] = await Promise.all([cachedApi('/api/conflicts?status=resolved'), cachedApi('/api/conflicts?status=rejected')]); setHistory([...a.data, ...b.data].sort((x, y) => ((x.resolved_at || '') < (y.resolved_at || '') ? 1 : -1))); if (a.stale || b.stale) setStale(a.stale ? a : b); setErr(''); }
    catch (e) { setErr(friendlyError(e)); }
  }, []);
  useEffect(() => { loadQueue(); if (page === 'history') loadHistory(); }, [page, loadQueue, loadHistory]);
  const changed = async () => { await refresh(); await loadQueue(); if (page === 'history') await loadHistory(); };
  const dialog = openId && <ConflictDialog id={openId} role={role} onClose={() => setOpenId(null)} onChanged={changed} notify={notify} Modal={Modal} Badge={Badge} />;

  const pending = conflicts.filter(c => c.status === 'pending');
  const by = cat => pending.filter(c => c.category === cat);

  if (page === 'audit') return <AuditHistory role="clinician" ui={ui} />;
  if (page === 'conflicts') return <ConflictReview role={role} conflicts={conflicts} refresh={changed} notify={notify} Head={Head} Badge={Badge} Modal={Modal} />;

  if (page === 'evidence') return <div className="content">
    <Head eyebrow="EVIDENCE REVIEW" title="Evidence waiting for your review" sub="Healthcare workers submit references to supporting documents. You record the exact value the evidence supports; you cannot review evidence you submitted yourself." />
    {stale && <StaleNotice fetchedAt={stale.fetchedAt} />}
    {err && <div className="disclaimer" role="alert">{err}</div>}
    {queue && queue.length === 0 && <div className="info-card"><ShieldCheck /><p>No evidence is waiting for review.</p></div>}
    {(queue || []).map(e => <div className="conflict-card" key={e.conflict_id + e.evidence_reference}>
      <div><Badge tone="purple">{label(e.field)}</Badge><h3>{e.evidence_reference} · {e.patient_id}</h3>
        <p><span>Submitted by a {e.submitted_role} · claimed value <b>{show(e.claimed_value)}</b> · {fmtDate(e.created_at)}</span></p><small>{e.description}</small>
        {e.submitted_by_me && <small> · You submitted this evidence, so another clinician must review it.</small>}</div>
      <button className="outline-btn" disabled={e.submitted_by_me} onClick={() => setOpenId(e.conflict_id)}>Review evidence</button>
    </div>)}
    {dialog}</div>;

  if (page === 'history') return <div className="content">
    <Head eyebrow="ACCOUNTABILITY" title="Resolution history" sub="Conflicts that were resolved or rejected. Nothing is deleted: the full history of each case stays on record." />
    {err && <div className="disclaimer" role="alert">{err}</div>}
    <Panel title="Closed conflicts" sub={history ? `${history.length} case${history.length === 1 ? '' : 's'}` : 'Loading…'}>
      <div className="table-wrap"><table><thead><tr><th>Closed</th><th>Patient</th><th>Detail</th><th>Category</th><th>Outcome</th><th>Value</th><th>By</th></tr></thead><tbody>
        {(history || []).map(c => <tr key={c.conflict_id}><td>{fmtDate(c.resolved_at)}</td><td>{c.patient_id}</td><td>{label(c.field)}</td><td>{CATEGORY_LABEL[c.category] || c.category}</td>
          <td><Badge tone={c.status === 'resolved' ? 'green' : 'neutral'}>{c.status}</Badge> <small>{(c.resolution || '').replace(/_/g, ' ')}</small></td><td>{show(c.selected_value)}</td><td>{c.resolved_role}</td></tr>)}
      </tbody></table>{history && history.length === 0 && <p style={{ fontSize: 12, padding: 12 }}>No conflicts have been closed yet.</p>}</div>
    </Panel>
  </div>;

  const queued = queue ? queue.length : '…';
  return <div className="content">
    <Head eyebrow="AUTHORIZED CLINICIAN" title="Clinical review at a glance." sub="Conflicts and evidence that need a clinical decision. Nothing is changed without your recorded decision." />
    {err && <div className="disclaimer" role="alert">{err}</div>}
    <div className="metric-grid">
      <Metric label="Clinical conflicts" value={by('CRITICAL_CLINICAL').length} change="Clinician-only decisions" I={Stethoscope} tone="violet" />
      <Metric label="Evidence to review" value={queued} change="Document conflicts" I={FileCheck2} tone="blue" />
      <Metric label="Other pending" value={by('REQUIRES_REVIEW').length + by('DOCUMENT_VERIFIABLE').length + by('PATIENT_VERIFIABLE').length} change="Needs review or awaiting evidence" I={AlertTriangle} />
      <Metric label="Total open" value={pending.length} change="All categories" I={ClipboardList} tone="green" />
    </div>
    <div className="grid-two">
      <Panel title="Pending clinical conflicts" sub="Clinician-only. Review the current value before deciding.">
        {by('CRITICAL_CLINICAL').length === 0 ? <p style={{ fontSize: 12 }}>No clinical conflicts are waiting.</p> :
          <div className="activity-list">{by('CRITICAL_CLINICAL').slice(0, 6).map(c => <div className="activity-row" key={c.conflict_id}>
            <div className="activity-main"><b>{label(c.field)} · {c.patient_id}</b><small>current {show(c.current_value)} · proposed {show(c.value_b)}</small></div>
            <button className="outline-btn" onClick={() => setOpenId(c.conflict_id)}>Review</button></div>)}</div>}
      </Panel>
      <Panel title="Evidence awaiting review" sub="Record the exact value the evidence supports.">
        {!queue || queue.length === 0 ? <p style={{ fontSize: 12 }}>{queue ? 'No evidence is waiting.' : 'Loading…'}</p> :
          <div className="activity-list">{queue.slice(0, 6).map(e => <div className="activity-row" key={e.conflict_id + e.evidence_reference}>
            <div className="activity-main"><b>{e.evidence_reference} · {label(e.field)}</b><small>{e.patient_id} · claimed {show(e.claimed_value)}</small></div>
            <button className="outline-btn" disabled={e.submitted_by_me} onClick={() => setOpenId(e.conflict_id)}>Review</button></div>)}</div>}
      </Panel>
    </div>
    <div className="disclaimer"><Info size={13} /> Evidence approval records a human decision; it is not cryptographic document verification.</div>
    {dialog}</div>;
}
