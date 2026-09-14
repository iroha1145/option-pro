#!/usr/bin/env node
/** Record production frontend/ hashes and gzip9 sizes after a live build. */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const execFileAsync = promisify(execFile);

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const FRONTEND = path.join(ROOT, 'frontend');
const OUT = process.env.OPTIX_PERF_BUNDLES
  || '/opt/cursor/artifacts/perf/round6-bundles.json';

function firstMatch(names, re) {
  return names.find((name) => re.test(name)) || null;
}

async function gzip9Size(filePath) {
  // Match the historical table: `gzip -9 -c`, not Node zlib headers.
  const { stdout } = await execFileAsync('gzip', ['-9', '-c', filePath], {
    encoding: 'buffer',
    maxBuffer: 32 * 1024 * 1024,
  });
  return stdout.length;
}

async function describe(rel) {
  if (!rel) return null;
  const abs = path.join(FRONTEND, rel);
  const raw = (await stat(abs)).size;
  const text = await readFile(abs, 'utf8');
  return {
    path: `frontend/${rel}`,
    raw,
    gzip9: await gzip9Size(abs),
    contains_ja: /サポート|スキップして本文へ/.test(text),
    contains_en_skip_link: /Skip to main content/.test(text),
  };
}

const html = await readFile(path.join(FRONTEND, 'index.html'), 'utf8');
const indexMatch = html.match(/\/assets\/(index-[^"]+\.js)/);
const cssMatch = html.match(/\/assets\/(index-[^"]+\.css)/);
const assets = await readdir(path.join(FRONTEND, 'assets'));
const files = {
  optimized_index: await describe(indexMatch ? `assets/${indexMatch[1]}` : null),
  optimized_css: await describe(cssMatch ? `assets/${cssMatch[1]}` : null),
  optimized_app: await describe(firstMatch(assets, /^App-.+\.js$/) && `assets/${firstMatch(assets, /^App-.+\.js$/)}`),
  optimized_runtime_en: await describe(firstMatch(assets, /^runtime-en-.+\.js$/) && `assets/${firstMatch(assets, /^runtime-en-.+\.js$/)}`),
  optimized_runtime_ja: await describe(firstMatch(assets, /^runtime-ja-.+\.js$/) && `assets/${firstMatch(assets, /^runtime-ja-.+\.js$/)}`),
  optimized_chart: await describe(firstMatch(assets, /^chart-.+\.js$/) && `assets/${firstMatch(assets, /^chart-.+\.js$/)}`),
  optimized_earnings_page: await describe(firstMatch(assets, /^Earnings-.+\.js$/) && `assets/${firstMatch(assets, /^Earnings-.+\.js$/)}`),
  optimized_eps_chart_wrapper: await describe(firstMatch(assets, /^EpsHatchChart-.+\.js$/) && `assets/${firstMatch(assets, /^EpsHatchChart-.+\.js$/)}`),
};

const report = {
  measured_at: new Date().toISOString(),
  note: 'Current production frontend/ after the live Vite build. Dictionary gzip is not main-bundle savings.',
  files,
  zh_critical_js_gzip_approx: {
    optimized_index_plus_app: (files.optimized_index?.gzip9 || 0) + (files.optimized_app?.gzip9 || 0),
    note: 'Chinese mode does not download runtime-en or runtime-ja.',
  },
};

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
console.log(`wrote ${OUT}`);
