#!/usr/bin/env node
/** Compact Round 6 lab summaries for docs. Does not invent missing files. */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { decideIntentPrefetch } from './lib/round6_intent_decision.mjs';
import { interleavedGateFailures } from './lib/interleaved_summary.mjs';

const DIR = process.env.OPTIX_PERF_DIR || '/opt/cursor/artifacts/perf';
const OUT = process.env.OPTIX_PERF_SUMMARY || path.join(DIR, 'round6-summary.json');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const COLD_BUDGET_MS = Number(process.env.OPTIX_PERF_COLD_BUDGET_MS || 2500);
const WARM_BUDGET_MS = Number(process.env.OPTIX_PERF_WARM_BUDGET_MS || 1000);
const inputFailures = [];

async function loadJson(name) {
  try {
    return JSON.parse(await readFile(path.join(DIR, name), 'utf8'));
  } catch (error) {
    inputFailures.push(`${name}: ${error instanceof Error ? error.message : String(error)}`);
    return null;
  }
}

async function sha256File(filePath) {
  return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

function pickReady(block) {
  if (!block) return null;
  return {
    n: block.n ?? null,
    ready_n: block.ready_n ?? null,
    timeout_n: block.timeout_n ?? null,
    p50: block.news_content_ready_p50 ?? block.p50 ?? null,
    p75: block.news_content_ready_p75 ?? block.p75 ?? null,
  };
}

const interleaved = await loadJson('round6-interleaved-mobile-ref.json');
const i18n = await loadJson('round6-i18n.json');
const surfaces = await loadJson('round6-surfaces.json');
const bundles = await loadJson('round6-bundles.json');
const provenance = await loadJson('round6-provenance.json');
const expectedCommit = process.env.OPTIX_PERF_PRODUCT_COMMIT
  || provenance?.measured_product_commit
  || interleaved?.product_commit
  || null;
const expectedEntry = process.env.OPTIX_PERF_ENTRY
  || bundles?.files?.optimized_index?.path
  || interleaved?.entry
  || null;
const gateFailures = [...inputFailures];

if (!expectedCommit) gateFailures.push('identity: missing product_commit');
if (!expectedEntry) gateFailures.push('identity: missing frontend entry');
if (interleaved) {
  if (interleaved.ok !== true) gateFailures.push('interleaved: report ok is not true');
  if (interleaved.gate?.ok !== true) gateFailures.push('interleaved: gate ok is not true');
  if (!Array.isArray(interleaved.samples) || interleaved.samples.length !== interleaved.pairs) {
    gateFailures.push(`interleaved: raw samples=${interleaved.samples?.length ?? 'missing'} pairs=${interleaved.pairs ?? 'missing'}`);
  }
  if ((interleaved.pairs || 0) < 20) gateFailures.push(`interleaved: pairs=${interleaved.pairs ?? 'missing'} expected>=20`);
  for (const side of ['opt', 'unopt']) {
    for (const cache of ['cold', 'warm']) {
      const block = interleaved.summary?.[side]?.[cache];
      if (block?.n !== interleaved.pairs || block?.ready_n !== interleaved.pairs) {
        gateFailures.push(`${side}_${cache}: n=${block?.n ?? 'missing'} ready_n=${block?.ready_n ?? 'missing'} pairs=${interleaved.pairs ?? 'missing'}`);
      }
    }
  }
  gateFailures.push(...interleavedGateFailures(interleaved.summary, {
    coldBudgetMs: COLD_BUDGET_MS,
    warmBudgetMs: WARM_BUDGET_MS,
    requireNetworkTelemetry: true,
  }));
}
if (i18n?.gate?.ok !== true) gateFailures.push('i18n: gate ok is not true');
if (i18n && (i18n.ready?.n !== 9 || i18n.ready?.ready_n !== 9 || i18n.cold?.length !== 9)) {
  gateFailures.push(`i18n: n=${i18n.ready?.n ?? 'missing'} ready_n=${i18n.ready?.ready_n ?? 'missing'} raw=${i18n.cold?.length ?? 'missing'} expected=9`);
}
if (surfaces?.gate?.ok !== true) gateFailures.push('surfaces: gate ok is not true');
if (surfaces) {
  for (const [label, block] of [
    ['home', surfaces.pages?.['/']],
    ['earnings', surfaces.pages?.['/earnings']],
    ['nav_home_card', surfaces.first_nav?.home_card],
    ['nav_desktop', surfaces.first_nav?.desktop_nav],
    ['intent_immediate', surfaces.intent?.immediate],
    ['intent_hover_then_click', surfaces.intent?.hover_then_click],
    ['intent_hover_only', surfaces.intent?.hover_only],
    ['extras_no_intent', surfaces.extras?.no_intent],
    ['extras_palette_closed', surfaces.extras?.palette_closed],
  ]) {
    if ((block?.n || 0) < 8 || block?.ready_n !== block?.n || block?.samples?.length !== block?.n) {
      gateFailures.push(`surfaces_${label}: n=${block?.n ?? 'missing'} ready_n=${block?.ready_n ?? 'missing'} raw=${block?.samples?.length ?? 'missing'} expected>=8`);
    }
  }
  if ((surfaces.earnings_scroll?.n || 0) < 8 || surfaces.earnings_scroll?.samples?.length !== surfaces.earnings_scroll?.n) {
    gateFailures.push(`surfaces_earnings_scroll: n=${surfaces.earnings_scroll?.n ?? 'missing'} raw=${surfaces.earnings_scroll?.samples?.length ?? 'missing'} expected>=8`);
  }
  const intentDecision = decideIntentPrefetch(surfaces.intent, {
    expectedN: surfaces.intent?.immediate?.n ?? 8,
  });
  if (intentDecision.decision !== 'keep') {
    gateFailures.push(`surfaces: intent=${intentDecision.decision}/${intentDecision.reason}`);
  }
}
if (!bundles?.first_js_gzip9?.shared) gateFailures.push('bundles: missing first_js_gzip9.shared');
if (!provenance?.measured_product_tree) gateFailures.push('provenance: missing measured_product_tree');
if (!provenance?.seed?.sqlite_sha256) gateFailures.push('provenance: missing seed sqlite_sha256');

for (const [label, value] of [
  ['interleaved', interleaved?.product_commit],
  ['i18n', i18n?.product_commit],
  ['surfaces', surfaces?.product_commit],
  ['provenance', provenance?.measured_product_commit],
]) {
  if (expectedCommit && value !== expectedCommit) {
    gateFailures.push(`${label}: product_commit=${value ?? 'missing'} expected=${expectedCommit}`);
  }
}
for (const [label, value] of [
  ['interleaved', interleaved?.entry],
  ['surfaces', surfaces?.entry],
  ['bundles', bundles?.files?.optimized_index?.path],
]) {
  if (expectedEntry && value !== expectedEntry) {
    gateFailures.push(`${label}: entry=${value ?? 'missing'} expected=${expectedEntry}`);
  }
}
if (expectedEntry && provenance?.frontend_hashes) {
  const recordedHash = provenance.frontend_hashes[expectedEntry];
  if (!recordedHash) {
    gateFailures.push(`provenance: missing hash for ${expectedEntry}`);
  } else {
    try {
      const actualHash = await sha256File(path.join(ROOT, expectedEntry.replace(/^frontend\//, 'frontend/')));
      if (actualHash !== recordedHash) gateFailures.push(`provenance: hash mismatch for ${expectedEntry}`);
    } catch (error) {
      gateFailures.push(`provenance: cannot hash ${expectedEntry}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
}

const report = {
  ok: gateFailures.length === 0,
  writtenAt: new Date().toISOString(),
  product_commit: expectedCommit,
  entry: expectedEntry,
  interleaved: interleaved
    ? {
      pairs: interleaved.pairs,
      comparison_status: interleaved.summary?.comparison_status ?? null,
      measuredAt: interleaved.measuredAt ?? null,
      product_commit: interleaved.product_commit ?? null,
      entry: interleaved.entry ?? null,
      opt_cold: pickReady(interleaved.summary?.opt?.cold),
      opt_warm: pickReady(interleaved.summary?.opt?.warm),
      unopt_cold: pickReady(interleaved.summary?.unopt?.cold),
      unopt_warm: pickReady(interleaved.summary?.unopt?.warm),
      titles: {
        opt: interleaved.summary?.opt?.cold?.titles ?? [],
        unopt: interleaved.summary?.unopt?.cold?.titles ?? [],
      },
    }
    : null,
  i18n: i18n
    ? {
      ...(i18n.invariants || {}),
      ready_n: i18n.ready?.ready_n ?? null,
      ready_class_content_n: i18n.ready?.ready_n ?? null,
      gate_ok: i18n.gate?.ok ?? null,
    }
    : null,
  surfaces: surfaces
    ? {
      home_p75: surfaces.pages?.['/']?.p75 ?? null,
      earnings_p75: surfaces.pages?.['/earnings']?.p75 ?? null,
      gate_ok: surfaces.gate?.ok ?? null,
      first_nav: {
        home_card: surfaces.first_nav?.home_card?.p75 ?? null,
        desktop_nav: surfaces.first_nav?.desktop_nav?.p75 ?? null,
      },
      intent: {
        immediate: surfaces.intent?.immediate?.p75 ?? null,
        hover_then_click: surfaces.intent?.hover_then_click?.p75 ?? null,
        hover_only_chunk: surfaces.intent?.hover_only?.chunk_n ?? null,
        extra_paid_n: surfaces.intent?.hover_only?.extra_paid_n ?? null,
        decision: decideIntentPrefetch(surfaces.intent, {
          expectedN: surfaces.intent?.immediate?.n ?? 8,
        }),
      },
      extras: surfaces.extras
        ? {
          no_intent_chunk_n: surfaces.extras.no_intent_chunk_n ?? null,
          palette_closed_stock_n: surfaces.extras.palette_closed_stock_n ?? null,
        }
        : null,
      earnings_scroll: surfaces.earnings_scroll
        ? {
          before: surfaces.earnings_scroll.chart_before_n,
          after: surfaces.earnings_scroll.chart_after_n,
          stayed: surfaces.earnings_scroll.stayed_n,
        }
        : null,
    }
    : null,
  first_js_gzip9: bundles?.first_js_gzip9 ?? null,
  seed: provenance?.seed
    ? {
      sqlite_sha256: provenance.seed.sqlite_sha256 ?? null,
      sqlite_bytes: provenance.seed.sqlite_bytes ?? null,
    }
    : null,
  gate: {
    ok: gateFailures.length === 0,
    cold_budget_ms: COLD_BUDGET_MS,
    warm_budget_ms: WARM_BUDGET_MS,
    failures: gateFailures,
  },
};

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
console.log(`wrote ${OUT}`);
if (gateFailures.length) process.exit(1);
