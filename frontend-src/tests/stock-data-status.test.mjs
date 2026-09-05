import test from 'node:test';
import assert from 'node:assert/strict';
import { dailyDataVersion, mapStockDataStatus, normalizeStatusTickers, summarizeStockData } from '../src/lib/stockDataStatus.ts';

const at = '2026-09-04T20:00:00Z';
const ready = { available: true, fresh: true, as_of: at };
const row = (ticker, options = {}) => ({ ticker, status: 'ready', refresh_status: 'ready',
  resources: { overview: ready, daily_chart: ready, signals: ready }, ...options });

test('status tickers deduplicate across case and ordering, preserving share class symbols', () => {
  assert.deepEqual(normalizeStatusTickers(['MSFT', ' aapl ', 'AAPL', '', 'BRK-B', 'BRK.B', 'SPX', '^GSPC']), ['AAPL', 'BRK-B', 'BRK.B', 'MSFT', '^GSPC']);
});

test('missing, unexpected and malformed status rows cannot inflate coverage', () => {
  const items = mapStockDataStatus({ items: [row('AAPL'), row('AAPL'), row('EXTRA'), row('MSFT', { status: 'complete' })] }, ['AAPL', 'MSFT']);
  const coverage = summarizeStockData(['AAPL', 'MSFT'], items);
  assert.equal(coverage.total, 2);
  assert.equal(coverage.overview, 1);
  assert.equal(coverage.ready, 1);
  assert.equal(coverage.unknown, 1);
  assert.throws(() => mapStockDataStatus({}, ['AAPL']), /Invalid stock/);
});

test('available resources remain counted during retry failure; stale and partial are separate', () => {
  const items = mapStockDataStatus({ items: [row('AAPL', { status: 'partial', refresh_status: 'failed', resources: {
    overview: ready, daily_chart: { ...ready, fresh: false }, signals: { available: false, fresh: true },
  } }), row('MSFT', { status: 'running', resources: { overview: ready } })] }, ['AAPL', 'MSFT']);
  assert.deepEqual(summarizeStockData(['AAPL', 'MSFT'], items), {
    total: 2, overview: 2, dailyChart: 1, signals: 0, preparing: 1, partial: 2, failed: 1, stale: 1, unknown: 0, ready: 0,
  });
});

test('resource booleans are strict and chart versions follow only available daily snapshots', () => {
  const items = mapStockDataStatus({ items: [row('AAPL', { resources: {
    overview: { available: 'true', fresh: true }, daily_chart: { ...ready, available: false }, signals: ready,
  } })] }, ['AAPL']);
  assert.equal(items[0].resources.overview.available, false);
  assert.equal(items[0].resources.overview.fresh, false);
  assert.equal(dailyDataVersion(items[0]), '');
  assert.equal(dailyDataVersion(mapStockDataStatus({ items: [row('AAPL')] }, ['AAPL'])[0]), at);
});

test('failed refresh remains visible even when all previously saved resources are fresh', () => {
  const items = mapStockDataStatus({ items: [row('AAPL', { refresh_status: 'failed' }), row('MSFT', { status: 'pending', refresh_status: 'failed' })] }, ['AAPL', 'MSFT']);
  const summary = summarizeStockData(['AAPL', 'MSFT'], items);
  assert.equal(summary.dailyChart, 2);
  assert.equal(summary.failed, 2);
  assert.equal(summary.preparing, 0);
  assert.equal(summary.ready, 0);
});
