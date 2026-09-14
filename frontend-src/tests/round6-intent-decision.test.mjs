import assert from 'node:assert/strict';
import test from 'node:test';
import { decideIntentPrefetch } from '../../scripts/perf/lib/round6_intent_decision.mjs';

test('intent prefetch stays only when desktop hover is clearly faster', () => {
  assert.equal(decideIntentPrefetch(null).decision, 'undecided');
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900 },
    hover_then_click: { p75: 600 },
    hover_only: { n: 8, chunk_n: 0, chart_n: 0 },
  }).decision, 'revert');
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900 },
    hover_then_click: { p75: 600 },
    hover_only: { n: 8, chunk_n: 8, chart_n: 2 },
  }).decision, 'revert');
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900 },
    hover_then_click: { p75: 860 },
    hover_only: { n: 8, chunk_n: 8, chart_n: 0 },
  }).reason, 'no_clear_hover_benefit');
  const keep = decideIntentPrefetch({
    immediate: { p75: 900 },
    hover_then_click: { p75: 600 },
    hover_only: { n: 8, chunk_n: 7, chart_n: 0 },
  });
  assert.equal(keep.decision, 'keep');
  assert.equal(keep.faster_ms, 300);
});
