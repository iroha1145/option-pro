import test from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_FILTERS } from '../src/components/screener/types.ts';
import { buildStrengthScanRequest } from '../src/components/screener/scanRequest.ts';
import { strengthParametersMatch } from '../src/lib/screenerScanFlow.ts';
import { isA0Ranking, keepServerRankingOrder, rowPrimarySortScore } from '../src/lib/screenerSort.ts';
import { t1StatusPresentation } from '../src/lib/t1Status.ts';
import {
  DEFAULT_ALGORITHM_PREFERENCES,
  readAlgorithmPreferences,
  requestedRadarAlgorithm,
  requestedScreenerAlgorithm,
  writeAlgorithmPreferences,
} from '../src/lib/algorithmPreferences.ts';
import { resetPreferenceWriteQueue } from '../src/lib/viewPreferenceWrites.ts';

test('follow_default is sent explicitly on both scan and refresh identities', () => {
  const request = buildStrengthScanRequest(DEFAULT_FILTERS);
  assert.equal(request.apiParams.ranking_algorithm, 'follow_default');
  assert.equal(request.refreshParameters.ranking_algorithm, 'follow_default');
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

test('preference persist failure keeps the explicit follow_default request identity', async () => {
  const memory = new Map();
  globalThis.window = {
    localStorage: {
      getItem: (key) => memory.get(key) ?? null,
      setItem: (key, value) => memory.set(key, value),
    },
  };
  const { persistAlgorithmChoice, viewPreferencesApi } = await import('../src/api/modules/viewPreferences.ts');
  const originalWrite = viewPreferencesApi.write;
  resetPreferenceWriteQueue();
  const request = buildStrengthScanRequest({
    ...DEFAULT_FILTERS,
    rankingAlgorithm: 'follow_default',
  });
  assert.equal(request.apiParams.ranking_algorithm, 'follow_default');
  assert.equal(request.refreshParameters.ranking_algorithm, 'follow_default');
  try {
    for (const code of [503, 401, 'timeout']) {
      viewPreferencesApi.write = async () => {
        const error = new Error(String(code));
        error.code = code;
        throw error;
      };
      const failed = await persistAlgorithmChoice(
        { screenerRankingAlgorithm: 'follow_default' },
        true,
        'account:alice',
      );
      assert.equal(failed.screenerRankingAlgorithm, 'follow_default');
      assert.equal(failed.persisted, false);
      assert.equal(failed.syncError.code, code);
    }
  } finally {
    viewPreferencesApi.write = originalWrite;
    resetPreferenceWriteQueue();
    delete globalThis.window;
  }
});
