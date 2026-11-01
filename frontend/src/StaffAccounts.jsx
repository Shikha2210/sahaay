// Supervisor: create a staff login (health worker, clinician or another supervisor).
//   POST /api/admin/users  (supervisor only; audited as USER_PROVISIONED). Staff cannot register themselves.
// The password is chosen here and handed to the person; it is shown once and never stored in the browser.
import React, { useState } from 'react';
import { Info } from 'lucide-react';
import { api } from './api';
import { friendlyError } from './offlineCache';
import useOnline from './useOnline';
import { NeedsConnection } from './Notices';

const ROLES = [['worker', 'Health worker'], ['clinician', 'Clinician'], ['supervisor', 'Supervisor']];

function strongPassword() {
  const sets = ['abcdefghijkmnopqrstuvwxyz', 'ABCDEFGHJKLMNPQRSTUVWXYZ', '23456789', '!@#$%*+?'];
  const all = sets.join(''), buf = new Uint32Array(16); crypto.getRandomValues(buf);
  const chars = sets.map((s, i) => s[buf[i] % s.length]);
  for (let i = sets.length; i < 16; i++) chars.push(all[buf[i] % all.length]);
  const sh = new Uint32Array(chars.length); crypto.getRandomValues(sh);
  return chars.map((c, i) => [sh[i], c]).sort((a, b) => a[0] - b[0]).map(x => x[1]).join('');
}

export default function StaffAccounts({ notify, ui }) {
  const { Head, Panel } = ui;
  const online = useOnline();
  const blank = { name: '', email: '', role: 'worker', password: '' };
  const [f, setF] = useState(blank), [busy, setBusy] = useState(false), [err, setErr] = useState(''), [done, setDone] = useState(null);
  const set = (k, v) => setF(x => ({ ...x, [k]: v }));
  async function submit(e) {
    e.preventDefault(); setErr(''); setDone(null); setBusy(true);
    try {
      const r = await api('/api/admin/users', { method: 'POST', body: JSON.stringify({ name: f.name.trim(), email: f.email.trim(), password: f.password, role: f.role }) });
      setDone({ email: r.email, role: r.role, password: f.password }); notify('Staff account created.'); setF(blank);
    } catch (er) { setErr(er.status === 409 ? 'That email address already has an account.' : friendlyError(er)); } finally { setBusy(false); }
  }
  return <div className="content">
    <Head eyebrow="ACCESS CONTROL" title="Staff accounts" sub="Create a login for a health worker, clinician or supervisor. Staff cannot sign themselves up; each creation is recorded in the audit history." />
    <div className="info-card"><Info /><p>Give the person their email and the password below in person or by a private channel, and ask them to keep it safe. The password is shown <b>once</b> and cannot be looked up later.</p></div>
    <Panel title="New staff account" sub="Passwords need at least 10 characters">
      <form className="modal-form" onSubmit={submit} style={{ maxWidth: 520 }}>
        {err && <div className="disclaimer" role="alert">{err}</div>}
        <label>Full name<input required minLength={2} maxLength={100} value={f.name} onChange={e => set('name', e.target.value)} autoComplete="off" /></label>
        <label>Email address<input required type="email" value={f.email} onChange={e => set('email', e.target.value)} autoComplete="off" /></label>
        <label>Role<select value={f.role} onChange={e => set('role', e.target.value)}>{ROLES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
        <label>Password<input required minLength={10} maxLength={128} value={f.password} onChange={e => set('password', e.target.value)} autoComplete="new-password" /></label>
        <div><button type="button" className="outline-btn" onClick={() => set('password', strongPassword())}>Generate a strong password</button></div>
        {!online && <NeedsConnection>Creating an account needs a connection.</NeedsConnection>}
        <button className="gradient-btn" disabled={busy || !online || f.password.length < 10}>{busy ? 'Creating…' : 'Create account'}</button>
      </form>
    </Panel>
    {done && <div className="info-card" role="status"><Info /><p>Created <b>{done.role === 'worker' ? 'health worker' : done.role}</b> account for <b>{done.email}</b>.<br />Password (shown once): <code>{done.password}</code></p></div>}
  </div>;
}
