import assert from 'node:assert/strict';
import test from 'node:test';

import { api } from '../static/js/api.js';
import {
  beginWatchlistDefaultReset,
  createWatchlistInitialization,
  retainWatchlistInitialization,
  resolveWatchlistInitialization,
  shouldRenderWatchlistRefresh,
  stageWatchlistTicker,
} from '../static/js/utils/frontendState.js';

test('first watchlist addition is merged after backend defaults finish initializing', () => {
  let initialization = createWatchlistInitialization(null);
  assert.equal(initialization.phase, 'initializing');
  initialization = stageWatchlistTicker(initialization, 'nvda');
  initialization = stageWatchlistTicker(initialization, 'NVDA');
  const resolved = resolveWatchlistInitialization(initialization, ['AAPL', 'MSFT']);
  assert.equal(resolved.phase, 'initialized');
  assert.deepEqual(resolved.tickers, ['AAPL', 'MSFT', 'NVDA']);
});

test('watchlist initialization distinguishes explicit empty, failure, concurrent add, and reset', () => {
  const uninitialized = createWatchlistInitialization();
  assert.equal(uninitialized.phase, 'uninitialized');

  const explicitlyEmpty = createWatchlistInitialization([]);
  assert.equal(explicitlyEmpty.phase, 'explicitly_empty');

  const initialized = createWatchlistInitialization(['AAPL']);
  assert.equal(initialized.phase, 'initialized');

  let initializing = createWatchlistInitialization(null);
  initializing = stageWatchlistTicker(initializing, 'nvda');
  initializing = stageWatchlistTicker(initializing, 'aapl');
  initializing = stageWatchlistTicker(initializing, 'NVDA');
  const retained = retainWatchlistInitialization(initializing);
  assert.equal(retained.phase, 'initializing');
  assert.deepEqual(retained.pending, ['NVDA', 'AAPL']);

  const retried = resolveWatchlistInitialization(retained, ['MSFT', 'AAPL']);
  assert.deepEqual(retried.tickers, ['MSFT', 'AAPL', 'NVDA']);

  const reset = resolveWatchlistInitialization(
    createWatchlistInitialization(null),
    ['SPY', 'QQQ'],
  );
  assert.deepEqual(reset.tickers, ['SPY', 'QQQ']);
});

test('every failed empty-state retry re-renders an enabled retry action', () => {
  const firstFailure = { changed: true, retryable: true };
  const repeatedFailure = { changed: false, retryable: true };
  assert.equal(shouldRenderWatchlistRefresh(firstFailure, true), true);
  assert.equal(shouldRenderWatchlistRefresh(repeatedFailure, true), true);
  assert.equal(shouldRenderWatchlistRefresh({ changed: false }, true), false);
});

test('reset to defaults stays initializing until backend defaults are read', () => {
  const reset = beginWatchlistDefaultReset();
  assert.equal(reset.phase, 'initializing');
  assert.notEqual(reset.phase, 'explicitly_empty');
  assert.deepEqual(reset.pending, []);

  const resolved = resolveWatchlistInitialization(reset, ['SPY', 'QQQ']);
  assert.equal(resolved.phase, 'initialized');
  assert.deepEqual(resolved.tickers, ['SPY', 'QQQ']);
});

test('an explicitly empty custom watchlist never falls back to the full universe', async (t) => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    throw new Error('network should not be called');
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const payload = await api.watchlist({ tickers: [] });
  assert.equal(calls, 0);
  assert.deepEqual(payload, { groups: [], attempted: 0, succeeded: 0, source_status: 'empty' });
});
