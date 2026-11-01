// Supervisor: link a patient's login to a patient record (Milestone 4).
//   GET /api/admin/patient-accounts, POST /api/admin/patients/{id}/link-account
// The backend requires a recorded identity-check attestation, refuses to move an already-linked account or record, and audits the link.
import React, { useCallback, useEffect, useState } from 'react';
import { Info } from 'lucide-react';
import { api } from './api';
import { fmtDate } from './fieldLabels';

const METHODS = [['in_person_document', 'Checked in person against a document'], ['trusted_introduction', 'Introduced by a trusted colleague'], ['other', 'Other (explain below)']];

export default function SupervisorAccounts({ patients, notify, ui }) {
  const { Head, Badge, Modal, Panel } = ui;
  const [rows, setRows] = useState(null), [err, setErr] = useState(''), [form, setForm] = useState(null), [q, setQ] = useState('');
  const load = useCallback(async () => {
    try { setRows(await api('/api/admin/patient-accounts')); setErr(''); }
    catch (e) { setErr(e.code === 'network' ? 'You appear to be offline. Account linking needs a connection.' : e.message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const unlinked = (rows || []).filter(r => !r.linked_patient_id).length;
  const matches = form ? patients.filter(p => (p.name + ' ' + p.patient_id + ' ' + (p.village || '')).toLowerCase().includes(q.toLowerCase())).slice(0, 50) : [];
  return <div className="content">
    <Head eyebrow="IDENTITY GOVERNANCE" title="Patient accounts" sub="Connect a patient's login to their health record, only after you have checked who they are. The check is recorded and the link is audited." />
    <div className="info-card"><Info /><p>Linking gives the person access to <b>that record only</b>. You must confirm that you verified their identity yourself; SAHAAY cannot verify it for you. A link cannot be silently changed: an account or record that is already linked is refused.</p></div>
    {err && <div className="disclaimer" role="alert">{err}</div>}
    <Panel title="Patient logins" sub={rows ? `${rows.length} account${rows.length === 1 ? '' : 's'} · ${unlinked} not linked yet` : 'Loading…'}>
      <div className="table-wrap"><table><thead><tr><th>Account</th><th>Email</th><th>Record</th><th>Identity check</th><th /></tr></thead><tbody>
        {(rows || []).map(r => <tr key={r.email}>
          <td><b>{r.name}</b><small>created {fmtDate(r.created_at)}</small></td><td>{r.email}</td>
          <td>{r.linked_patient_id ? <Badge tone="green">{r.linked_patient_id}</Badge> : <Badge tone="amber">not linked</Badge>}</td>
          <td>{r.identity_check_method ? r.identity_check_method.replace(/_/g, ' ') : '—'}{r.linked_at ? <small>{fmtDate(r.linked_at)}</small> : null}</td>
          <td>{!r.linked_patient_id && <button className="outline-btn" onClick={() => { setQ(''); setForm({ account: r, patient_id: '', method: 'in_person_document', note: '', verified: false, error: '', busy: false }); }}>Link to a record</button>}</td></tr>)}
      </tbody></table>{rows && rows.length === 0 && <p style={{ fontSize: 12, padding: 12 }}>No patient accounts have been created yet.</p>}</div>
    </Panel>
    {form && <Modal title="Link patient account" close={() => setForm(null)}>
      <form className="modal-form" onSubmit={async e => {
        e.preventDefault(); setForm(f => ({ ...f, busy: true, error: '' }));
        try {
          await api('/api/admin/patients/' + encodeURIComponent(form.patient_id) + '/link-account', { method: 'POST', body: JSON.stringify({ email: form.account.email, identity_verified: form.verified, identity_check_method: form.method, identity_check_note: form.note.trim() }) });
          notify('Account linked and the identity check recorded.'); setForm(null); await load();
        } catch (er) { setForm(f => ({ ...f, busy: false, error: er.message })); }
      }}>
        <div className="modal-note">Account: <b>{form.account.name}</b> ({form.account.email})</div>
        {form.error && <div className="disclaimer" role="alert">{form.error}</div>}
        <label>Find the patient record<input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, patient ID or village" /></label>
        <label>Patient record<select required value={form.patient_id} onChange={e => setForm({ ...form, patient_id: e.target.value })}>
          <option value="">Select a record…</option>{matches.map(p => <option key={p.patient_id} value={p.patient_id}>{p.name} · {p.patient_id}{p.village ? ' · ' + p.village : ''}</option>)}</select></label>
        <label>How was identity checked?<select value={form.method} onChange={e => setForm({ ...form, method: e.target.value })}>{METHODS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        <label>Note for the audit record (no ID numbers)<input required minLength={5} maxLength={500} value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} placeholder="e.g. Checked in person at the clinic" /></label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12 }}><input type="checkbox" checked={form.verified} onChange={e => setForm({ ...form, verified: e.target.checked })} /> I have verified this person's identity myself and they are the person this record belongs to.</label>
        <button className="gradient-btn" disabled={form.busy || !form.verified || !form.patient_id || form.note.trim().length < 5}>{form.busy ? 'Linking…' : 'Link account'}</button>
      </form></Modal>}
  </div>;
}
