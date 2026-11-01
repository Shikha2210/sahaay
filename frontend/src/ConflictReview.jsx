// Conflict review workflow for supervisors and clinicians (Milestone 3 / bug C2).
// The server enforces the policy (documentation/CONFLICT_POLICY.md); this screen only shows what the signed-in role may do
// (GET /api/conflicts/{id} -> `allowed`) and sends the exact fields the API expects. No browser prompt or alert dialogs.
import React, { useEffect, useState } from 'react';
import { ArrowRight, ArrowUpRight, ShieldCheck, AlertTriangle, CloudOff } from 'lucide-react';
import { api } from './api';
import { AiExplain, AiRecordSummary } from './AiParts';
import { friendlyError } from './offlineCache';
import useOnline from './useOnline';

const RESOLUTIONS = ['apply_evidence', 'apply_confirmation', 'accept_current', 'accept_incoming', 'custom', 'reject_incoming'];
export const SENT_RESOLUTIONS = RESOLUTIONS;   // used by a contract test: every value this screen can send must exist on the server
const WHO = {
  DOCUMENT_VERIFIABLE: 'A worker submits evidence, a clinician approves it and records the exact value, then a supervisor or clinician applies that value.',
  PATIENT_VERIFIABLE: 'The linked patient confirms the correct value; a supervisor or clinician then applies exactly that value.',
  CRITICAL_CLINICAL: 'A supervisor (or authorized clinician) approves clinical conflicts; healthcare workers and patients cannot.',
  REQUIRES_REVIEW: 'Fields without a verification route need a supervisor or authorized clinician.',
};
const show = v => (v === null || v === undefined || v === '' ? '—' : String(v));
const coerce = (field, text) => (field === 'age' ? (String(text).trim() === '' ? null : Number(text)) : text);
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body) });

export default function ConflictReview({ role, conflicts, refresh, notify, Head, Badge, Modal }) {
  const [openId, setOpenId] = useState(null), [cat, setCat] = useState('ALL');
  const online = useOnline();
  const allPending = conflicts.filter(c => c.status === 'pending');
  const pending = cat === 'ALL' ? allPending : allPending.filter(c => c.category === cat);
  return <div className="content">
    <Head eyebrow="CLINICAL GOVERNANCE" title="Conflict review queue" sub="Route field conflicts according to verification policy. The server checks every decision against your role and the record's current version." />
    {!online && <div className="info-card" role="status"><CloudOff /><p>You are offline. This queue is the copy saved on this device and may be out of date. Opening a conflict and making decisions need a connection.</p></div>}
    <div className="toolbar" style={{ gap: 8, flexWrap: 'wrap' }} role="group" aria-label="Filter by category">{[['ALL', 'All'], ['CRITICAL_CLINICAL', 'Clinical'], ['DOCUMENT_VERIFIABLE', 'Document'], ['PATIENT_VERIFIABLE', 'Patient-confirmed'], ['REQUIRES_REVIEW', 'Needs review']].map(([k, t]) => <button key={k} className={cat === k ? 'gradient-btn' : 'outline-btn'} onClick={() => setCat(k)}>{t} ({k === 'ALL' ? allPending.length : allPending.filter(c => c.category === k).length})</button>)}</div>
    {pending.length === 0 && <div className="info-card"><ShieldCheck /><p>No conflicts are waiting for review{cat === 'ALL' ? '' : ' in this category'}.</p></div>}
    {pending.map(c => <div className="conflict-card" key={c.conflict_id}>
      <div>
        <Badge tone={c.category === 'CRITICAL_CLINICAL' ? 'amber' : 'purple'}>{c.category}</Badge>
        {c.changed_since_raised && <Badge tone="amber">Changed since raised</Badge>}
        <h3>{c.field} · {c.patient_id}</h3>
        <p>{show(c.value_a)} <ArrowRight size={13} /> {show(c.value_b)}{c.changed_since_raised ? <> · current value: <b>{show(c.current_value)}</b></> : null}</p>
        {c.synthetic && <small>Synthetic demonstration case</small>}
      </div>
      <button className="outline-btn" onClick={() => setOpenId(c.conflict_id)}>Review case <ArrowUpRight size={14} /></button>
    </div>)}
    <div className="disclaimer">Critical clinical conflicts require an appropriately authorized clinical professional. Evidence approval records a human decision; it is not cryptographic document verification.</div>
    {openId && <ConflictDialog id={openId} role={role} onClose={() => setOpenId(null)} onChanged={refresh} notify={notify} Modal={Modal} Badge={Badge} />}
  </div>;
}

export function ConflictDialog({ id, role, onClose, onChanged, notify, Modal, Badge }) {
  const [d, setD] = useState(null), [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const [mode, setMode] = useState('accept_current'), [val, setVal] = useState(''), [note, setNote] = useState('');
  const [approved, setApproved] = useState({}), [reviewNote, setReviewNote] = useState({}), [ask, setAsk] = useState(null);
  const online = useOnline();

  // keepMessage: after a refused decision we reload the fresh state but must NOT erase the explanation of the refusal
  async function load(keepMessage = false) { try { setD(await api('/api/conflicts/' + encodeURIComponent(id))); if (!keepMessage) setErr(''); } catch (e) { setErr(friendlyError(e)); } }
  useEffect(() => { load(); }, [id]);

  // run an API action; on success close, on a stale/closed conflict reload the fresh state and keep the dialog open
  async function act(fn, done, { keepOpen = false } = {}) {
    setBusy(true); setErr('');
    try { await fn(); notify(done); await onChanged(); if (keepOpen) await load(); else onClose(); }
    catch (e) { setErr(friendlyError(e)); if (e.code === 'stale_conflict' || e.code === 'conflict_not_pending') await load(true); }
    finally { setBusy(false); setAsk(null); }
  }
  const resolve = extra => post('/api/conflicts/' + encodeURIComponent(id) + '/resolve', { expected_field_version: d.current_field_version, ...extra });
  // two-step confirmation instead of a browser dialog
  const confirmThen = (text, fn, done) => setAsk({ text, run: () => act(fn, done) });

  const body = !d ? <p>{err || 'Loading…'}</p> : (() => {
    const a = d.allowed, field = d.field, pend = d.status === 'pending';
    const approvedEv = d.evidence.filter(e => e.status === 'approved'), pendingEv = d.evidence.filter(e => e.status === 'pending_review');
    const confirmed = d.confirmations.filter(x => x.status === 'confirmed');
    return <div className="modal-form">
      <div><Badge tone={d.category === 'CRITICAL_CLINICAL' ? 'amber' : 'purple'}>{d.category}</Badge> <Badge tone="neutral">{d.status}</Badge></div>
      <div className="modal-note"><b>{field}</b> on patient <b>{d.patient_id}</b><br />
        Current value: <b>{show(d.current_value)}</b> (version {d.current_field_version})<br />
        Value when the conflict was raised: {show(d.value_a)} → proposed by another device: <b>{show(d.value_b)}</b></div>
      {d.changed_since_raised && <div className="disclaimer" role="alert"><AlertTriangle size={13} /> The current value differs from the one shown when the conflict was raised. Decide based on the current value.</div>}
      <p style={{ fontSize: 12 }}>{WHO[d.category]}</p>

      {d.category === 'DOCUMENT_VERIFIABLE' && <section><b>Evidence</b>
        {d.evidence.length === 0 && <p style={{ fontSize: 12 }}>No evidence has been submitted yet. A healthcare worker submits a document reference; a clinician then approves it.</p>}
        {d.evidence.map(e => <div key={e.evidence_reference} style={{ borderTop: '1px solid #ffffff14', padding: '8px 0', fontSize: 12 }}>
          <b>{e.evidence_reference}</b> · {e.status} · claimed value: {show(e.claimed_value)}{e.status === 'approved' ? <> · <b>approved value: {show(e.approved_value)}</b></> : null}
          <br /><small>{e.description}{e.review_note ? ' — review: ' + e.review_note : ''}</small>
          {pend && a.can_review_evidence && e.status === 'pending_review' && <div style={{ marginTop: 6, display: 'grid', gap: 6 }}>
            <label>Exact value the evidence supports<input value={approved[e.evidence_reference] ?? show(e.claimed_value).replace('—', '')} onChange={ev => setApproved({ ...approved, [e.evidence_reference]: ev.target.value })} /></label>
            <label>Review note<input value={reviewNote[e.evidence_reference] || ''} onChange={ev => setReviewNote({ ...reviewNote, [e.evidence_reference]: ev.target.value })} placeholder="What did you check?" /></label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="gradient-btn" disabled={busy} onClick={() => act(() => post(`/api/conflicts/${encodeURIComponent(id)}/evidence/${encodeURIComponent(e.evidence_reference)}/review`, { decision: 'approve', review_note: reviewNote[e.evidence_reference] || '', approved_value: coerce(field, approved[e.evidence_reference] ?? e.claimed_value) }), 'Evidence approved with the recorded value.', { keepOpen: true })}>Approve with this value</button>
              <button className="outline-btn" disabled={busy} onClick={() => act(() => post(`/api/conflicts/${encodeURIComponent(id)}/evidence/${encodeURIComponent(e.evidence_reference)}/review`, { decision: 'reject', review_note: reviewNote[e.evidence_reference] || '' }), 'Evidence rejected.', { keepOpen: true })}>Reject evidence</button>
            </div></div>}
        </div>)}
        {pend && pendingEv.length > 0 && !a.can_review_evidence && <p style={{ fontSize: 12 }}>Waiting for a clinician to review the submitted evidence.</p>}
      </section>}

      {d.category === 'PATIENT_VERIFIABLE' && <section><b>Patient confirmation</b>
        {confirmed.length === 0 ? <p style={{ fontSize: 12 }}>The patient has not confirmed a value yet.</p> : confirmed.map(x => <p key={x.confirmation_id} style={{ fontSize: 12 }}>Patient confirmed: <b>{show(x.confirmed_value)}</b></p>)}
      </section>}

      {pend && a.resolutions.includes('apply_evidence') && approvedEv.map(e => <button key={e.evidence_reference} className="gradient-btn" disabled={busy} onClick={() => confirmThen(`Apply the clinician-approved value "${show(e.approved_value)}" to ${field}?`, () => resolve({ resolution: 'apply_evidence', evidence_reference: e.evidence_reference }), 'Approved value applied.')}>Apply approved value “{show(e.approved_value)}”</button>)}
      {pend && a.resolutions.includes('apply_confirmation') && confirmed.map(x => <button key={x.confirmation_id} className="gradient-btn" disabled={busy} onClick={() => confirmThen(`Apply the patient's confirmed value "${show(x.confirmed_value)}" to ${field}?`, () => resolve({ resolution: 'apply_confirmation', patient_confirmation_id: x.confirmation_id }), "Patient's confirmed value applied.")}>Apply patient's confirmed value “{show(x.confirmed_value)}”</button>)}

      {pend && a.resolutions.includes('accept_current') && <section><b>Clinician decision</b>
        {[['accept_current', `Keep the current value (${show(d.current_value)})`], ['accept_incoming', `Use the incoming value (${show(d.value_b)})`], ['custom', 'Record a different value']].map(([k, label]) => <label key={k} style={{ display: 'flex', gap: 8, fontSize: 12 }}><input type="radio" name="mode" checked={mode === k} onChange={() => setMode(k)} /> {label}</label>)}
        {mode === 'custom' && <label>Value<input value={val} onChange={e => setVal(e.target.value)} /></label>}
        {mode === 'custom' && <label>Reason (required)<input value={note} onChange={e => setNote(e.target.value)} /></label>}
        <button className="gradient-btn" disabled={busy || (mode === 'custom' && (!val.trim() || note.trim().length < 3))} onClick={() => confirmThen(mode === 'custom' ? `Record "${val}" as the value of ${field}?` : mode === 'accept_incoming' ? `Replace the current ${field} with "${show(d.value_b)}"?` : `Keep the current ${field} value?`, () => resolve(mode === 'custom' ? { resolution: 'custom', selected_value: coerce(field, val), note } : { resolution: mode }), 'Decision recorded and audited.')}>Record decision</button>
      </section>}

      {pend && !d.synthetic && a.resolutions.length === 0 && !a.can_reject && <div className="disclaimer">{role === 'clinician' ? 'Nothing can be applied yet (waiting for approved evidence or a patient confirmation).' : d.category === 'DOCUMENT_VERIFIABLE' || d.category === 'PATIENT_VERIFIABLE' ? 'Nothing can be applied yet: this needs approved evidence or a patient confirmation first.' : 'Only an authorized clinician can resolve this conflict. You can monitor it here.'}</div>}
      {pend && d.synthetic && <div className="disclaimer">Synthetic demonstration case — it cannot modify patient records.</div>}

      {pend && a.can_reject && <section><b>Reject the incoming change</b>
        <label>Reason (required)<input value={note} onChange={e => setNote(e.target.value)} placeholder="Why is the incoming value not accepted?" /></label>
        <button className="outline-btn" disabled={busy || note.trim().length < 3} onClick={() => confirmThen('Reject the incoming change and keep the current value?', () => post('/api/conflicts/' + encodeURIComponent(id) + '/resolve', { resolution: 'reject_incoming', note }), 'Incoming change rejected; current value kept.')}>Reject incoming change</button>
      </section>}

      <AiExplain conflictId={id} />
      {role === 'clinician' && <AiRecordSummary patientId={d.patient_id} audience="clinician" />}

      {d.history && d.history.length > 0 && <section><b>History</b>{d.history.map((h, i) => <div key={i} style={{ fontSize: 11, opacity: .85 }}>{new Date(h.at).toLocaleString()} · {h.actor_role} · {h.action}{h.new_value !== undefined ? ` → ${show(h.new_value)}` : ''}</div>)}</section>}
    </div>;
  })();

  return <Modal title="Review conflict" close={onClose}>
    {!online && <div className="disclaimer" role="status" style={{ marginBottom: 12 }}>You are offline. Decisions need a connection so the server can check them against the current record; nothing can be changed from here until you are back online.</div>}
    {err && <div className="disclaimer" role="alert" style={{ marginBottom: 12, fontSize: 13, fontWeight: 600, borderLeft: '3px solid #e8a33d', color: '#f3dcae' }}>{err}</div>}
    {body}
    {ask && <div className="modal-note" role="alertdialog" style={{ marginTop: 10 }}>
      <p>{ask.text}</p>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button className="outline-btn" onClick={() => setAsk(null)}>Cancel</button>
        <button className="gradient-btn" disabled={busy} onClick={ask.run}>Confirm</button>
      </div></div>}
  </Modal>;
}
