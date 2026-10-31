# DEPENDENCY_AUDIT.md — frontend `npm audit` (2026-11-01, Milestone 2)

## Before: 15 vulnerabilities (2 low, 10 moderate, 1 high, 2 critical)
| Severity | Package | Direct? | In production bundle? | Source |
|---|---|---|---|---|
| **critical** | `vitest` 2.1.9 | **direct (devDependency I added in Milestone 1)** | No — test runner | Vitest UI server arbitrary file read/execute (only when the Vitest *UI* server is running), mocker path traversal |
| **critical** | `tinypool` (via vitest) | transitive | No | Prototype-pollution → RCE gadget in worker options |
| **high** | `vite` 5.4.21 (nested **under vitest 2**, not our Vite 6) | transitive | No | Path traversal in optimized-deps `.map` handling; `launch-editor` NTLM hash disclosure on Windows |
| moderate | `@vitest/mocker`, `vite-node`, `esbuild` (nested under vitest) | transitive | No | dev-server request exposure, mocker issues |
| low/moderate ×9 | `workbox-build` → `@rollup/plugin-node-resolve` → `deepmerge` (low); `@trickfilm400/rollup-plugin-off-main-thread` → `ejs` → `jake` → `filelist` → `minimatch` (moderate); `glob` → `minimatch` (moderate) | transitive of **`vite-plugin-pwa`** (production dependency list) | **No** — build-time only (generates the service worker) | prototype poisoning, ReDoS/DoS, glob abort bypass |

## Action taken
1. **`npm install -D vitest@5.0.5`** (major upgrade of my own test tool; supports our Vite 6.4 and Node 24; peer ranges checked first). This removed the 2 critical, the 1 high and the 4 vitest-related moderate findings.
2. `npm audit fix` (**no `--force`**) was run: it changed nothing, because the fixes depend on newer `workbox-build` releases that do not exist yet (`workbox-build@7.4.1` is the latest; `vite-plugin-pwa@2.0.0` also depends on 7.4.1; `deepmerge@4.3.1` is the latest).
3. Production build (`npm run build`) and all tests re-run after the change: pass.

## After: 9 vulnerabilities (2 low, 7 moderate) — accepted, with reasons
All nine are inside `workbox-build`, which only runs on the developer/CI machine during `vite build`. None of that code is shipped to browsers (checked: they are not in the built JS; bundle contains react, react-dom, dexie, lucide icons and our code). Exploiting them requires attacker-controlled input to the build tool (e.g. a malicious glob pattern or EJS template in our own build config), which we don't accept from outside. **Not exploitable in the running application.**
Mitigation/ follow-up: re-run `npm audit` every milestone; update `vite-plugin-pwa`/`workbox-build` when a patched release exists; never run builds on untrusted branches with secrets.

## Backend (Python) dependencies
`pip-audit` has **not** been run (not installed; no network scan performed). Known maintenance concerns noted in BUGS_AND_GAPS X11 (`python-jose`, `passlib`). To do in Milestone 6.
