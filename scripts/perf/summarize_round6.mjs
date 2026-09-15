#!/usr/bin/env node
/** Compact Round 6 lab summaries for docs. Does not invent missing files. */
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { decideIntentPrefetch } from './lib/round6_intent_decision.mjs';

const DIR = process.env.OPTIX_PERF_DIR || '/opt/cursor/artifacts/perf';
const OUT = process.env.OPTIX_PERF_SUMMARY || path.join(DIR, 'round6-summary.json');

async function loadJson(name) {
  try {
    return JSON.parse(await readFile(path.join(DIR, name), 'utf8'));
  } catch {
    return null;
  }
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

// Old n=20 (index-uc86EHir.js) wrote the same filename. Only treat a file as
// the V2 remasurement when it was produced after the 12e78b87 surfaces run.
const INTERLEAVED_V2_SINCE_MS = Date.parse('2026-09-15T02:47:00.000Z');
const interleavedMeasuredMs = Date.parse(interleaved?.measuredAt || '') || 0;
const interleavedIsV2 = interleavedMeasuredMs >= INTERLEAVED_V2_SINCE_MS;

const report = {
  writtenAt: new Date().toISOString(),
  product_commit: provenance?.measured_product_commit
    || interleaved?.product_commit
    || null,
  entry: bundles?.files?.optimized_index?.path || null,
  interleaved: interleavedIsV2
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
  interleaved_note: interleavedIsV2
    ? null
    : 'v2 n=20 remasurement in progress on f2c05331 / index-DDX2TFDT.js; historical index-uc86EHir.js numbers stay in r6-interleaved-n20.json',
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
        decision: decideIntentPrefetch(surfaces.intent),
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
};

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
console.log(`wrote ${OUT}`);
