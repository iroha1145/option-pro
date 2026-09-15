#!/usr/bin/env node
/** Record production frontend/ hashes and gzip9 sizes after a live build. */
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzip9Size, walkStaticJsGraph } from './lib/round6_bundle_graph.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const FRONTEND = path.join(ROOT, 'frontend');
const OUT = process.env.OPTIX_PERF_BUNDLES
  || '/opt/cursor/artifacts/perf/round6-bundles.json';

function firstMatch(names, re) {
  return names.find((name) => re.test(name)) || null;
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

const indexRel = indexMatch ? `assets/${indexMatch[1]}` : null;
const shared = indexRel ? await walkStaticJsGraph(FRONTEND, indexRel) : null;
const homeGraph = await walkStaticJsGraph(FRONTEND, firstMatch(assets, /^Home-.+\.js$/) ? `assets/${firstMatch(assets, /^Home-.+\.js$/)}` : indexRel);
const earningsGraph = await walkStaticJsGraph(FRONTEND, firstMatch(assets, /^Earnings-.+\.js$/) ? `assets/${firstMatch(assets, /^Earnings-.+\.js$/)}` : indexRel);
const newsGraph = await walkStaticJsGraph(FRONTEND, firstMatch(assets, /^Catalysts-.+\.js$/) ? `assets/${firstMatch(assets, /^Catalysts-.+\.js$/)}` : indexRel);
const sharedPaths = new Set(shared?.files.map((file) => file.path) || []);
const plus = (graph) => ({
  route_only_gzip9: graph.files.filter((file) => !sharedPaths.has(file.path)).reduce((sum, file) => sum + file.gzip9, 0),
  with_shared_gzip9: (shared?.gzip9 || 0) + graph.files.filter((file) => !sharedPaths.has(file.path)).reduce((sum, file) => sum + file.gzip9, 0),
  script_n: new Set([...(shared?.files.map((file) => file.path) || []), ...graph.files.map((file) => file.path)]).size,
});

const report = {
  measured_at: new Date().toISOString(),
  note: 'Current production frontend/ after the live Vite build. Dictionary gzip is not main-bundle savings. zh_critical_js_gzip_approx is entry+App only; first_js_gzip9 includes the shared static import graph.',
  files,
  zh_critical_js_gzip_approx: {
    optimized_index_plus_app: (files.optimized_index?.gzip9 || 0) + (files.optimized_app?.gzip9 || 0),
    note: 'Chinese mode does not download runtime-en or runtime-ja. This is not the complete first-download JS.',
  },
  first_js_gzip9: {
    shared: shared ? { script_n: shared.script_n, gzip9: shared.gzip9, raw: shared.raw } : null,
    home: plus(homeGraph),
    earnings: plus(earningsGraph),
    catalysts: plus(newsGraph),
    note: 'gzip -9 of statically imported JS only. Excludes CSS, JSON, fonts, data, and later intent prefetch.',
  },
};

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
console.log(`wrote ${OUT}`);
