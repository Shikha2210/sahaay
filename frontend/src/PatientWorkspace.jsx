// Patient workspace (Milestone 4): the signed-in patient's OWN record and OWN confirmations, from the real API.
//   GET /api/me/record, GET /api/me/conflicts, POST /api/patients/{id}/confirm-field   (all enforced by the backend).
// Nothing here is hard-coded patient content. An account that is not linked to a record sees an honest empty state.
import React, { useCallback, useEffect, useState } from 'react';
import { CalendarDays, FileText, HeartPulse, Info, ShieldCheck, ArrowUpRight } from 'lucide-react';
import { api } from './api';
import { label, show, fmtDate, greeting } from './fieldLabels';

const RECORD_FIELDS = ['name', 'age', 'phone', 'village', 'condition', 'diagnosis', 'medications', 'allergies', 'treatment_plan', 'blood_pressure', 'blood_glucose', 'dosage', 'last_visit'];

function usePatientData() {
  const [s, setS] = useState({ loading: true, error: '', linked: null, pid: null, record: null, conflicts: [] });
  const load = useCallback(async () => {
    try {
      const [r, c] = await Promise.all([api('/api/me/record'), api('/api/me/conflicts?status=all')]);
      setS({ loading: false, error: '', linked: r.linked, pid: r.patient_id, record: r.record || null, conflicts: c.conflicts || [] });
    } catch (e) {
      setS(prev => ({ ...prev, loading: false, error: e.code === 'network' ? 'You appear to be offline. Your record needs a connection to load.' : e.message }));
    }
  }, []);
  useEffect(() => { load(); }, [load]);
  return { ...s, reload: load };
}

// Real activity only: built from the patient's own conflict records.
function activity(conflicts) {
  const ev = [];
  for (const c of conflicts) {
    ev.push([c.created_at, `A difference was found in your ${label(c.field).toLowerCase()}`, `Two values were recorded (${show(c.value_a)} and ${show(c.value_b)}).`]);
    if (c.my_confirmation) ev.push([c.my_confirmation.confirmed_at, `You confirmed your ${label(c.field).toLowerCase()}`, `You chose ${show(c.my_confirmation.confirmed_value)}.`]);
    if (c.status === 'resolved') ev.push([c.resolved_at, `Your care team updated your ${label(c.field).toLowerCase()}`, `The record now shows ${show(c.selected_value)}.`]);
    if (c.status === 'rejected') ev.push([c.resolved_at, `A proposed change to your ${label(c.field).toLowerCase()} was not applied`, 'Your record kept its current value.']);
  }
  return ev.filter(e => e[0]).sort((a, b) => (a[0] < b[0] ? 1 : -1)).slice(0, 6).map(([d, t, sub]) => [fmtDate(d), t, sub]);
}

export default function PatientWorkspace({ mode, setPage, notify, onChanged, ui }) {
  const { Head, Metric, Panel, Timeline, Badge } = ui;
  const d = usePatientData();
  const [ask, setAsk] = useState(null), [busy, setBusy] = useState(false), [err, setErr] = useState('');

  if (d.loading) return <div className="content"><Head eyebrow="PATIENT SPACE" title="Loading your information…" sub="" /></div>;
  if (d.error) return <div className="content"><Head eyebrow="PATIENT SPACE" title="We could not load your information" sub="" /><div className="info-card" role="alert"><Info /><p>{d.error}</p></div><button className="outline-btn" onClick={() => { d.reload(); }}>Try again</button></div>;
  if (!d.linked) return <div className="content"><Head eyebrow="PATIENT SPACE" title="Your account is not linked to a health record yet" sub="" />
    <div className="info-card"><Info /><p>Your login is not connected to a patient record. A supervisor links it after checking your identity in person. Until then there is no record, appointment or health information to show here.</p></div></div>;

  const pending = d.conflicts.filter(c => c.status === 'pending');
  const needs = pending.filter(c => !c.my_confirmation);
  const rec = d.record || {};

  async function confirm(c, value) {
    setBusy(true); setErr('');
    try {
      await api('/api/patients/' + encodeURIComponent(d.pid) + '/confirm-field', { method: 'POST', body: JSON.stringify({ conflict_id: c.conflict_id, confirmed_value: value }) });
      notify('Thank you - your confirmation was recorded.'); setAsk(null); await d.reload(); if (onChanged) onChanged();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  if (mode === 'confirmations') return <div className="content">
    <Head eyebrow="CONFIRM YOUR INFORMATION" title="Help us keep your record correct" sub="When two different values were recorded for the same detail, you can tell us which one is right. Your care team then applies your answer." />
    {err && <div className="disclaimer" role="alert">{err}</div>}
    {pending.length === 0 && <div className="info-card"><ShieldCheck /><p>Nothing needs your confirmation right now.</p></div>}
    {pending.map(c => <div className="conflict-card" key={c.conflict_id} style={{ alignItems: 'flex-start' }}>
      <div style={{ flex: 1 }}>
        <Badge tone="purple">{label(c.field)}</Badge>
        <h3>Which {label(c.field).toLowerCase()} is correct?</h3>
        {c.my_confirmation
          ? <p><span>You confirmed <b>{show(c.my_confirmation.confirmed_value)}</b>. Your care team will apply it; your record does not change until they do.</span></p>
          : <>
            <p>Two different values are on file. Choose the one that is correct for you.</p>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 8 }}>
              {[c.value_a, c.value_b].map((v, i) => <button key={i} className="outline-btn" disabled={busy} onClick={() => { setErr(''); setAsk({ c, value: v }); }}>{i === 0 ? 'Currently on file: ' : 'Also reported: '}<b>{show(v)}</b></button>)}
            </div>
            {ask && ask.c.conflict_id === c.conflict_id && <div className="modal-note" role="alertdialog" style={{ marginTop: 10 }}>
              <p>Confirm that <b>{show(ask.value)}</b> is the correct {label(c.field).toLowerCase()}?</p>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="gradient-btn" disabled={busy} onClick={() => confirm(c, ask.value)}>Yes, this is correct</button>
                <button className="outline-btn" disabled={busy} onClick={() => setAsk(null)}>Cancel</button>
              </div></div>}
          </>}
      </div>
    </div>)}
    {d.conflicts.filter(c => c.status !== 'pending').length > 0 && <Panel title="Recently closed" sub="Decisions already made by your care team">
      <div className="activity-list">{d.conflicts.filter(c => c.status !== 'pending').map(c => <div className="activity-row" key={c.conflict_id}>
        <div className="activity-main"><b>{label(c.field)}</b><small>{c.status === 'resolved' ? `Record now shows ${show(c.selected_value)}` : 'Proposed change was not applied'} · {fmtDate(c.resolved_at)}</small></div>
        <Badge tone={c.status === 'resolved' ? 'green' : 'neutral'}>{c.status === 'resolved' ? 'Applied' : 'Not applied'}</Badge></div>)}</div></Panel>}
  </div>;

  if (mode === 'record') return <div className="content">
    <Head eyebrow="PERSONAL HEALTH" title="Your health record" sub="The information your care team holds about you." />
    <div className="grid-two">
      <Panel title="Health profile" sub={`Patient ID · ${d.pid}`}>
        <div className="profile-health"><div className="big-avatar">{(rec.name || '?')[0].toUpperCase()}</div><div><b>{show(rec.name)}</b><small>Patient ID · {d.pid}</small>
          <Badge tone={rec.identity_status === 'VERIFIED' ? 'green' : 'amber'}>Identity status: {show(rec.identity_status)}</Badge></div></div>
        <div className="detail-grid">{RECORD_FIELDS.filter(f => f !== 'name' && rec[f] !== undefined && rec[f] !== null && rec[f] !== '').map(f => <div key={f}><small>{label(f)}</small><b>{show(rec[f])}</b></div>)}
          <div><small>Last updated</small><b>{fmtDate(rec.updated_at)}</b></div></div>
      </Panel>
      <Panel title="Recent activity" sub="What changed on your record">
        {activity(d.conflicts).length ? <Timeline items={activity(d.conflicts)} /> : <p style={{ fontSize: 12 }}>No activity to show yet.</p>}
      </Panel>
    </div>
    {needs.length > 0 && <div className="info-card"><Info /><p>{needs.length} detail{needs.length === 1 ? '' : 's'} on your record need{needs.length === 1 ? 's' : ''} your confirmation. <button className="text-btn" onClick={() => setPage('confirmations')}>Review now <ArrowUpRight size={14} /></button></p></div>}
  </div>;

  return <div className="content">
    <Head eyebrow={`PATIENT SPACE · ${greeting()}`} title="Your health, at a glance." sub="A clear view of your own record. Your information stays yours." action={<button className="gradient-btn" onClick={() => setPage('assistant')}>Ask SAHAAY AI</button>} />
    <div className="metric-grid">
      <Metric label="Your record" value={d.pid} change="Linked to your account" I={HeartPulse} />
      <Metric label="Needs your confirmation" value={needs.length} change={needs.length ? 'Please review' : 'Nothing waiting'} I={ShieldCheck} tone="violet" />
      <Metric label="Last updated" value={rec.updated_at ? new Date(rec.updated_at).toLocaleDateString() : '—'} change="Record update" I={FileText} tone="blue" />
      <Metric label="Identity status" value={show(rec.identity_status)} change="Set by your care team" I={CalendarDays} tone="green" />
    </div>
    <div className="grid-two">
      <Panel title="Your record" sub="Key details" action={<button className="text-btn" onClick={() => setPage('my-health')}>View record <ArrowUpRight size={14} /></button>}>
        <div className="detail-grid">{['name', 'age', 'phone', 'village'].filter(f => rec[f] !== undefined && rec[f] !== null && rec[f] !== '').map(f => <div key={f}><small>{label(f)}</small><b>{show(rec[f])}</b></div>)}</div>
      </Panel>
      <Panel title="Recent activity" sub="From your own record" action={needs.length ? <button className="text-btn" onClick={() => setPage('confirmations')}>Confirm <ArrowUpRight size={14} /></button> : null}>
        {activity(d.conflicts).length ? <Timeline items={activity(d.conflicts)} /> : <p style={{ fontSize: 12 }}>No activity to show yet.</p>}
      </Panel>
    </div>
    <div className="disclaimer">Identity status is set by your care team. This app does not perform Aadhaar verification.</div>
  </div>;
}
