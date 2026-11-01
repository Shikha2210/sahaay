// The patient's AI assistant page (Milestone 5). Same layout and styling as before; now wired to the real backend with a real
// availability badge, language choice, loading / error / rate-limit / offline states and privacy notes.
// General health information only: the chat request never contains any patient data (the server also removes identifiers typed in).
import React, { useEffect, useRef, useState } from 'react';
import { ArrowUpRight, Send, Sparkles } from 'lucide-react';
import { askAssistant, aiStatus, describeAiError, getLanguage } from './aiClient';
import { AiRecordSummary, LanguageSelect } from './AiParts';

const GREETING = { from: 'ai', tag: 'greeting', text: "Hi! I'm SAHAAY, your health information assistant. I can explain common health terms, help you prepare questions for your healthcare worker and share general wellness information. I can't diagnose or advise on medicines. What would you like help with?" };
const PROMPTS = [['Explain a health term', "What does 'blood pressure' mean?"], ['Prepare for appointment', 'Help me prepare questions to ask at a doctor visit.'], ['Healthy habits', 'What are some healthy habits for hot weather?']];

export default function AiAssistant({ notify, ui }) {
  const { Head, Badge } = ui;
  const [msgs, setMsgs] = useState([GREETING]), [draft, setDraft] = useState(''), [busy, setBusy] = useState(false), [lang, setLang] = useState(getLanguage());
  const [status, setStatus] = useState(null), [online, setOnline] = useState(typeof navigator === 'undefined' ? true : navigator.onLine), [last, setLast] = useState('');
  const end = useRef(null);

  useEffect(() => { aiStatus().then(setStatus).catch(() => setStatus({ configured: null })); }, []);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener('online', on); window.addEventListener('offline', off);
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off); };
  }, []);
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'end' }); }, [msgs, busy]);

  async function send(text) {
    const q = (text ?? draft).trim();
    if (!q || busy) return;
    const history = msgs.filter(m => m.tag === 'ai' || m.from === 'user').slice(-6).map(m => ({ role: m.from === 'user' ? 'user' : 'assistant', text: m.text }));
    setDraft(''); setLast(q); setMsgs(m => [...m, { from: 'user', text: q }]); setBusy(true);
    try {
      const r = await askAssistant({ message: q, history, language: lang });
      setMsgs(m => [...m, { from: 'ai', tag: r.emergency ? 'emergency' : 'ai', text: r.answer, redactions: r.redactions, guarded: r.guarded }]);
    } catch (e) {
      const d = describeAiError(e);
      setMsgs(m => [...m, { from: 'ai', tag: 'error', text: d.message, retry: d.kind !== 'config' && d.kind !== 'auth' }]);
    } finally { setBusy(false); }
  }

  const badge = !online ? <Badge tone="amber">Offline</Badge> : status === null ? <Badge tone="neutral">Checking…</Badge>
    : status.configured ? <Badge tone="green">Assistant available</Badge> : <Badge tone="amber">{status.configured === false ? 'Not set up on this server' : 'Status unknown'}</Badge>;

  return <div className="content"><Head eyebrow="SAHAAY AI · PATIENT SUPPORT" title="A little help, whenever you need it." sub="Ask general health questions, understand common terms or prepare for your next appointment. Replies are general information, never a diagnosis." action={badge} />
    <div className="assistant-layout">
      <div className="chat-panel">
        <div className="chat-top"><div className="ai-avatar"><Sparkles /></div><div><b>SAHAAY AI assistant</b><small>● {online ? 'Here to help' : 'Offline'} · General information only</small></div>
          <LanguageSelect value={lang} onChange={setLang} /><Badge tone="purple">AI support</Badge></div>
        <div className="chat-messages" role="log" aria-live="polite">
          {msgs.map((m, i) => <div className={'chat-line ' + m.from} key={i}>
            {m.from === 'ai' && <div className="chat-avatar"><Sparkles size={14} /></div>}
            <div className="bubble" style={{ whiteSpace: 'pre-wrap', ...(m.tag === 'emergency' ? { border: '1px solid #e8a33d' } : null), ...(m.tag === 'error' ? { opacity: .95, borderLeft: '3px solid #e0709b' } : null) }}>
              {m.text}
              {m.tag === 'ai' && <small style={{ display: 'block', marginTop: 6, opacity: .7 }}>AI-generated general information · not medical advice or a diagnosis{m.guarded ? ' · replaced because it contained clinical advice' : ''}</small>}
              {m.tag === 'emergency' && <small style={{ display: 'block', marginTop: 6, opacity: .8 }}>Safety message from SAHAAY (not AI-generated)</small>}
              {m.redactions && Object.keys(m.redactions).length > 0 && <small style={{ display: 'block', marginTop: 6, opacity: .8 }}>For your privacy we removed before sending: {Object.entries(m.redactions).map(([k, n]) => `${k}${n > 1 ? ' ×' + n : ''}`).join(', ')}</small>}
              {m.tag === 'error' && m.retry && last && <button className="text-btn" style={{ display: 'block', marginTop: 6 }} disabled={busy} onClick={() => send(last)}>Try again</button>}
            </div></div>)}
          {busy && <div className="chat-line ai" role="status"><div className="chat-avatar"><Sparkles size={14} /></div><div className="bubble">Thinking…</div></div>}
          <div ref={end} />
        </div>
        <div className="suggestions">{PROMPTS.map(([label, text]) => <button key={label} disabled={busy} onClick={() => setDraft(text)}>{label}</button>)}</div>
        <div className="chat-input"><input aria-label="Your question" value={draft} disabled={busy || !online} maxLength={2000} onChange={e => setDraft(e.target.value)} onKeyDown={e => e.key === 'Enter' && send()} placeholder={online ? 'Ask SAHAAY anything…' : 'Offline - the assistant needs a connection'} />
          <button className="send-btn" aria-label="Send" disabled={busy || !online || !draft.trim()} onClick={() => send()}><Send size={16} /></button></div>
        <small style={{ padding: '6px 14px 12px', opacity: .7, fontSize: 10 }}>Please don't type names, phone numbers or ID numbers. Anything that looks like an ID or phone number is removed before it is sent. Your conversation is not saved.</small>
      </div>
      <aside className="assistant-aside"><div className="ai-orb"><Sparkles size={28} /></div><h3>Support, not a substitute.</h3>
        <p>SAHAAY AI provides general information. It does not diagnose conditions, prescribe medicines or replace a clinician.</p><hr />
        <b>Need urgent help?</b><p>If you may be experiencing a medical emergency, call your local emergency number (112 in India) or go to the nearest hospital now.</p><hr />
        <AiRecordSummary audience="patient" />
        <button className="outline-btn" style={{ marginTop: 12 }} onClick={() => notify('Care team contact details will appear when connected.')}>Contact my care team <ArrowUpRight size={14} /></button></aside>
    </div></div>;
}
