// Writes dist/_headers (Netlify / Cloudflare Pages format) with the security headers from documentation/DEPLOYMENT_GUIDE.md.
// The CSP must name the API origin, which is only known at build time, so the file is generated from VITE_API_URL.
import fs from 'node:fs';
import path from 'node:path';

const api = process.env.VITE_API_URL || '';
if (!/^https:\/\/[^/\s]+$/.test(api)) { console.error('make-headers: VITE_API_URL must be an https:// origin without a path, e.g. https://sahaay-api.onrender.com'); process.exit(1); }
const csp = `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' ${api}; manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`;
const common = [
  `Content-Security-Policy: ${csp}`, 'Strict-Transport-Security: max-age=31536000; includeSubDomains', 'X-Content-Type-Options: nosniff', 'X-Frame-Options: DENY',
  'Referrer-Policy: no-referrer', 'Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()', 'Cross-Origin-Opener-Policy: same-origin'];
const block = (p, extra = []) => `${p}\n${[...common, ...extra].map(l => '  ' + l).join('\n')}\n`;
const out = [
  block('/*'),
  block('/assets/*', ['Cache-Control: public, max-age=31536000, immutable']),
  block('/index.html', ['Cache-Control: no-cache']), block('/sw.js', ['Cache-Control: no-cache']),
  block('/registerSW.js', ['Cache-Control: no-cache']), block('/manifest.webmanifest', ['Cache-Control: no-cache']),
].join('\n');
fs.writeFileSync(path.resolve('dist', '_headers'), out);
console.log('wrote dist/_headers for API origin ' + api);
