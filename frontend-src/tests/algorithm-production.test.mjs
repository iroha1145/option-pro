import test from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_FILTERS } from '../src/components/screener/types.ts';
import { buildStrengthScanRequest } from '../src/components/screener/scanRequest.ts';
import { isStrengthSnapshotPreparing, strengthParametersMatch } from '../src/lib/screenerScanFlow.ts';
import { isA0Ranking, keepServerRankingOrder, rowPrimarySortScore } from '../src/lib/screenerSort.ts';
import { applyEodLimitedView, followsEodScreenerView, isEodLimitedRanking } from '../src/lib/eodLimitedView.ts';
import { t1StatusPresentation } from '../src/lib/t1Status.ts';
import {
  DEFAULT_ALGORITHM_PREFERENCES,
  readAlgorithmPreferences,
  requestedRadarAlgorithm,
  requestedScreenerAlgorithm,
  writeAlgorithmPreferences,
} from '../src/lib/algorithmPreferences.ts';

test('follow_default is sent explicitly on both scan and refresh identities', () => {
  const request = buildStrengthScanRequest(DEFAULT_FILTERS);
  assert.equal(request.apiParams.ranking_algorithm, 'follow_default');
  assert.equal(request.refreshParameters.ranking_algorithm, 'follow_default');
  assert.equal(request.apiParams.list_kind, 'observation');
  assert.equal(request.apiParams.timeframe, 'mid');
});

test('explicit A0 is sent on both scan and refresh identities', () => {
  const request = buildStrengthScanRequest({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'a0_mid_long',
  });
  assert.equal(request.apiParams.ranking_algorithm, 'a0_mid_long');
  assert.equal(request.refreshParameters.ranking_algorithm, 'a0_mid_long');
  assert.equal(
    strengthParametersMatch(request.refreshParameters, request.refreshParameters),
    true,
  );
  assert.equal(
    strengthParametersMatch(request.refreshParameters, {
      ...request.refreshParameters,
      ranking_algorithm: undefined,
    }),
    false,
  );
});

test('A0 display score stays on the sort score and missing stays missing', () => {
  const scored = {
    ticker: 'AAA',
    strengthScore: 91,
    sortScore: 70,
    sortAlgorithm: 'a0_mid_long',
  };
  const missing = {
    ticker: 'BBB',
    strengthScore: 88,
    sortScore: null,
    sortAlgorithm: 'a0_mid_long',
  };
  assert.equal(rowPrimarySortScore(scored, 'a0_mid_long'), 70);
  assert.equal(rowPrimarySortScore(missing, 'a0_mid_long'), null);
  assert.equal(keepServerRankingOrder('a0_mid_long'), true);
  assert.equal(keepServerRankingOrder('production'), false);
  assert.equal(isA0Ranking('a0_mid_long'), true);
});

test('T1 unknown or unmet is not labeled as a weak signal', () => {
  assert.equal(t1StatusPresentation('unmet'), null);
  assert.equal(t1StatusPresentation('unknown'), null);
  assert.ok(t1StatusPresentation('met'));
  assert.ok(t1StatusPresentation('pending'));
  assert.ok(t1StatusPresentation('unavailable'));
  assert.equal((t1StatusPresentation('pending')?.label ?? '').includes('弱信号'), false);
  assert.equal((t1StatusPresentation('unavailable')?.label ?? '').includes('弱信号'), false);
});

test('follow_default is an explicit request identity', () => {
  assert.equal(requestedScreenerAlgorithm('follow_default'), 'follow_default');
  assert.equal(requestedRadarAlgorithm('follow_default'), 'follow_default');
  assert.equal(requestedScreenerAlgorithm('production'), 'production');
  assert.equal(requestedRadarAlgorithm('t1_daily_priority'), 't1_daily_priority');
});

test('explicit EOD limited is sent on both scan and refresh identities', () => {
  const request = buildStrengthScanRequest({
    ...DEFAULT_FILTERS,
    timeframe: 'mid',
    rankingAlgorithm: 'eod_limited_v1',
    resultSet: 'observation',
  });
  assert.equal(request.apiParams.ranking_algorithm, 'eod_limited_v1');
  assert.equal(request.apiParams.list_kind, 'observation');
  const composite = buildStrengthScanRequest({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'follow_default',
    resultSet: 'composite',
  });
  assert.equal(composite.apiParams.list_kind, 'composite');
  assert.equal(request.refreshParameters.ranking_algorithm, 'eod_limited_v1');
  assert.equal(keepServerRankingOrder('eod_limited_v1'), true);
  assert.equal(isEodLimitedRanking('eod_limited_v1'), true);
});

test('first EOD select remaps timeframe all to mid', () => {
  const next = applyEodLimitedView({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'eod_limited_v1',
    timeframe: 'all',
  });
  assert.equal(next.timeframe, 'mid');
  assert.equal(next.rankingAlgorithm, 'eod_limited_v1');
});

test('follow_default also remaps leftover all to mid', () => {
  const next = applyEodLimitedView({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'follow_default',
    timeframe: 'all',
  });
  assert.equal(next.timeframe, 'mid');
  assert.equal(followsEodScreenerView('follow_default'), true);
  assert.equal(followsEodScreenerView('production'), false);
});

test('only preparing 503 is treated as an in-progress A0 snapshot', () => {
  assert.equal(isStrengthSnapshotPreparing({ code: 503, bizCode: 'strength_snapshot_preparing' }), true);
  assert.equal(isStrengthSnapshotPreparing({ code: 503, bizCode: 'strength_snapshot_unavailable' }), false);
  assert.equal(isStrengthSnapshotPreparing({ code: 500, bizCode: 'strength_snapshot_preparing' }), false);
  assert.equal(isStrengthSnapshotPreparing(null), false);
});

test('local algorithm preferences keep an explicit original choice', () => {
  const memory = new Map();
  globalThis.window = {
    localStorage: {
      getItem: (key) => memory.get(key) ?? null,
      setItem: (key, value) => memory.set(key, value),
    },
  };
  assert.deepEqual(readAlgorithmPreferences(), DEFAULT_ALGORITHM_PREFERENCES);
  writeAlgorithmPreferences({ screenerRankingAlgorithm: 'production', radarSortAlgorithm: 'production' }, 'account:alice');
  const stored = readAlgorithmPreferences('account:alice');
  assert.equal(stored.screenerRankingAlgorithm, 'production');
  assert.equal(stored.radarSortAlgorithm, 'production');
  writeAlgorithmPreferences({ screenerRankingAlgorithm: 'follow_default' }, 'account:alice');
  assert.equal(readAlgorithmPreferences('account:alice').screenerRankingAlgorithm, 'follow_default');
  assert.equal(readAlgorithmPreferences('account:bob').screenerRankingAlgorithm, 'follow_default');
  delete globalThis.window;
});
