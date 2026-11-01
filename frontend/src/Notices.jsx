// Small shared notices for offline behaviour (Milestone 6).
import React from 'react';
import { CloudOff } from 'lucide-react';

// Shown above read-only information that came from this device's saved copy because the server could not be reached.
export function StaleNotice({ fetchedAt }) {
  const when = fetchedAt ? new Date(fetchedAt).toLocaleString() : 'earlier';
  return <div className="info-card" role="status"><CloudOff /><p>You are offline or the server cannot be reached. Showing information saved on this device ({when}); it may be out of date. Actions that need the server are unavailable until you are back online.</p></div>;
}
// Explains why an action is unavailable instead of silently doing nothing.
export function NeedsConnection({ children = 'This action needs a connection so the server can check it.' }) {
  return <small role="status" style={{ display: 'block', opacity: .85, marginTop: 6 }}>{children}</small>;
}
