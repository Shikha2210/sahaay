// Edit an existing patient on this device (works offline). The change is saved locally at once and queued; the server decides later.
// If another device changed the same detail meanwhile, a conflict is raised (M3) instead of anything being overwritten.
import React, { useState } from 'react';
import { db } from './db';
import { queueOperation } from './syncEngine';

const FIELDS = [['phone', 'Phone number', 40], ['village', 'Village / locality', 120], ['condition', 'Care note / condition', 500]];

export default function PatientEdit({ patient, onClose, onSaved, notify, Modal }) {
  const [vals, setVals] = useState(Object.fromEntries(FIELDS.map(([k]) => [k, patient[k] ?? ''])));
  const [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const changes = Object.fromEntries(FIELDS.filter(([k]) => (vals[k] || '').trim() !== String(patient[k] ?? '').trim()).map(([k]) => [k, vals[k].trim()]));
  async function save(e) {
    e.preventDefault(); setErr('');
    for (const [k, label, max] of FIELDS) if (k in changes && (!changes[k] || changes[k].length > max)) { setErr(`${label} must be 1–${max} characters.`); return; }
    if (!Object.keys(changes).length) { setErr('Nothing was changed.'); return; }
    setBusy(true);
    try {
      await queueOperation({ patient_id: patient.patient_id, changes });                         // base version = what this device last saw
      await db.patients.update(patient.patient_id, changes);                                    // shown straight away; kept if server data arrives first
      await db.audit.add({ patient_id: patient.patient_id, event_type: 'PATIENT_EDITED_LOCALLY', description: `Edited ${Object.keys(changes).join(', ')} on this device`, created_at: new Date().toISOString() });
      notify('Saved on this device. It will sync when a connection is available.'); await onSaved?.(); onClose();
    } catch (er) { setErr(er.message); setBusy(false); }
  }
  return <Modal title={`Edit ${patient.name}`} close={onClose}>
    <form className="modal-form" onSubmit={save}>
      <div className="modal-note">Patient {patient.patient_id}. Changes are saved on this device and sent when you are online. If someone else changed the same detail, SAHAAY keeps both values for review instead of overwriting.</div>
      {err && <div className="disclaimer" role="alert">{err}</div>}
      {FIELDS.map(([k, label, max]) => <label key={k}>{label}<input value={vals[k]} maxLength={max} onChange={e => setVals({ ...vals, [k]: e.target.value })} /></label>)}
      <button className="gradient-btn" disabled={busy || !Object.keys(changes).length}>{busy ? 'Saving…' : 'Save on this device'}</button>
    </form></Modal>;
}
