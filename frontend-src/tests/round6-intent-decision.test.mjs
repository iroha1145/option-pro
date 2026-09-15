import assert from 'node:assert/strict';
import test from 'node:test';
import { decideIntentPrefetch } from '../../scripts/perf/lib/round6_intent_decision.mjs';

test('intent prefetch stays only when desktop hover is clearly faster', () => {
  assert.equal(decideIntentPrefetch(null).decision, 'undecided');
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 600, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 0, chart_n: 0, extra_paid_n: 0 },
  }).decision, 'revert');
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 600, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 8, chart_n: 2, extra_paid_n: 0 },
  }).decision, 'revert');
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 860, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 8, chart_n: 0, extra_paid_n: 0 },
  }).reason, 'no_clear_hover_benefit');
  const incompleteChunks = decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 600, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 7, chart_n: 0, extra_paid_n: 0 },
  });
  assert.equal(incompleteChunks.decision, 'revert');
  assert.equal(incompleteChunks.reason, 'hover_did_not_prefetch_all_chunks');
  const keep = decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 600, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 8, chart_n: 0, extra_paid_n: 0 },
  });
  assert.equal(keep.decision, 'keep');
  assert.equal(keep.faster_ms, 300);
});
