#!/usr/bin/env node
/**
 * Fails the build when the INITIAL download grows past its budget
 * (Phase 3 item 4 of the 2026-08-17 architecture review).
 *
 * Budgeting the initial payload rather than `dist/` as a whole is the point.
 * Total size is the wrong metric once routes are code-split: adding a lazily
 * loaded admin page makes the bundle bigger and costs the user nothing, while
 * a stray static import in the router costs every visitor on every cold load
 * and may barely move the total. What is measured here is exactly what a
 * first-time visitor must fetch before the app can paint -- the entry chunk,
 * its static imports, and the stylesheets referenced by index.html.
 *
 * Gzip, because that is what the CDN serves and the browser downloads. Raw
 * bytes are reported alongside for debugging only.
 */
import { gzipSync } from 'node:zlib';
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DIST = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'dist');

// Budget with deliberate headroom over today's measured figure, not a
// rubber stamp of it: tight enough that another eagerly imported page or
// charting library trips it, loose enough that ordinary feature work does not.
const BUDGET_GZIP_KB = 200;

if (!existsSync(join(DIST, '.vite', 'manifest.json'))) {
  console.error(
    'bundle-budget: dist/.vite/manifest.json not found. Run `pnpm build` first ' +
      '(build.manifest must be enabled in vite.config.ts).',
  );
  process.exit(1);
}
const manifest = JSON.parse(readFileSync(join(DIST, '.vite', 'manifest.json'), 'utf8'));

const entry = Object.values(manifest).find((c) => c.isEntry);
if (!entry) {
  console.error('bundle-budget: no entry chunk in the manifest.');
  process.exit(1);
}

// The entry chunk plus everything it pulls in synchronously. `imports` is the
// static graph; `dynamicImports` is deliberately NOT followed -- those are the
// lazy route chunks this budget exists to keep out of the initial load.
const initial = new Set();
const walk = (key) => {
  if (initial.has(key)) return;
  initial.add(key);
  for (const dep of manifest[key]?.imports ?? []) walk(dep);
};
walk(Object.keys(manifest).find((k) => manifest[k] === entry));

const files = [];
for (const key of initial) {
  const chunk = manifest[key];
  files.push(chunk.file);
  for (const css of chunk.css ?? []) files.push(css);
}

let totalGzip = 0;
let totalRaw = 0;
const rows = files.map((f) => {
  const bytes = readFileSync(join(DIST, f));
  const gzip = gzipSync(bytes).length;
  totalGzip += gzip;
  totalRaw += bytes.length;
  return { f, raw: bytes.length, gzip };
});

rows.sort((a, b) => b.gzip - a.gzip);
const kb = (n) => (n / 1024).toFixed(1).padStart(7);
console.log('Initial download (entry chunk + static imports + CSS):\n');
console.log('     gzip       raw  file');
for (const r of rows) console.log(`${kb(r.gzip)}kB ${kb(r.raw)}kB  ${r.f}`);

const totalKb = totalGzip / 1024;
console.log(`\n  total: ${totalKb.toFixed(1)} kB gzip (${(totalRaw / 1024).toFixed(1)} kB raw)`);
console.log(`  budget: ${BUDGET_GZIP_KB} kB gzip`);

if (totalKb > BUDGET_GZIP_KB) {
  console.error(
    `\nbundle-budget: FAIL -- initial download is ${totalKb.toFixed(1)} kB gzip, ` +
      `over the ${BUDGET_GZIP_KB} kB budget by ${(totalKb - BUDGET_GZIP_KB).toFixed(1)} kB.\n` +
      'Something is now imported statically that should be lazy. Check src/app/router.tsx\n' +
      'for a page imported at the top instead of through `lazy: () => import(...)`, and\n' +
      'shared components for a new dependency on a charting/markdown library.',
  );
  process.exit(1);
}
console.log(`\nbundle-budget: OK -- ${(BUDGET_GZIP_KB - totalKb).toFixed(1)} kB of headroom.`);
