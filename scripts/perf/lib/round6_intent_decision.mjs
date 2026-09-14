/**
 * Round 6 E（意图预取）去留。必须看 1440 主导航三案。
 * 收益不明显或悬停没有预取到路由块，就回退，不靠口头印象。
 */
export const INTENT_FASTER_MS = 150;
export const INTENT_FASTER_RATIO = 0.08;

export function decideIntentPrefetch(intent) {
  if (!intent?.immediate || !intent?.hover_then_click || !intent?.hover_only) {
    return { decision: 'undecided', reason: 'missing_surfaces_intent' };
  }
  const hoverSamples = intent.hover_only.n ?? 0;
  const chunkN = intent.hover_only.chunk_n ?? 0;
  const chartN = intent.hover_only.chart_n ?? 0;
  if (!hoverSamples) {
    return { decision: 'undecided', reason: 'no_hover_only_samples' };
  }
  if (chunkN === 0) {
    return { decision: 'revert', reason: 'hover_did_not_prefetch_chunk', chunk_n: 0 };
  }
  if (chartN > 0) {
    return { decision: 'revert', reason: 'hover_fetched_chart', chart_n: chartN };
  }

  const immediate = intent.immediate.p75;
  const hovered = intent.hover_then_click.p75;
  if (immediate == null || hovered == null) {
    return { decision: 'undecided', reason: 'missing_p75', chunk_n: chunkN };
  }
  const fasterMs = immediate - hovered;
  const fasterRatio = immediate > 0 ? fasterMs / immediate : 0;
  if (fasterMs >= INTENT_FASTER_MS && fasterRatio >= INTENT_FASTER_RATIO) {
    return {
      decision: 'keep',
      reason: 'hover_then_click_faster',
      faster_ms: fasterMs,
      faster_ratio: fasterRatio,
      chunk_n: chunkN,
    };
  }
  return {
    decision: 'revert',
    reason: 'no_clear_hover_benefit',
    faster_ms: fasterMs,
    faster_ratio: fasterRatio,
    chunk_n: chunkN,
  };
}
