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
  refreshActionMatchesRequest,
  shouldCommitScanGeneration,
  shouldDiscoverPublishedScan,
  shouldLockScanTrigger,
  shouldSubmitStrengthRefresh,
  workerWaitDecision,
  workerWaitHasTimedOut,
  strengthParametersMatch,
  strengthScanPath,
  strengthPublicationMatches,
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

test('date-only labels do not fabricate a scan completion clock or shift in Asia', () => {
  assert.equal(parseScanClock('2026-07-02'), null);
  const previous = process.env.TZ;
  try {
    process.env.TZ = 'Asia/Tokyo';
    assert.equal(visibleScanDate('2026-07-02'), '2026-07-02');
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

test('real ISO closing times display the New York score date in Tokyo and UTC', () => {
  const previous = process.env.TZ;
  try {
    for (const timezone of ['Asia/Tokyo', 'UTC']) {
      process.env.TZ = timezone;
      assert.equal(visibleScanDate('2026-09-04T20:00:00Z'), '2026-09-04', timezone);
      assert.equal(visibleScanDate('2026-01-02T21:00:00Z'), '2026-01-02', timezone);
      assert.equal(visibleScanDate('2026-09-05T00:30:00Z'), '2026-09-04', timezone);
    }
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
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
  }, false, '2026-08-01', 'scan'), '扫描价 · 日线');
});

test('equal trade and fallback times keep the quote, not the scan-price label', () => {
  const quote = {
    symbol: 'AAPL',
    price: 1234.56,
    previous_close: 99,
    change: 1135.56,
    change_pct: 1147,
    trade_at: '2026-09-04T15:00:00.000Z',
    received_at: '2026-09-04T15:00:00.000Z',
    session: 'regular',
    source: 'finnhub',
    freshness: 'snapshot',
    subscription_status: 'limited',
  };
  const status = {
    enabled: true, configured: true, public_enabled: true, connected: true, connection_status: 'connected',
  };
  assert.equal(preferLiveQuote(quote, true, '2026-09-04T15:00:00Z'), true);
  assert.equal(displayedQuoteLabel(quote, status, true, '2026-09-04T15:00:00Z'), '定时更新');
  assert.equal(preferLiveQuote(quote, true, '2026-09-04'), false, 'intraday must not beat the complete daily session');
  assert.equal(preferLiveQuote({ ...quote, trade_at: '2026-09-04T20:00:00.000Z' }, true, '2026-09-04'), false);
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
  writePendingStrengthTask({ requestId: 'req-1', parameters, storedAt: Date.now() });
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
  assert.equal(shouldCommitScanGeneration(1, 1), true);
  assert.equal(shouldCommitScanGeneration(1, 2), false, 'a later click must drop the earlier generation');
  assert.equal(shouldLockScanTrigger({ scanning: true, draftMatchesInFlight: true }), true);
  assert.equal(shouldLockScanTrigger({ scanning: true, draftMatchesInFlight: false }), false, 'switching filters must unlock a new scan');
  assert.equal(shouldLockScanTrigger({ scanning: false, draftMatchesInFlight: true }), false);
});

test('E02 worker phases distinguish accepted, running, failed, cancelled, and timeout', () => {
  assert.equal(workerActionPhase({ status: 'accepted' }), 'queued');
  assert.equal(workerActionPhase({ status: 'started' }), 'running');
  assert.equal(workerActionPhase({ status: 'failed' }), 'failed');
  assert.equal(workerActionPhase({ status: 'cancelled' }), 'failed');
  assert.equal(workerActionPhase({ status: 'canceled' }), 'failed');
  assert.equal(workerWaitDecision('completed'), 'done');
  assert.equal(workerWaitDecision('failed'), 'failed');
  assert.equal(workerWaitDecision('cancelled'), 'failed');
  assert.equal(workerWaitDecision('canceled'), 'failed');
  assert.equal(workerWaitDecision('running'), 'poll');
  assert.equal(workerWaitHasTimedOut(10, 10), true);
  assert.equal(workerWaitHasTimedOut(9, 10), false);
});

test('E03 a completed action for other parameters is not this scan', () => {
  const requested = {
    universe: 'themes', timeframe: 'all', profile: 'balanced', top: 20,
    sector_id: 'semiconductors', min_price: 5, min_avg_dollar_volume: 10_000_000, include_options: true,
  };
  assert.equal(refreshActionMatchesRequest({
    details: { parameters: { ...requested, sector_id: 'software' } },
  }, requested), false);
  assert.equal(refreshActionMatchesRequest({ details: { parameters: requested } }, requested), true);
});

test('C06 a failed read does not invent a scan clock from the click time', () => {
  const prior = scanDisplayTimes({
    queryCheckedAt: 1_789_200_000_000,
    snapshotSavedAt: '2026-08-02T11:01:00+00:00',
    scanCompletedAt: '2026-08-02T11:01:00+00:00',
    scoreDataThrough: '2026-08-01',
    stale: true,
    submittedRefresh: false,
  });
  assert.equal(prior.scanCompletedAt, Date.parse('2026-08-02T11:01:00+00:00'));
  assert.notEqual(prior.scanCompletedAt, prior.queryCheckedAt);
  assert.equal(parseScanClock('not-a-clock'), null);
  assert.ok(parseScanClock('2099-01-01T00:00:00Z') == null, 'anomalous future clocks stay unknown');
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
  writePendingStrengthTask({ requestId: 'accepted-1', parameters, storedAt: Date.now() });
  const pending = readPendingStrengthTask();
  assert.equal(pending.requestId, 'accepted-1');
  assert.equal(strengthParametersMatch(pending.parameters, parameters), true);
  clearPendingStrengthTask();
});

test('C05 hidden pages do not discover published scans', () => {
  assert.equal(shouldDiscoverPublishedScan({ scanState: 'done', visibilityState: 'visible', isMock: false }), true);
  assert.equal(shouldDiscoverPublishedScan({ scanState: 'done', visibilityState: 'hidden', isMock: false }), false);
  assert.equal(shouldDiscoverPublishedScan({ scanState: 'scanning', visibilityState: 'visible', isMock: false }), false);
  assert.equal(shouldDiscoverPublishedScan({ scanState: 'done', visibilityState: 'visible', isMock: true }), false);
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
  const quote = await readFile(path.join(src, 'components', 'shared', 'LiveQuote.tsx'), 'utf8');
  assert.match(quote, /if \(!quote && !usingFallback\) return null/);
  assert.match(quote, /fallbackQuoteLabel\(fallbackAt, fallbackKind\)/);
  assert.match(page, /shouldDiscoverPublishedScan/);
  assert.match(page, /shouldCommitScanGeneration/);
  assert.match(page, /shouldLockScanTrigger/);
  assert.match(page, /refreshActionMatchesRequest/);
  assert.match(page, /strength_parameters_busy/);
  assert.doesNotMatch(page, /setLastScanAt\(checkedAt\)/);
  const runtime = await readFile(path.join(src, 'api', 'modules', 'runtime.ts'), 'utf8');
  assert.match(runtime, /workerWaitDecision/);
  assert.match(runtime, /workerWaitHasTimedOut/);
  assert.match(runtime, /worker_action_timeout/);
});


test('disabled session storage never breaks task submission or completion', () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get: () => { throw new Error('Storage disabled'); } });
  try {
    assert.equal(readPendingStrengthTask(), null);
    assert.doesNotThrow(() => writePendingStrengthTask({ requestId: 'task', parameters: {}, storedAt: Date.now() }));
    assert.doesNotThrow(() => clearPendingStrengthTask());
  } finally {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original);
    else delete globalThis.sessionStorage;
  }
});

test('pending task recovery is bounded, identity scoped, and cleared by request id', () => {
  const memory = new Map();
  const original = globalThis.sessionStorage;
  globalThis.sessionStorage = {
    getItem: (key) => memory.get(key) ?? null,
    setItem: (key, value) => memory.set(key, value),
    removeItem: (key) => memory.delete(key),
  };
  try {
    const task = { requestId: 'current', parameters: {}, storedAt: Date.now(), principal: 'owner:' };
    writePendingStrengthTask(task);
    assert.equal(readPendingStrengthTask('visitor:'), null);
    assert.equal(readPendingStrengthTask('owner:').requestId, 'current');
    clearPendingStrengthTask('old');
    assert.equal(readPendingStrengthTask().requestId, 'current');
    clearPendingStrengthTask('current');
    assert.equal(readPendingStrengthTask(), null);
    writePendingStrengthTask({ ...task, storedAt: Date.now() - 86400001 });
    assert.equal(readPendingStrengthTask('owner:'), null);
  } finally {
    globalThis.sessionStorage = original;
  }
});

test('publication verification uses real nested worker results and rejects stale or mismatched versions', () => {
  const snapshot = { snapshotSavedAt: '2026-09-04T21:00:00Z', scoreVersion: 'v2', stale: false, sourceStatus: 'active' };
  const task = { details: { result: { completed_at: snapshot.snapshotSavedAt, score_version: 'v2' } } };
  assert.equal(strengthPublicationMatches(snapshot, task), true);
  assert.equal(strengthPublicationMatches({ ...snapshot, scoreVersion: 'v1' }, task), false);
  assert.equal(strengthPublicationMatches({ ...snapshot, stale: true }, task), false);
  assert.equal(strengthPublicationMatches({ ...snapshot, sourceStatus: 'unknown' }, task), false);
  assert.equal(strengthPublicationMatches({ ...snapshot, snapshotSavedAt: '2026-09-03T21:00:00Z' }, task), false);
  assert.equal(strengthPublicationMatches({ ...snapshot, snapshotSavedAt: '2026-09-05T21:00:00Z', scoreVersion: 'v3' }, task), true);
});
