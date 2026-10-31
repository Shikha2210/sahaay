# SAHAAY dark dashboard v2

## Run locally
Requires Node.js 18+ (Node 20 LTS recommended).

```powershell
npm.cmd install
npm.cmd run dev
```

Open the localhost URL printed by Vite.

## Included
- Dark charcoal / violet / soft-pink responsive interface inspired by the supplied reference.
- Sign-in screen with Patient, Healthcare Worker and Professional Supervisor workspaces.
- Patient dashboard and interactive SAHAAY AI assistant demo.
- Healthcare worker dashboard, patient directory, patient registration, identity workflow screen and sync centre.
- Professional supervisor overview, conflict queue, identity monitoring, sync monitoring and audit screen.
- Dexie IndexedDB demo patient data and PWA configuration.

## Important limitations
This is a front-end prototype. Sign-in is a demo role selector and is NOT real authentication. Do not use it to grant access to real patient data. Production requires backend authentication, role-based authorization enforced server-side, secure sessions, consent records, audit logging and protected APIs. Aadhaar verification is NOT implemented: QR decoding is not signature verification, and identity attempts must not be marked verified without an authorized, compliant verification service. AI responses are scripted demo guidance, not a medical model. They do not diagnose or prescribe. Use synthetic data only.
