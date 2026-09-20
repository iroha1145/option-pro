import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

import { DEFAULT_FILTERS } from '../src/components/screener/types.ts';
import { buildStrengthScanRequest } from '../src/components/screener/scanRequest.ts';
import { isStrengthSnapshotPreparing, strengthParametersMatch } from '../src/lib/screenerScanFlow.ts';
import { keepServerRankingOrder, rowPrimarySortScore } from '../src/lib/screenerSort.ts';
import { applyEodLimitedView, isEodLimitedRanking, supportsDollarVolumeFilter } from '../src/lib/eodLimitedView.ts';
import { t1StatusPresentation } from '../src/lib/t1Status.ts';
import {
  DEFAULT_ALGORITHM_PREFERENCES,
  readAlgorithmPreferences,
  requestedRadarAlgorithm,
  requestedScreenerAlgorithm,
  writeAlgorithmPreferences,
} from '../src/lib/algorithmPreferences.ts';

test('the screener and owner console expose no algorithm category or internal score basis', async () => {
  const [page, filters, owner, sideCards] = await Promise.all([
    readFile(new URL('../src/pages/Screener.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/screener/FilterWorkbench.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/catalysts/ManagePanel.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/components/screener/SideCards.tsx', import.meta.url), 'utf8'),
  ]);
  assert.doesNotMatch(filters, /排序算法|原版排序|中长期趋势|收盘技术（受限）/);
  assert.doesNotMatch(page, /scoreBasis|screener-effective-algorithm|切回原版排序/);
  assert.doesNotMatch(owner, /screenerRankingAlgorithm|选股默认算法/);
  assert.match(sideCards, /SCORE_HINTS\.strengthComposite\.body/);
  assert.doesNotMatch(sideCards, /该档位暂无权重明细|偏好档位决定评分时侧重哪些因子/);
});

test('the screener always sends the single current engine identity', () => {
  const request = buildStrengthScanRequest(DEFAULT_FILTERS);
  assert.equal(request.apiParams.ranking_algorithm, 'eod_limited_v1');
  assert.equal(request.refreshParameters.ranking_algorithm, 'eod_limited_v1');
  assert.equal(request.apiParams.list_kind, 'observation');
  assert.equal(request.apiParams.timeframe, 'mid');
});

test('legacy A0 and original choices normalize to the single current engine', () => {
  const filters = applyEodLimitedView({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'a0_mid_long',
    timeframe: 'mid',
    profile: 'aggressive',
  });
  const request = buildStrengthScanRequest(filters);
  assert.equal(request.apiParams.ranking_algorithm, 'eod_limited_v1');
  assert.equal(request.apiParams.timeframe, 'mid');
  assert.equal(request.apiParams.profile, 'aggressive');
  assert.equal(request.refreshParameters.ranking_algorithm, 'eod_limited_v1');
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

test('the visible score always uses the current engine strength score', () => {
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
  assert.equal(rowPrimarySortScore(scored), 91);
  assert.equal(rowPrimarySortScore(missing), 88);
  assert.equal(keepServerRankingOrder(), true);
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

test('legacy screener preferences normalize without changing radar preferences', () => {
  assert.equal(requestedScreenerAlgorithm('follow_default'), 'eod_limited_v1');
  assert.equal(requestedRadarAlgorithm('follow_default'), 'follow_default');
  assert.equal(requestedScreenerAlgorithm('production'), 'eod_limited_v1');
  assert.equal(requestedRadarAlgorithm('t1_daily_priority'), 't1_daily_priority');
});

test('observation and eligible result sets share the same engine identity', () => {
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

test('legacy all timeframe remaps to the supported mid view', () => {
  const next = applyEodLimitedView({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'eod_limited_v1',
    timeframe: 'all',
  });
  assert.equal(next.timeframe, 'mid');
  assert.equal(next.rankingAlgorithm, 'eod_limited_v1');
});

test('follow_default also normalizes to the current engine and mid view', () => {
  const next = applyEodLimitedView({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'follow_default',
    timeframe: 'all',
  });
  assert.equal(next.timeframe, 'mid');
  assert.equal(next.rankingAlgorithm, 'eod_limited_v1');
});

test('restored A0 preferences cannot restore removed view constraints', () => {
  const next = applyEodLimitedView({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'a0_mid_long',
    timeframe: 'mid',
    profile: 'aggressive',
    presetId: 'aggressive',
  });
  assert.equal(next.timeframe, 'mid');
  assert.equal(next.profile, 'aggressive');
  assert.equal(next.presetId, 'aggressive');
  assert.equal(next.rankingAlgorithm, 'eod_limited_v1');
});

test('dollar-volume controls follow server capability metadata', () => {
  assert.equal(supportsDollarVolumeFilter({}), false);
  assert.equal(supportsDollarVolumeFilter({ serverSupport: false }), false);
  assert.equal(supportsDollarVolumeFilter({ serverSupport: true }), true);
});

test('only preparing 503 is treated as an in-progress snapshot', () => {
  assert.equal(isStrengthSnapshotPreparing({ code: 503, bizCode: 'strength_snapshot_preparing' }), true);
  assert.equal(isStrengthSnapshotPreparing({ code: 503, bizCode: 'strength_snapshot_unavailable' }), false);
  assert.equal(isStrengthSnapshotPreparing({ code: 500, bizCode: 'strength_snapshot_preparing' }), false);
  assert.equal(isStrengthSnapshotPreparing(null), false);
});

test('local legacy screener preferences collapse while radar preference remains independent', () => {
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
  assert.equal(stored.screenerRankingAlgorithm, 'eod_limited_v1');
  assert.equal(stored.radarSortAlgorithm, 'production');
  writeAlgorithmPreferences({ screenerRankingAlgorithm: 'follow_default' }, 'account:alice');
  assert.equal(readAlgorithmPreferences('account:alice').screenerRankingAlgorithm, 'eod_limited_v1');
  assert.equal(readAlgorithmPreferences('account:bob').screenerRankingAlgorithm, 'eod_limited_v1');
  delete globalThis.window;
});
