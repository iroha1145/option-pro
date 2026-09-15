import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  buildInterleavedSummary,
  interleavedGateFailures,
} from '../../scripts/perf/lib/interleaved_summary.mjs';
import { walkStaticJsGraph } from '../../scripts/perf/lib/round6_bundle_graph.mjs';
import {
  readyGateFailures,
  summarizeReady,
} from '../../scripts/perf/lib/round6_ready_summary.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..', '..');
const summarizeScript = path.join(repo, 'scripts/perf/summarize_round6.mjs');
const bundleScript = path.join(repo, 'scripts/perf/record_round6_bundles.mjs');
const frontendHtml = await readFile(path.join(repo, 'frontend/index.html'), 'utf8');
const entryName = frontendHtml.match(/<script\b[^>]*\btype=["']module["'][^>]*\bsrc=["']\/assets\/(index-[^"']+\.js)["']/i)?.[1]
  || frontendHtml.match(/<script\b[^>]*\bsrc=["']\/assets\/(index-[^"']+\.js)["'][^>]*\btype=["']module["']/i)?.[1];
if (!entryName) throw new Error('frontend/index.html has no module entry');
const entry = `frontend/assets/${entryName}`;
const productCommit = 'test-product-commit';

function browserRows(value, count = 20) {
  return Array.from({ length: count }, () => ({
    news_content_ready_ms: value,
    news_title: '可见新闻',
    lcp: { startTime: Math.max(1, value - 100) },
    rate_limited: 0,
    http_error_n: 0,
    request_failed_n: 0,
  }));
}

function completeInterleaved({ warm = 500 } = {}) {
  const summary = buildInterleavedSummary({
    optCold: browserRows(1200),
    optWarm: browserRows(warm),
    unoptCold: browserRows(1800),
    unoptWarm: browserRows(700),
  });
  return {
    ok: true,
    gate: { ok: true, failures: [] },
    pairs: 20,
    product_commit: productCommit,
    entry,
    measuredAt: '2026-09-15T00:00:00.000Z',
    summary,
    samples: Array.from({ length: 20 }, (_, index) => ({ i: index + 1 })),
  };
}

function completeSurfaces() {
  const ready = { n: 8, ready_n: 8, error_n: 0, empty_n: 0, idle_n: 0, timeout_n: 0, unknown_n: 0, p75: 700, samples: Array.from({ length: 8 }, () => ({ ready_class: 'content' })) };
  return {
    product_commit: productCommit,
    entry,
    gate: { ok: true, failures: [] },
    pages: { '/': ready, '/earnings': ready },
    first_nav: { home_card: ready, desktop_nav: ready },
    intent: {
      immediate: { ...ready, p75: 900 },
      hover_then_click: { ...ready, p75: 600 },
      hover_only: { ...ready, chunk_n: 8, chart_n: 0, extra_paid_n: 0 },
    },
    extras: {
      no_intent: ready,
      palette_closed: ready,
      no_intent_chunk_n: 0,
      palette_closed_stock_n: 0,
    },
    earnings_scroll: { n: 8, chart_before_n: 0, chart_after_n: 8, stayed_n: 8, samples: Array.from({ length: 8 }, () => ({ chart_stayed_mounted: true })) },
  };
}

async function writeInputs(dir, { warm = 500, corrupt = null } = {}) {
  const entryBytes = await readFile(path.join(repo, entry));
  const fixtures = {
    'round6-interleaved-mobile-ref.json': completeInterleaved({ warm }),
    'round6-i18n.json': {
      product_commit: productCommit,
      gate: { ok: true, failures: [] },
      ready: { n: 9, ready_n: 9 },
      cold: Array.from({ length: 9 }, () => ({ ready_class: 'content' })),
      invariants: { zh_no_runtime: true },
    },
    'round6-surfaces.json': completeSurfaces(),
    'round6-bundles.json': {
      files: { optimized_index: { path: entry } },
      first_js_gzip9: { shared: { script_n: 1, gzip9: 1, raw: entryBytes.length } },
    },
    'round6-provenance.json': {
      measured_product_commit: productCommit,
      measured_product_tree: 'test-tree',
      frontend_hashes: { [entry]: createHash('sha256').update(entryBytes).digest('hex') },
      seed: { sqlite_sha256: 'seed-hash', sqlite_bytes: 1 },
    },
  };
  for (const [name, value] of Object.entries(fixtures)) {
    await writeFile(path.join(dir, name), name === corrupt ? '{bad json' : `${JSON.stringify(value)}\n`);
  }
}

function runSummary(dir) {
  return spawnSync(process.execPath, [summarizeScript], {
    cwd: repo,
    encoding: 'utf8',
    env: {
      ...process.env,
      OPTIX_PERF_DIR: dir,
      OPTIX_PERF_SUMMARY: path.join(dir, 'summary.json'),
      OPTIX_PERF_PRODUCT_COMMIT: productCommit,
      OPTIX_PERF_ENTRY: entry,
    },
  });
}

test('content gate rejects rate limits and network failures', () => {
  const summary = summarizeReady([
    { ready_class: 'content', ready_ms: 10, rate_limited: 1 },
    { ready_class: 'content', ready_ms: 11, http_error_n: 1 },
    { ready_class: 'content', ready_ms: 12, request_failed_n: 1 },
  ]);
  assert.deepEqual(readyGateFailures(summary, { expectedN: 3, label: 'surface' }), [
    'surface: rate_limited_n=1',
    'surface: http_error_n=1',
    'surface: request_failed_n=1',
  ]);
});

test('interleaved gate enforces frozen budgets and clean network telemetry', () => {
  const report = completeInterleaved({ warm: 1200 });
  report.summary.opt.cold.request_failed_n = 1;
  const failures = interleavedGateFailures(report.summary, {
    coldBudgetMs: 2500,
    warmBudgetMs: 1000,
    requireNetworkTelemetry: true,
  });
  assert.ok(failures.includes('opt_cold: request_failed_n=1'));
  assert.ok(failures.includes('opt_warm: p75=1200 budget=1000'));

  const missing = buildInterleavedSummary({
    optCold: browserRows(1200).map(({ http_error_n: _http, ...row }) => row),
    optWarm: browserRows(500),
    unoptCold: browserRows(1800),
    unoptWarm: browserRows(700),
  });
  assert.ok(interleavedGateFailures(missing, { requireNetworkTelemetry: true })
    .includes('opt_cold: http_telemetry_n=0 expected=20'));
});

test('surfaces extras retain real readiness and chart gates cover before/after/stayed', async () => {
  const source = await readFile(path.join(repo, 'scripts/perf/measure_round6_surfaces.mjs'), 'utf8');
  assert.doesNotMatch(source, /ready_class:\s*'content',\s*\n\s*ready_ms:\s*1/);
  assert.match(source, /ready_class:\s*ready\.kind/);
  assert.match(source, /chart_before_n=.*expected=0/);
  assert.match(source, /chart_after_n=.*expected=\$\{REPEATS\}/);
  assert.match(source, /stayed_n=.*expected=\$\{REPEATS\}/);
  assert.match(source, /\(\?:eps-chart\|chart\)-/);
});

test('bundle and provenance record app-shell and eps-chart assets', async () => {
  const [bundles, provenance] = await Promise.all([
    readFile(path.join(repo, 'scripts/perf/record_round6_bundles.mjs'), 'utf8'),
    readFile(path.join(repo, 'scripts/perf/record_round6_provenance.mjs'), 'utf8'),
  ]);
  assert.match(bundles, /\^app-shell-/);
  assert.match(bundles, /\^eps-chart-/);
  assert.match(provenance, /\^app-shell-/);
  assert.match(provenance, /\^eps-chart-/);
});

test('bundle shared graph unions the HTML entry and app-shell closures', async (t) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'round6-bundles-'));
  const output = path.join(dir, 'bundles.json');
  t.after(() => rm(dir, { recursive: true, force: true }));
  const result = spawnSync(process.execPath, [bundleScript], {
    cwd: repo,
    encoding: 'utf8',
    env: { ...process.env, OPTIX_PERF_BUNDLES: output },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);

  const assets = await readdir(path.join(repo, 'frontend/assets'));
  const appName = assets.find((name) => /^app-shell-.+\.js$/.test(name))
    || assets.find((name) => /^App-.+\.js$/.test(name));
  assert.ok(appName, 'built App/app-shell asset is required');
  const graphs = await Promise.all([
    walkStaticJsGraph(path.join(repo, 'frontend'), `assets/${entryName}`),
    walkStaticJsGraph(path.join(repo, 'frontend'), `assets/${appName}`),
  ]);
  const expected = new Map(graphs.flatMap((graph) => graph.files.map((file) => [file.path, file])));
  const report = JSON.parse(await readFile(output, 'utf8'));
  const shared = report.first_js_gzip9.shared;
  assert.deepEqual(shared.roots, graphs.map((graph) => graph.entry));
  assert.deepEqual(shared.files.map((file) => file.path), [...expected.keys()].sort());
  assert.equal(shared.script_n, expected.size);
  assert.equal(shared.raw, [...expected.values()].reduce((sum, file) => sum + file.raw, 0));
  assert.equal(shared.gzip9, [...expected.values()].reduce((sum, file) => sum + file.gzip9, 0));
});

test('round6 summary succeeds only with complete, matching, in-budget inputs', async (t) => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'round6-summary-ok-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  await writeInputs(dir);
  const result = runSummary(dir);
  assert.equal(result.status, 0, result.stderr || result.stdout);
  const report = JSON.parse(await readFile(path.join(dir, 'summary.json'), 'utf8'));
  assert.equal(report.ok, true);
  assert.deepEqual(report.gate.failures, []);
});

test('round6 summary fails closed for corrupt input and a warm-budget miss', async (t) => {
  const corruptDir = await mkdtemp(path.join(os.tmpdir(), 'round6-summary-corrupt-'));
  const slowDir = await mkdtemp(path.join(os.tmpdir(), 'round6-summary-slow-'));
  t.after(() => Promise.all([
    rm(corruptDir, { recursive: true, force: true }),
    rm(slowDir, { recursive: true, force: true }),
  ]));

  await writeInputs(corruptDir, { corrupt: 'round6-i18n.json' });
  const corrupt = runSummary(corruptDir);
  assert.equal(corrupt.status, 1, corrupt.stderr || corrupt.stdout);
  assert.match(await readFile(path.join(corruptDir, 'summary.json'), 'utf8'), /round6-i18n\.json/);

  await writeInputs(slowDir, { warm: 1200 });
  const slow = runSummary(slowDir);
  assert.equal(slow.status, 1, slow.stderr || slow.stdout);
  assert.match(await readFile(path.join(slowDir, 'summary.json'), 'utf8'), /opt_warm: p75=1200 budget=1000/);
});
