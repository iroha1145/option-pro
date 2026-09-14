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

const report = {
  writtenAt: new Date().toISOString(),
  interleaved: interleaved
    ? {
      pairs: interleaved.pairs,
      comparison_status: interleaved.summary?.comparison_status ?? null,
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
  i18n: i18n?.invariants ?? null,
  surfaces: surfaces
    ? {
      home_p75: surfaces.pages?.['/']?.p75 ?? null,
      earnings_p75: surfaces.pages?.['/earnings']?.p75 ?? null,
      first_nav: {
        home_card: surfaces.first_nav?.home_card?.p75 ?? null,
        desktop_nav: surfaces.first_nav?.desktop_nav?.p75 ?? null,
      },
      intent: {
        immediate: surfaces.intent?.immediate?.p75 ?? null,
        hover_then_click: surfaces.intent?.hover_then_click?.p75 ?? null,
        hover_only_chunk: surfaces.intent?.hover_only?.chunk_n ?? null,
        decision: decideIntentPrefetch(surfaces.intent),
      },
      earnings_scroll: surfaces.earnings_scroll
        ? {
          before: surfaces.earnings_scroll.chart_before_n,
          after: surfaces.earnings_scroll.chart_after_n,
          stayed: surfaces.earnings_scroll.stayed_n,
        }
        : null,
    }
    : null,
};

await mkdir(path.dirname(OUT), { recursive: true });
await writeFile(OUT, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
console.log(`wrote ${OUT}`);
