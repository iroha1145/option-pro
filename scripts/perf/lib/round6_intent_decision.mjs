/**
 * Round 6 E（意图预取）去留。必须看 1440 主导航三案。
 * 收益不明显、样本不齐、悬停没有预取到路由块、或有额外付费请求，就回退。
 */
export const INTENT_FASTER_MS = 150;
export const INTENT_FASTER_RATIO = 0.08;

export function decideIntentPrefetch(intent, { expectedN = 8 } = {}) {
  if (!intent?.immediate || !intent?.hover_then_click || !intent?.hover_only) {
    return { decision: 'undecided', reason: 'missing_surfaces_intent' };
  }
  const hoverSamples = intent.hover_only.n ?? 0;
  const chunkN = intent.hover_only.chunk_n ?? 0;
  const chartN = intent.hover_only.chart_n ?? 0;
  const extraPaidN = intent.hover_only.extra_paid_n ?? 0;
  const hoverReadyN = intent.hover_only.ready_n ?? hoverSamples;
  const hoverErrorN = intent.hover_only.error_n ?? 0;
  const hoverTimeoutN = intent.hover_only.timeout_n ?? 0;
  const immediateReady = intent.immediate.ready_n ?? intent.immediate.n ?? 0;
  const hoveredReady = intent.hover_then_click.ready_n ?? intent.hover_then_click.n ?? 0;

  if (!hoverSamples) {
    return { decision: 'undecided', reason: 'no_hover_only_samples' };
  }
  if (hoverSamples !== expectedN || hoverReadyN !== expectedN) {
    return {
      decision: 'undecided',
      reason: 'incomplete_hover_only_samples',
      n: hoverSamples,
      ready_n: hoverReadyN,
      expected_n: expectedN,
    };
  }
  if (hoverErrorN > 0 || hoverTimeoutN > 0) {
    return {
      decision: 'revert',
      reason: 'hover_only_had_failures',
      error_n: hoverErrorN,
      timeout_n: hoverTimeoutN,
    };
  }
  if (chunkN !== expectedN) {
    return { decision: 'revert', reason: 'hover_did_not_prefetch_all_chunks', chunk_n: chunkN, expected_n: expectedN };
  }
  if (chartN > 0) {
    return { decision: 'revert', reason: 'hover_fetched_chart', chart_n: chartN };
  }
  if (extraPaidN > 0) {
    return { decision: 'revert', reason: 'hover_extra_paid_requests', extra_paid_n: extraPaidN };
  }
  if (immediateReady !== expectedN || hoveredReady !== expectedN) {
    return {
      decision: 'undecided',
      reason: 'incomplete_click_samples',
      immediate_ready_n: immediateReady,
      hover_then_click_ready_n: hoveredReady,
      expected_n: expectedN,
    };
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
