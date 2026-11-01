// Renders the existing SAHAAY icon (frontend/public/icon.svg: purple tile + white cross) to the PNG sizes installable web apps need.
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const pub = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'frontend', 'public');
const CROSS = '<path d="M29 12h6v17h17v6H35v17h-6V35H12v-6h17z" fill="#fff"/>';
const svgs = {
  // "any": the existing artwork unchanged (rounded tile)
  any: fs.readFileSync(path.join(pub, 'icon.svg'), 'utf8'),
  // "maskable": full-bleed tile, cross shrunk into the central safe zone (platforms crop to circles/squircles)
  maskable: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#a77bcb"/><g transform="translate(32 32) scale(.62) translate(-32 -32)">${CROSS}</g></svg>`,
  // Apple touch icon: square, iOS applies its own rounding
  apple: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="#a77bcb"/><g transform="translate(32 32) scale(.8) translate(-32 -32)">${CROSS}</g></svg>`,
};
const jobs = [['any', 192, 'icon-192.png'], ['any', 512, 'icon-512.png'], ['maskable', 192, 'icon-maskable-192.png'], ['maskable', 512, 'icon-maskable-512.png'], ['apple', 180, 'apple-touch-icon.png']];
const browser = await chromium.launch({ channel: 'msedge', headless: true });
for (const [kind, size, file] of jobs) {
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svgs[kind].replace('<svg ', `<svg width="${size}" height="${size}" style="display:block" `)}</body></html>`);
  await page.screenshot({ path: path.join(pub, file), omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
  await page.close(); console.log('wrote', file, size);
}
await browser.close();
