// Reusable AI pieces (Milestone 5): language picker, "explain this conflict", and a consent-gated record summary.
// They only show TEXT returned by the backend. Nothing here can approve, reject or resolve anything, and no AI text is stored.
import React, { useState } from 'react';
import { Sparkles, Info } from 'lucide-react';
import { LANGUAGES, getLanguage, setLanguage, explainConflict, summariseRecord, describeAiError } from './aiClient';

const selectStyle = { background: '#141218', color: '#eee', border: '1px solid #ffffff1b', borderRadius: 8, padding: '6px 8px', fontSize: 11, outline: 'none', maxWidth: 190 };
export function LanguageSelect({ value, onChange, label = 'Reply language' }) {
  return <select aria-label={label} title={label} style={selectStyle} value={value} onChange={e => { setLanguage(e.target.value); onChange(e.target.value); }}>
    {LANGUAGES.map(([k, t]) => <option key={k} value={k}>{t}</option>)}</select>;
}

const wrap = { whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.6 };
function Failure({ err }) {
  return <div className="disclaimer" role="alert" style={{ marginTop: 8 }}>{err.message}</div>;
}

// "Explain this conflict": sent NO record values and NO identifiers (the server builds the facts). It cannot decide anything.
export function AiExplain({ conflictId }) {
  const [lang, setLang] = useState(getLanguage()), [busy, setBusy] = useState(false), [out, setOut] = useState(null), [err, setErr] = useState(null);
  async function run() {
    setBusy(true); setErr(null);
    try { setOut(await explainConflict({ conflictId, language: lang })); } catch (e) { setOut(null); setErr(describeAiError(e)); } finally { setBusy(false); }
  }
  return <section style={{ marginTop: 10 }} aria-label="AI explanation">
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <button type="button" className="outline-btn" disabled={busy} onClick={run}><Sparkles size={13} /> {busy ? 'Explaining…' : out ? 'Explain again' : 'Explain this conflict (AI)'}</button>
      <LanguageSelect value={lang} onChange={setLang} label="Explanation language" />
    </div>
    {busy && <p style={{ fontSize: 12 }} role="status">The AI assistant is writing an explanation…</p>}
    {err && <Failure err={err} />}
    {out && <div className="modal-note" role="status" style={{ marginTop: 8 }}>
      <div style={wrap}>{out.explanation}</div>
      <small style={{ display: 'block', marginTop: 8, opacity: .8 }}>AI-generated explanation. It was given no patient values or names, it cannot approve, reject or resolve anything, and only the people named by the policy can decide.{out.guarded ? ' (The answer was replaced because it contained clinical advice.)' : ''}</small>
    </div>}
  </section>;
}

// "Summarise this record": explicit consent first; shows exactly which details were shared.
export function AiRecordSummary({ patientId = null, audience = 'patient' }) {
  const [lang, setLang] = useState(getLanguage()), [agree, setAgree] = useState(false), [busy, setBusy] = useState(false), [out, setOut] = useState(null), [err, setErr] = useState(null);
  async function run() {
    setBusy(true); setErr(null);
    try { setOut(await summariseRecord({ patientId, language: lang, consented: agree })); } catch (e) { setOut(null); setErr(describeAiError(e)); } finally { setBusy(false); }
  }
  const who = audience === 'clinician' ? 'this patient\'s' : 'your';
  return <section style={{ marginTop: 10 }} aria-label="AI record summary">
    <b style={{ fontSize: 12 }}>{audience === 'clinician' ? 'Plain-language record summary (AI)' : 'Summarise my record (AI)'}</b>
    <div className="modal-note" style={{ marginTop: 6 }}><Info size={13} /> To write this summary, SAHAAY sends an external AI service {who} <b>age range</b> and <b>clinical details</b> (such as condition, medications, allergies, dosage and last visit date).
      It never sends {who} name, phone number, address, patient ID or identity details. The sharing is recorded in the audit log. Nothing the AI writes is saved.</div>
    <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12, margin: '8px 0' }}><input type="checkbox" checked={agree} onChange={e => setAgree(e.target.checked)} /> I understand and agree to send these details to an external AI service for this summary.</label>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <button type="button" className="outline-btn" disabled={!agree || busy} onClick={run}><Sparkles size={13} /> {busy ? 'Writing summary…' : 'Create summary'}</button>
      <LanguageSelect value={lang} onChange={setLang} label="Summary language" />
    </div>
    {busy && <p style={{ fontSize: 12 }} role="status">The AI assistant is writing a summary…</p>}
    {err && <Failure err={err} />}
    {out && (out.summary
      ? <div className="modal-note" role="status" style={{ marginTop: 8 }}><div style={wrap}>{out.summary}</div>
        <small style={{ display: 'block', marginTop: 8, opacity: .8 }}>AI-written from the details listed here only: {out.fields_shared.join(', ')}. It is not a diagnosis and may contain mistakes. Check anything important with your care team.{out.guarded ? ' (The answer was replaced because it contained clinical advice.)' : ''}</small></div>
      : <div className="info-card" style={{ marginTop: 8 }}><Info /><p>{out.message}</p></div>)}
  </section>;
}
