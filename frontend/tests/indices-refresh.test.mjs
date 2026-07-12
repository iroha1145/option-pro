import assert from 'node:assert/strict';
import test from 'node:test';

import { createIndicesVisibilityHandler } from '../static/js/components/indices.js';
import { refreshRemainingMs } from '../static/js/utils/frontendState.js';

test('index refresh throttle opens only after the full sixty-second interval', () => {
  assert.equal(refreshRemainingMs(1_000, 60_999, 60_000), 1);
  assert.equal(refreshRemainingMs(1_000, 61_000, 60_000), 0);
});

test('visibility changes call the same throttle before requesting an index refresh', () => {
  let hidden = false;
  let clock = 61_000;
  let lastStartedAt = 1_000;
  const calls = [];
  const handler = createIndicesVisibilityHandler({
    isHidden: () => hidden,
    now: () => clock,
    lastStartedAt: () => lastStartedAt,
    refresh: (force) => {
      calls.push(force);
      lastStartedAt = clock;
    },
  });

  assert.equal(handler(), true);
  assert.deepEqual(calls, [true]);
  clock += 100;
  assert.equal(handler(), false);
  hidden = true;
  clock += 60_000;
  assert.equal(handler(), false);
  assert.deepEqual(calls, [true]);
});
