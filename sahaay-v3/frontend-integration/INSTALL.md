# Frontend integration install

Copy `App.jsx`, `db.js`, `api.js`, and `syncEngine.js` into your existing Vite project's `src` directory. Keep your existing `main.jsx`, `styles.css`, package.json and public assets.

In the Vite project root create `.env`:

```env
VITE_API_URL=http://127.0.0.1:8000
```

Install missing packages if needed:

```powershell
npm install dexie lucide-react
```

Restart Vite after changing `.env`:

```powershell
npm run dev
```

The supplied App.jsx is integrated against the original component and CSS structure. It replaces the false local `demo_acknowledged` sync with the actual API sync client; migration v2 re-queues those old demo-acknowledged operations. Existing local patient, conflict and audit records are retained.
