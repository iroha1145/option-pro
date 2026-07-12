import assert from 'node:assert/strict';
import test from 'node:test';

import {
  STRENGTH_FILTER_KEYS,
  beginStrengthFilterRequest,
  settleStrengthFilterRequest,
  snapshotsEqual,
  strengthFilterSnapshot,
  updateStrengthDraftFilters,
} from '../static/js/utils/frontendState.js';

const INITIAL_FILTERS = strengthFilterSnapshot({
  timeframe: 'all', profile: 'balanced', top: 20, sectorId: '',
  minPrice: 5, minAvgDollarVolume: 10_000_000,
});

test('strength filters keep separate immutable draft, applied, and request snapshots', () => {
  const draft = updateStrengthDraftFilters(INITIAL_FILTERS, {
    sectorId: 'technology', minAvgDollarVolume: 12_500_000,
  });
  assert.equal(snapshotsEqual(draft, INITIAL_FILTERS, STRENGTH_FILTER_KEYS), false);

  const request = beginStrengthFilterRequest({
    draftFilters: draft,
    appliedFilters: INITIAL_FILTERS,
  }, { filters: draft, applyDraft: true });
  assert.deepEqual(request.requestParams, {
    timeframe: 'all',
    profile: 'balanced',
    top: 20,
    sector_id: 'technology',
    min_price: 5,
    min_avg_dollar_volume: 12_500_000,
  });
  assert.throws(() => { request.requestParams.top = 50; }, TypeError);
});

test('editing numeric filters during a request survives the old response and drives the next request', () => {
  const state = {
    draftFilters: INITIAL_FILTERS,
    appliedFilters: INITIAL_FILTERS,
    requestFilters: null,
    resultFilters: null,
  };
  const first = beginStrengthFilterRequest(state, {
    filters: state.draftFilters,
    applyDraft: true,
  });
  state.appliedFilters = first.appliedFilters;
  state.requestFilters = first.requestFilters;

  // The user types while the first request is still in flight.
  state.draftFilters = updateStrengthDraftFilters(state.draftFilters, {
    minPrice: 12.5,
    minAvgDollarVolume: 27_500_000,
  });

  // The first response belongs to its immutable request snapshot. Settling it
  // must not replace the newer draft values.
  const firstSettled = settleStrengthFilterRequest(state, first.requestFilters);
  state.draftFilters = firstSettled.draftFilters;
  state.resultFilters = firstSettled.resultFilters;
  assert.equal(state.resultFilters.minPrice, 5);
  assert.equal(state.resultFilters.minAvgDollarVolume, 10_000_000);
  assert.equal(state.draftFilters.minPrice, 12.5);
  assert.equal(state.draftFilters.minAvgDollarVolume, 27_500_000);

  const second = beginStrengthFilterRequest(state, {
    filters: state.draftFilters,
    applyDraft: true,
  });
  assert.equal(second.requestParams.min_price, 12.5);
  assert.equal(second.requestParams.min_avg_dollar_volume, 27_500_000);
});
