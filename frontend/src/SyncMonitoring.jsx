// Supervisor "Synchronization monitoring": the SERVER's view (GET /api/sync/summary) plus this device's own queue. Replaces prototype text.
import React, { useEffect, useState } from 'react';
import { AlertTriangle, ArrowDownUp, CloudOff, Users, Info } from 'lucide-react';
import { cachedApi, friendlyError } from './offlineCache';
import { fmtDate } from './fieldLabels';
import { StaleNotice } from './Notices';

export default function SyncMonitoring({ ops, ui }) {
  const { Head, Metric, Panel } = ui;
  const [res, setRes] = useState(null), [err, setErr] = useState('');
  useEffect(() => { cachedApi('/api/sync/summary').then(setRes).catch(e => setErr(friendlyError(e))); }, []);
  const d = res?.data;
  const mine = { waiting: ops.filter(o => o.status === 'pending').length, rejected: ops.filter(o => o.status === 'rejected').length };
  return <div className="content">
    <Head eyebrow="SYSTEM OPERATIONS" title="Synchronization monitoring" sub="What the server has received from all devices, and the state of this device's own queue." />
    {res?.stale && <StaleNotice fetchedAt={res.fetchedAt} />}
    {err && <div className="disclaimer" role="alert">{err}</div>}
    <div className="metric-grid">
      <Metric label="Operations received" value={d ? d.operations_total : '—'} change="Confirmed by the server" I={ArrowDownUp} />
      <Metric label="Devices seen" value={d ? d.devices.length : '—'} change="Recent activity" I={Users} tone="blue" />
      <Metric label="Open conflicts" value={d ? d.pending_conflicts : '—'} change="Waiting for review" I={AlertTriangle} tone="violet" />
      <Metric label="This device's queue" value={mine.waiting} change={mine.rejected ? `${mine.rejected} rejected` : 'Waiting to sync'} I={CloudOff} tone={mine.rejected ? 'red' : 'green'} />
    </div>
    <Panel title="Devices" sub={d ? `Based on the latest ${d.sampled} operations` : 'Loading…'}>
      <div className="table-wrap"><table><thead><tr><th scope="col">Device</th><th scope="col">Operations</th><th scope="col">People</th><th scope="col">Last activity</th></tr></thead><tbody>
        {(d?.devices || []).map(v => <tr key={v.device_id}><td><code>{v.device_id.slice(0, 12)}{v.device_id.length > 12 ? '…' : ''}</code></td><td>{v.operations}</td><td>{v.people}</td><td>{fmtDate(v.last_seen)}</td></tr>)}
      </tbody></table>{d && d.devices.length === 0 && <p style={{ fontSize: 12, padding: 12 }}>No device has synchronised yet.</p>}</div>
    </Panel>
    <div className="info-card"><Info /><p>Changes the server <b>rejected</b> are not stored on the server; they stay on the device that made them (see that device's Sync centre).</p></div>
  </div>;
}
