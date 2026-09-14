import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ReadAttemptAborted,
  NEWS_DETAIL_RETRY_WAITS_MS,
  boundedReadRetryDelayMs,
  createCancellableSleep,
  isAutoRetryableReadError,
  runBoundedRead,
  shouldApplyRecoveryJob,
} from '../src/lib/boundedReadRetry.ts';

test('401/403/404 与 retryable=false 不可自动重试', () => {
  for (const error of [
    { code: 401, message: 'no' },
    { code: 403, message: 'no' },
    { code: 404, message: 'no' },
    { code: 502, retryable: false, message: 'no' },
  ]) {
    assert.equal(isAutoRetryableReadError(error), false, JSON.stringify(error));
  }
  assert.equal(isAutoRetryableReadError({ code: 429, retryAfter: 30, retryable: true }), true);
  assert.equal(isAutoRetryableReadError({ code: 503, retryable: true }), true);
  assert.equal(isAutoRetryableReadError(new TypeError('network')), true);
});

test('429 Retry-After 30 秒取 max(本地退避, 服务端剩余)', () => {
  assert.equal(boundedReadRetryDelayMs(0, { retryAfter: 30 }, NEWS_DETAIL_RETRY_WAITS_MS), 30_000);
  assert.equal(boundedReadRetryDelayMs(1, { retryAfter: 30 }, NEWS_DETAIL_RETRY_WAITS_MS), 30_000);
  assert.equal(boundedReadRetryDelayMs(0, { retryAfter: 1 }, NEWS_DETAIL_RETRY_WAITS_MS), 1_500);
  assert.equal(boundedReadRetryDelayMs(0, {}, NEWS_DETAIL_RETRY_WAITS_MS), 1_500);
  assert.equal(boundedReadRetryDelayMs(1, {}, NEWS_DETAIL_RETRY_WAITS_MS), 3_000);
});

test('迟到的任务 A 不得替换已经在跟的任务 B', () => {
  assert.equal(shouldApplyRecoveryJob({ jobId: 'B' }, { jobId: 'A' }, 'A', 'B'), false);
  assert.equal(shouldApplyRecoveryJob({ jobId: 'A' }, { jobId: 'B' }, 'B', 'B'), true);
  assert.equal(shouldApplyRecoveryJob(null, { jobId: 'B' }, 'B', 'B'), true);
  assert.equal(shouldApplyRecoveryJob({ jobId: 'B' }, { jobId: 'A' }, 'B', 'B'), false);
});

test('runBoundedRead：404 只读一次，429 按 Retry-After 睡，关闭后不再读', async () => {
  const calls = [];
  await assert.rejects(
    runBoundedRead({
      read: async () => {
        calls.push('404');
        throw { code: 404, message: 'gone' };
      },
      isAlive: () => true,
      sleep: async () => { throw new Error('404 must not sleep'); },
    }),
    (error) => error.code === 404,
  );
  assert.deepEqual(calls, ['404']);

  const slept = [];
  let round = 0;
  const value = await runBoundedRead({
    read: async () => {
      round += 1;
      if (round === 1) throw { code: 429, retryAfter: 30, retryable: true };
      return 'ok';
    },
    isAlive: () => true,
    sleep: async (ms) => { slept.push(ms); },
  });
  assert.equal(value, 'ok');
  assert.deepEqual(slept, [30_000]);

  const later = [];
  let alive = true;
  const timers = new Map();
  let next = 0;
  const sleeper = createCancellableSleep({
    isAlive: () => alive,
    setTimeoutFn: (fn, ms) => {
      const id = ++next;
      timers.set(id, { fn, ms });
      return id;
    },
    clearTimeoutFn: (id) => { timers.delete(id); },
  });
  const pending = runBoundedRead({
    read: async () => {
      later.push('read');
      throw { code: 503, retryable: true };
    },
    isAlive: () => alive,
    sleep: sleeper.sleep,
  });
  await Promise.resolve();
  assert.deepEqual(later, ['read']);
  alive = false;
  sleeper.cancel();
  await assert.rejects(pending, (error) => error instanceof ReadAttemptAborted);
  assert.equal(timers.size, 0);
  assert.deepEqual(later, ['read']);
});
