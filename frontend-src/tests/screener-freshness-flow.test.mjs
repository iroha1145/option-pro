import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  clearPendingStrengthTask,
  parseScanClock,
  readPendingStrengthTask,
  scanDisplayTimes,
  shouldSubmitStrengthRefresh,
  strengthParametersMatch,
  strengthScanPath,
  visibleScanDate,
  workerActionPhase,
  writePendingStrengthTask,
} from '../src/lib/screenerScanFlow.ts';
import { displayedQuoteLabel, fallbackQuoteLabel, preferLiveQuote } from '../src/lib/liveQuotes.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');

const ownerBase = {
  isOwner: true,
  isMock: false,
  forceRefresh: false,
  snapshotMissing: false,
  snapshotStale: false,
};

test('A01 owner click on a stale 200 snapshot submits a refresh', () => {
  const decision = shouldSubmitStrengthRefresh({ ...ownerBase, snapshotStale: true });
  assert.equal(decision.submit, true);
  assert.equal(decision.reason, 'stale');
});

test('A02 matching fresh snapshot is reused without a worker submit', () => {
  const decision = shouldSubmitStrengthRefresh(ownerBase);
  assert.equal(decision.submit, false);
  assert.equal(decision.reason, 'reuse');
});

test('A03 missing snapshot submits only for the owner', () => {
  assert.equal(shouldSubmitStrengthRefresh({ ...ownerBase, snapshotMissing: true }).submit, true);
  assert.equal(shouldSubmitStrengthRefresh({ ...ownerBase, isOwner: false, snapshotMissing: true }).submit, false);
});

test('A04 visitors never submit a refresh for stale or missing snapshots', () => {
  assert.equal(shouldSubmitStrengthRefresh({
    isOwner: false,
    isMock: false,
    forceRefresh: true,
    snapshotMissing: true,
    snapshotStale: true,
    sourceStatus: 'historical',
  }).submit, false);
});

test('scan display times do not use the click clock as the scan clock', () => {
  const times = scanDisplayTimes({
    queryCheckedAt: 1_789_000_500_000,
    snapshotSavedAt: '2026-08-02T11:01:00+00:00',
    scanCompletedAt: '2026-08-02T11:01:00+00:00',
    scoreDataThrough: '2026-08-01',
    stale: true,
    submittedRefresh: false,
  });
  assert.equal(times.queryCheckedAt, 1_789_000_500_000);
  assert.equal(times.scanCompletedAt, Date.parse('2026-08-02T11:01:00+00:00'));
  assert.notEqual(times.scanCompletedAt, times.queryCheckedAt);
  assert.equal(times.reusedExisting, false);
  assert.equal(visibleScanDate('2026-08-01'), '2026-08-01');
});

test('date-only clocks are not UTC midnight', () => {
  const clock = parseScanClock('2026-07-02');
  assert.ok(clock);
  const date = new Date(clock);
  assert.equal(date.getUTCFullYear(), 2026);
  assert.equal(date.getUTCMonth() + 1, 7);
  assert.equal(date.getUTCDate(), 2);
  assert.equal(date.getUTCHours(), 20);
});

test('unknown fallback time prefers a timestamped quote without inventing a fallback clock', () => {
  const quote = {
    symbol: 'NVDA',
    price: 120,
    previous_close: 100,
    change: 20,
    change_pct: 20,
    trade_at: '2026-09-04T20:00:00.000Z',
    received_at: '2026-09-04T20:00:00.000Z',
    session: 'regular',
    source: 'snapshot',
    freshness: 'snapshot',
    subscription_status: 'limited',
  };
  assert.equal(preferLiveQuote(quote, true, null), false);
  assert.equal(preferLiveQuote(quote, true, '2026-08-01'), true);
  assert.equal(fallbackQuoteLabel(null), '扫描价');
  assert.equal(fallbackQuoteLabel('2026-08-01'), '扫描价 · 日线');
  assert.equal(displayedQuoteLabel(quote, {
    enabled: true, configured: true, public_enabled: true, connected: true, connection_status: 'connected',
  }, false, '2026-08-01'), '扫描价 · 日线');
});

test('worker action mapping keeps reuse and phase, and pending tasks are recovered', () => {
  const memory = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => { memory.set(key, String(value)); },
    removeItem: (key) => { memory.delete(key); },
  };
  assert.equal(workerActionPhase({ status: 'queued' }), 'queued');
  assert.equal(workerActionPhase({ status: 'running' }), 'running');
  assert.equal(workerActionPhase({ status: 'completed' }), 'verifying');
  const parameters = {
    universe: 'themes',
    timeframe: 'all',
    profile: 'balanced',
    top: 20,
    sector_id: 'semiconductors',
    min_price: 5,
    min_avg_dollar_volume: 10_000_000,
    include_options: true,
  };
  writePendingStrengthTask({ requestId: 'req-1', parameters, storedAt: 1 });
  const pending = readPendingStrengthTask();
  assert.equal(pending.requestId, 'req-1');
  assert.equal(strengthParametersMatch(pending.parameters, parameters), true);
  assert.match(strengthScanPath({ ...parameters, sector: 'semiconductors' }), /sector_id=semiconductors/);
  clearPendingStrengthTask();
  assert.equal(readPendingStrengthTask(), null);
});

test('A05 signed-in customer is not treated as owner', () => {
  assert.equal(shouldSubmitStrengthRefresh({
    isOwner: false,
    isMock: false,
    forceRefresh: true,
    snapshotMissing: true,
    snapshotStale: true,
    sourceStatus: 'stale',
  }).submit, false);
});

test('A07 later generation must win: scan path is parameter-specific', () => {
  const first = strengthScanPath({
    universe: 'themes', timeframe: 'all', profile: 'balanced', top: 20,
    sector_id: 'semiconductors', min_price: 5, min_avg_dollar_volume: 10_000_000, include_options: true,
  });
  const second = strengthScanPath({
    universe: 'themes', timeframe: 'all', profile: 'balanced', top: 20,
    sector_id: 'software', min_price: 5, min_avg_dollar_volume: 10_000_000, include_options: true,
  });
  assert.notEqual(first, second);
  assert.match(first, /semiconductors/);
  assert.match(second, /software/);
});

test('A09 pending task is recovered instead of blindly posting again', () => {
  const memory = new Map();
  globalThis.sessionStorage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => { memory.set(key, String(value)); },
    removeItem: (key) => { memory.delete(key); },
  };
  const parameters = {
    universe: 'themes', timeframe: 'all', profile: 'balanced', top: 20,
    sector_id: 'semiconductors', min_price: 5, min_avg_dollar_volume: 10_000_000, include_options: true,
  };
  writePendingStrengthTask({ requestId: 'accepted-1', parameters, storedAt: 10 });
  const pending = readPendingStrengthTask();
  assert.equal(pending.requestId, 'accepted-1');
  assert.equal(strengthParametersMatch(pending.parameters, parameters), true);
  clearPendingStrengthTask();
});

test('D06 score date and quote label stay independent', () => {
  const times = scanDisplayTimes({
    queryCheckedAt: 1_789_100_000_000,
    snapshotSavedAt: '2026-08-02T11:01:00+00:00',
    scanCompletedAt: '2026-08-02T11:01:00+00:00',
    scoreDataThrough: '2026-08-01',
    stale: true,
    submittedRefresh: false,
  });
  assert.equal(visibleScanDate(times.scoreDataThrough), '2026-08-01');
  assert.equal(fallbackQuoteLabel('2026-08-01'), '扫描价 · 日线');
  assert.notEqual(fallbackQuoteLabel('2026-08-01'), '定时更新');
});

test('production screener click path still uses the live worker for stale 200s', async () => {
  const page = await readFile(path.join(src, 'pages', 'Screener.tsx'), 'utf8');
  const cards = await readFile(path.join(src, 'components', 'screener', 'ResultCards.tsx'), 'utf8');
  const table = await readFile(path.join(src, 'components', 'screener', 'ResultTable.tsx'), 'utf8');
  assert.match(page, /shouldSubmitStrengthRefresh/);
  assert.match(page, /snapshotStale: result\.stale/);
  assert.match(page, /setLastScanAt\(times\.scanCompletedAt\)/);
  assert.doesNotMatch(page, /setLastScanAt\(Date\.now\(\)\)/);
  assert.match(page, /resetMarketReadPaths\(\[scanPath\]\)/);
  assert.match(cards, /fallbackAt=\{r\.priceAsOf \?\? r\.dailyDataThrough\}/);
  assert.match(table, /fallbackAt=\{r\.priceAsOf \?\? r\.dailyDataThrough\}/);
});
