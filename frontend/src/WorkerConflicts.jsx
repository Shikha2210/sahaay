// Healthcare-worker conflict notifications + evidence submission (Milestone 4).
//   GET /api/sync/conflicts   - conflicts that came from THIS worker's own synchronised changes, with outcomes
//   POST /api/conflicts/{id}/evidence - submit a document REFERENCE for a document-verifiable conflict
// The backend decides what the worker may see/do; the worker cannot review or resolve anything.
import React, { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Info, ShieldCheck } from 'lucide-react';
import { api } from './api';
import { label, show, fmtDate, CATEGORY_LABEL } from './fieldLabels';

export default function WorkerConflicts({ notify, onChanged, ui }) {
  const { Head, Badge, Modal } = ui;
  const [data, setData] = useState(null), [err, setErr] = useState(''), [tab, setTab] = useState('open'), [form, setForm] = useState(null);
  const load = useCallback(async () => {
    try { setData(await api('/api/sync/conflicts?status=all')); setErr(''); }
    catch (e) { setErr(e.code === 'network' ? 'You appear to be offline. Conflict notifications need a connection.' : e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const list = (data?.conflicts || []).filter(c => (tab === 'open' ? c.status === 'pending' : c.status !== 'pending'));
  return <div className="content">
    <Head eyebrow="SYNC CONFLICTS" title="Conflicts from your synced changes" sub="When your change disagrees with a newer record, SAHAAY keeps both values and sends the case for review. Nothing is overwritten. For document conflicts you can add supporting evidence." />
    <div className="toolbar"><div style={{ display: 'flex', gap: 8 }}>
      <button className={tab === 'open' ? 'gradient-btn' : 'outline-btn'} onClick={() => setTab('open')}>Open ({data?.open_count ?? 0})</button>
      <button className={tab === 'closed' ? 'gradient-btn' : 'outline-btn'} onClick={() => setTab('closed')}>Closed</button></div></div>
    {err && <div className="info-card" role="alert"><Info /><p>{err}</p></div>}
    {data && list.length === 0 && !err && <div className="info-card"><ShieldCheck /><p>{tab === 'open' ? 'No open conflicts from your changes.' : 'No closed conflicts yet.'}</p></div>}
    {list.map(c => <div className="conflict-card" key={c.conflict_id} style={{ alignItems: 'flex-start' }}>
      <div style={{ flex: 1 }}>
        <Badge tone={c.category === 'CRITICAL_CLINICAL' ? 'amber' : 'purple'}>{CATEGORY_LABEL[c.category] || c.category}</Badge> <Badge tone={c.status === 'pending' ? 'amber' : 'green'}>{c.status}</Badge>
        <h3>{label(c.field)} · {c.patient_id}</h3>
        <p>Record had {show(c.value_a)} <ArrowRight size={13} /> your change {show(c.value_b)}{c.status === 'pending' && c.current_value !== c.value_a ? <> · now {show(c.current_value)}</> : null}</p>
        {c.status !== 'pending' && <p><span><b>{c.status === 'resolved' ? `Outcome: record now shows ${show(c.selected_value)}` : 'Outcome: your change was not applied'}</b> · {fmtDate(c.resolved_at)}</span></p>}
        {c.evidence.length > 0 && <div style={{ fontSize: 12, marginTop: 6 }}>{c.evidence.map(e => <div key={e.evidence_reference}>Evidence <b>{e.evidence_reference}</b>: {e.status === 'pending_review' ? 'waiting for clinician review' : e.status}{e.status === 'approved' ? ` (approved value ${show(e.approved_value)})` : ''}{e.review_note ? ` - ${e.review_note}` : ''}</div>)}</div>}
        {c.status === 'pending' && c.category !== 'DOCUMENT_VERIFIABLE' && <small>{c.category === 'PATIENT_VERIFIABLE' ? 'The patient is asked to confirm the correct value.' : 'An authorized clinician will review this.'}</small>}
      </div>
      {c.can_submit_evidence && <button className="outline-btn" onClick={() => setForm({ c, reference: '', description: '', claimed: '', error: '', busy: false })}>Submit evidence</button>}
    </div>)}
    {form && <Modal title="Submit evidence" close={() => setForm(null)}>
      <form className="modal-form" onSubmit={async e => {
        e.preventDefault(); setForm(f => ({ ...f, busy: true, error: '' }));
        try {
          const claimed = form.c.field === 'age' ? Number(form.claimed) : form.claimed.trim();
          await api('/api/conflicts/' + encodeURIComponent(form.c.conflict_id) + '/evidence', { method: 'POST', body: JSON.stringify({ reference: form.reference.trim(), description: form.description.trim(), claimed_value: claimed }) });
          notify('Evidence submitted. A clinician will review it.'); setForm(null); await load(); if (onChanged) onChanged();
        } catch (er) { setForm(f => ({ ...f, busy: false, error: er.message })); }
      }}>
        <div className="modal-note">For <b>{label(form.c.field)}</b> on patient <b>{form.c.patient_id}</b>. This records a <b>reference</b> to a document (for example a register entry number). It does not upload or verify the document; a clinician decides.</div>
        {form.error && <div className="disclaimer" role="alert">{form.error}</div>}
        <label>Document reference<input required minLength={3} maxLength={100} pattern="[A-Za-z0-9_.:#\-]{3,100}" value={form.reference} onChange={e => setForm({ ...form, reference: e.target.value })} placeholder="e.g. REG-2026-0412" /></label>
        <label>What the document shows<input required minLength={3} maxLength={1000} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="Short description" /></label>
        <label>Value the document supports for {label(form.c.field).toLowerCase()}<input required value={form.claimed} onChange={e => setForm({ ...form, claimed: e.target.value })} /></label>
        <button className="gradient-btn" disabled={form.busy}>{form.busy ? 'Submitting…' : 'Submit evidence'}</button>
      </form></Modal>}
  </div>;
}
