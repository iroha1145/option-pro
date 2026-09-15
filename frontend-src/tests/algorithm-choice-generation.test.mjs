import test from 'node:test';
import assert from 'node:assert/strict';

import {
  nextChoiceGeneration,
  preferenceStorageKey,
  shouldApplyRemoteAlgorithmPreference,
  shouldCommitChoiceGeneration,
  shouldCommitHistoryPage,
} from '../src/lib/choiceGeneration.ts';
import {
  enqueuePreferenceWrite,
  persistRemoteOrKeepLocal,
  resetPreferenceWriteQueue,
} from '../src/lib/viewPreferenceWrites.ts';

test('later choice generation wins over a stale persist', () => {
  const first = 1;
  const second = nextChoiceGeneration(first);
  assert.equal(shouldCommitChoiceGeneration(first, second), false);
  assert.equal(shouldCommitChoiceGeneration(second, second), true);
});

test('late remote preference reads do not apply after the user changes', () => {
  assert.equal(shouldApplyRemoteAlgorithmPreference({
    startedGeneration: 1,
    currentGeneration: 2,
    cancelled: false,
  }), false);
  assert.equal(shouldApplyRemoteAlgorithmPreference({
    startedGeneration: 2,
    currentGeneration: 2,
    cancelled: true,
  }), false);
  assert.equal(shouldApplyRemoteAlgorithmPreference({
    startedGeneration: 2,
    currentGeneration: 2,
    cancelled: false,
  }), true);
});

test('stale history pages do not commit after a sort change', () => {
  assert.equal(shouldCommitHistoryPage({
    startedGeneration: 1,
    currentGeneration: 2,
    startedCursor: 'prod-page-2',
    currentCursor: 'prod-page-2',
    startedSort: 'production',
    currentSort: 't1_daily_priority',
  }), false);
  assert.equal(shouldCommitHistoryPage({
    startedGeneration: 2,
    currentGeneration: 2,
    startedCursor: 't1-page-1',
    currentCursor: 't1-page-1',
    startedSort: 't1_daily_priority',
    currentSort: 't1_daily_priority',
  }), true);
});

test('preference writes stay serial and last queued write wins', async () => {
  resetPreferenceWriteQueue();
  const order = [];
  let resolveFirst;
  const firstGate = new Promise((resolve) => { resolveFirst = resolve; });
  const first = enqueuePreferenceWrite(async () => {
    await firstGate;
    order.push('first');
    return 'first';
  });
  const second = enqueuePreferenceWrite(async () => {
    order.push('second');
    return 'second';
  });
  resolveFirst();
  assert.equal(await first, 'first');
  assert.equal(await second, 'second');
  assert.deepEqual(order, ['first', 'second']);
});

test('preference storage keys are principal-scoped', () => {
  assert.equal(preferenceStorageKey('account:alice'), 'optix.algorithm-prefs.v1:account:alice');
  assert.equal(preferenceStorageKey(null), 'optix.algorithm-prefs.v1:guest');
  assert.notEqual(preferenceStorageKey('account:alice'), preferenceStorageKey('account:bob'));
});

test('remote preference 503/401/timeout keep the local follow_default choice', async () => {
  resetPreferenceWriteQueue();
  const local = { screenerRankingAlgorithm: 'follow_default', persisted: false };
  for (const code of [503, 401, 'timeout']) {
    const failed = await persistRemoteOrKeepLocal(local, async () => {
      const error = new Error(String(code));
      error.code = code;
      throw error;
    });
    assert.equal(failed.screenerRankingAlgorithm, 'follow_default');
    assert.equal(failed.persisted, false);
    assert.equal(failed.syncError.code, code);
  }
});
