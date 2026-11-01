// Production REHEARSAL static server: serves frontend/dist over HTTPS with the same security headers documented in
// documentation/DEPLOYMENT_GUIDE.md (CSP, HSTS, frame/permissions policies, cache rules). Used only on this machine with a throw-away
// self-signed certificate; real hosting (Netlify/Cloudflare/nginx...) applies the same headers itself.
// usage: node serve_prod.mjs <port> <https://api-origin> <cert.pem> <key.pem>
import https from 'node:https';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [port, api, certFile, keyFile] = process.argv.slice(2);
const dist = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'frontend', 'dist');
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.webmanifest': 'application/manifest+json', '.json': 'application/json' };
export const CSP = `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' ${api}; manifest-src 'self'; worker-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`;

https.createServer({ cert: fs.readFileSync(certFile), key: fs.readFileSync(keyFile) }, (req, res) => {
  let p = decodeURIComponent(new URL(req.url, 'https://x').pathname);
  let file = path.join(dist, p === '/' ? 'index.html' : p);
  if (!file.startsWith(dist) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { file = path.join(dist, 'index.html'); p = '/index.html'; }   // single-page-app fallback
  const ext = path.extname(file);
  const immutable = p.startsWith('/assets/');
  res.writeHead(200, {
    'Content-Type': TYPES[ext] || 'application/octet-stream',
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',     // index.html / sw.js must always be revalidated
    'Content-Security-Policy': CSP, 'Strict-Transport-Security': 'max-age=31536000; includeSubDomains', 'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
    'Cross-Origin-Opener-Policy': 'same-origin',
  });
  fs.createReadStream(file).pipe(res);
}).listen(Number(port), () => console.log('rehearsal frontend on https://localhost:' + port));
