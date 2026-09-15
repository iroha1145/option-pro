import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import {
  nextChoiceGeneration,
  preferenceStorageKey,
  shouldApplyRemoteAlgorithmPreference,
  shouldCommitChoiceGeneration,
  shouldCommitHistoryPage,
} from '../src/lib/choiceGeneration.ts';
import {
  PreferenceWriteCancelledError,
  bindPreferenceWritePrincipal,
  enqueuePreferenceWrite,
  invalidatePreferenceWriteQueue,
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
  assert.equal(shouldApplyRemoteAlgorithmPreference({
    startedGeneration: 2,
    currentGeneration: 2,
    cancelled: false,
    pendingLocalSync: true,
  }), false);
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

test('persistAlgorithmChoice and access writes bind the queue to a principal generation', () => {
  const viewPrefs = fs.readFileSync(new URL('../src/api/modules/viewPreferences.ts', import.meta.url), 'utf8');
  assert.match(viewPrefs, /currentPreferenceWriteGeneration\(\)/);
  assert.match(viewPrefs, /principal: principal \?\? undefined/);
  const access = fs.readFileSync(new URL('../src/hooks/useAccess.tsx', import.meta.url), 'utf8');
  assert.match(access, /invalidatePreferenceWriteQueue\(\)/);
  assert.match(access, /bindPreferenceWritePrincipal\(`\$\{next\.role === 'owner' \? 'owner' : 'visitor'\}:\$\{next\.accountUsername \?\? ''\}`\)/);
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

test('queued write does not dispatch Alice patch after Bob signs in', async () => {
  resetPreferenceWriteQueue();
  bindPreferenceWritePrincipal('visitor:alice');
  const writes = [];
  let releaseFirst;
  let enteredFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const entered = new Promise((resolve) => { enteredFirst = resolve; });
  const first = enqueuePreferenceWrite(async () => {
    enteredFirst();
    await firstGate;
    writes.push({ principal: 'alice', patch: { radarSortAlgorithm: 'production' } });
    return 'alice-w1';
  }, { principal: 'visitor:alice' });
  await entered;
  const second = enqueuePreferenceWrite(async () => {
    writes.push({ principal: 'alice', patch: { radarSortAlgorithm: 't1_daily_priority' } });
    return 'alice-w2';
  }, { principal: 'visitor:alice' });
  invalidatePreferenceWriteQueue();
  bindPreferenceWritePrincipal('visitor:bob');
  const bob = enqueuePreferenceWrite(async () => {
    writes.push({ principal: 'bob', patch: { radarSortAlgorithm: 'follow_default' } });
    return 'bob';
  }, { principal: 'visitor:bob' });
  releaseFirst();
  assert.equal(await first, 'alice-w1');
  await assert.rejects(second, (error) => error instanceof PreferenceWriteCancelledError);
  assert.equal(await bob, 'bob');
  assert.deepEqual(
    writes.map((item) => item.principal).sort(),
    ['alice', 'bob'],
  );
  assert.equal(writes.some((item) => item.patch.radarSortAlgorithm === 't1_daily_priority'), false);
});

test('persistRemoteOrKeepLocal does not dispatch Alice patch after Bob signs in', async () => {
  resetPreferenceWriteQueue();
  bindPreferenceWritePrincipal('visitor:alice');
  const writes = [];
  let releaseFirst;
  let enteredFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const entered = new Promise((resolve) => { enteredFirst = resolve; });
  const first = persistRemoteOrKeepLocal(
    { radarSortAlgorithm: 'production' },
    async () => {
      writes.push({ radarSortAlgorithm: 'production' });
      enteredFirst();
      await firstGate;
      return { radarSortAlgorithm: 'production', persisted: true };
    },
    { principal: 'visitor:alice' },
  );
  await entered;
  const second = persistRemoteOrKeepLocal(
    { radarSortAlgorithm: 't1_daily_priority' },
    async () => {
      writes.push({ radarSortAlgorithm: 't1_daily_priority' });
      return { radarSortAlgorithm: 't1_daily_priority', persisted: true };
    },
    { principal: 'visitor:alice' },
  );
  invalidatePreferenceWriteQueue();
  bindPreferenceWritePrincipal('visitor:bob');
  const bob = persistRemoteOrKeepLocal(
    { radarSortAlgorithm: 'follow_default' },
    async () => {
      writes.push({ radarSortAlgorithm: 'follow_default' });
      return { radarSortAlgorithm: 'follow_default', persisted: true };
    },
    { principal: 'visitor:bob' },
  );
  releaseFirst();
  const firstResult = await first;
  const secondResult = await second;
  const bobResult = await bob;
  assert.equal(firstResult.radarSortAlgorithm, 'production');
  assert.equal(firstResult.persisted, true);
  assert.equal(secondResult.radarSortAlgorithm, 't1_daily_priority');
  assert.equal(secondResult.persisted, false);
  assert.equal(secondResult.syncError instanceof PreferenceWriteCancelledError, true);
  assert.equal(bobResult.radarSortAlgorithm, 'follow_default');
  assert.equal(bobResult.persisted, true);
  assert.deepEqual(writes.map((item) => item.radarSortAlgorithm), ['production', 'follow_default']);
});

test('replacing the write tail does not run a previously chained callback', async () => {
  resetPreferenceWriteQueue();
  bindPreferenceWritePrincipal('visitor:alice');
  const writes = [];
  let releaseFirst;
  let enteredFirst;
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  const entered = new Promise((resolve) => { enteredFirst = resolve; });
  const first = enqueuePreferenceWrite(async () => {
    enteredFirst();
    await firstGate;
    writes.push('alice-w1');
  }, { principal: 'visitor:alice' });
  await entered;
  const queued = enqueuePreferenceWrite(async () => {
    writes.push('alice-w2');
  }, { principal: 'visitor:alice' });
  resetPreferenceWriteQueue();
  bindPreferenceWritePrincipal('visitor:bob');
  releaseFirst();
  await first;
  await assert.rejects(queued, (error) => error instanceof PreferenceWriteCancelledError);
  assert.deepEqual(writes, ['alice-w1']);
});
