import test from 'node:test';
import assert from 'node:assert/strict';

import { SectorIvRefreshFlow } from '../src/components/sectors/ivRefreshFlow.ts';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function refresh(status, extra = {}) {
  return {
    status,
    retryAfterSeconds: 0,
    requestedAt: null,
    startedAt: null,
    completedAt: null,
    nextRefreshAt: null,
    errorCode: null,
    ...extra,
  };
}

function envelope(sectorId, ticker, status = 'idle', extra = {}) {
  const asOf = extra.asOf ?? '2026-09-20T12:00:00Z';
  return {
    sectorId,
    sectorName: sectorId,
    rows: ticker ? [{
      ticker,
      name: ticker,
      price: 100,
      sectorIvRank: 50,
      atmIvPercent: 30,
      stale: false,
      asOf,
    }] : [],
    sourceStatus: ticker ? 'active' : 'insufficient_data',
    stale: false,
    asOf: ticker ? asOf : null,
    dataLimited: false,
    successCount: ticker ? 1 : 0,
    requestedCount: 1,
    successRate: ticker ? 100 : 0,
    failedSymbols: [],
    snapshotSource: null,
    snapshotOrigin: null,
    providers: [],
    refresh: refresh(status, extra.refresh),
  };
}

test('public click follows queued to running to idle and publishes the new rows', async () => {
  const post = deferred();
  const reads = [
    envelope('technology', 'OLD'),
    envelope('technology', 'OLD', 'running', {
      refresh: { startedAt: '2026-09-20T12:01:00Z' },
    }),
    envelope('technology', 'NEW', 'idle', {
      asOf: '2026-09-20T12:02:00Z',
      refresh: { completedAt: '2026-09-20T12:02:00Z' },
    }),
  ];
  let readCount = 0;
  let postCount = 0;
  const flow = new SectorIvRefreshFlow({
    read: async () => reads[readCount++],
    refresh: async (sectorId) => {
      postCount += 1;
      await post.promise;
      return { sectorId, refresh: refresh('queued', { requestedAt: '2026-09-20T12:00:30Z' }) };
    },
  });
  const observed = [];
  flow.subscribe((state) => observed.push(state.refresh.status));

  await flow.selectSector('technology');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'OLD');

  const first = flow.submit();
  const duplicate = flow.submit();
  assert.equal(first, duplicate, 'rapid public clicks must share one POST');
  assert.equal(postCount, 1);
  assert.equal(flow.getSnapshot().submitting, true);

  post.resolve();
  await first;
  assert.ok(observed.includes('queued'), 'POST acceptance must be visible before completion');
  assert.equal(flow.getSnapshot().refresh.status, 'running');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'OLD');

  await flow.read();
  assert.equal(flow.getSnapshot().refresh.status, 'idle');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'NEW');
  assert.equal(flow.getSnapshot().data.asOf, '2026-09-20T12:02:00Z');
});

test('failed refresh and read failure retain the last successful rows and source time', async () => {
  const old = envelope('energy', 'XOM', 'idle', { asOf: '2026-09-20T11:00:00Z' });
  const failed = envelope('energy', 'XOM', 'failed', {
    asOf: '2026-09-20T11:00:00Z',
    refresh: { errorCode: 'provider_unavailable' },
  });
  let phase = 0;
  const flow = new SectorIvRefreshFlow({
    read: async () => {
      phase += 1;
      if (phase === 1) return old;
      if (phase === 2) return failed;
      throw Object.assign(new Error('temporary read failure'), { code: 503 });
    },
    refresh: async (sectorId) => ({ sectorId, refresh: refresh('queued') }),
  });

  await flow.selectSector('energy');
  await flow.read();
  assert.equal(flow.getSnapshot().refresh.status, 'failed');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'XOM');
  assert.equal(flow.getSnapshot().data.asOf, '2026-09-20T11:00:00Z');

  await flow.read();
  assert.equal(flow.getSnapshot().readError.code, 503);
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'XOM');
  assert.equal(flow.getSnapshot().data.asOf, '2026-09-20T11:00:00Z');
});

test('late reads and task acceptance from an old sector cannot overwrite the new sector', async () => {
  const oldRead = deferred();
  const oldPost = deferred();
  const flow = new SectorIvRefreshFlow({
    read: (sectorId) => sectorId === 'technology'
      ? oldRead.promise
      : Promise.resolve(envelope('energy', 'XOM')),
    refresh: async (sectorId) => {
      await oldPost.promise;
      return { sectorId, refresh: refresh('queued') };
    },
  });

  const technologyRead = flow.selectSector('technology');
  const technologyPost = flow.submit();
  await flow.selectSector('energy');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'XOM');

  oldRead.resolve(envelope('technology', 'NVDA'));
  oldPost.resolve();
  await Promise.all([technologyRead, technologyPost]);

  assert.equal(flow.getSnapshot().sectorId, 'energy');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'XOM');
  assert.equal(flow.getSnapshot().refresh.status, 'idle');
});

test('429 response becomes a visible cooldown without dropping saved data', async () => {
  const flow = new SectorIvRefreshFlow({
    read: async () => envelope('financials', 'JPM'),
    refresh: async () => {
      throw Object.assign(new Error('rate limited'), {
        code: 429,
        retryAfter: 37,
        bizCode: 'sector_iv_rate_limited',
      });
    },
  });

  await flow.selectSector('financials');
  await flow.submit();
  assert.equal(flow.getSnapshot().refresh.status, 'cooldown');
  assert.equal(flow.getSnapshot().refresh.retryAfterSeconds, 37);
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'JPM');
  assert.equal(flow.getSnapshot().actionError, null);
});

test('200 cooldown response keeps its countdown and does not immediately overwrite it with GET', async () => {
  let reads = 0;
  const flow = new SectorIvRefreshFlow({
    read: async () => {
      reads += 1;
      return envelope('healthcare', 'LLY');
    },
    refresh: async (sectorId) => ({
      sectorId,
      refresh: refresh('cooldown', { retryAfterSeconds: 52 }),
    }),
  });

  await flow.selectSector('healthcare');
  await flow.submit();
  assert.equal(reads, 1, 'cooldown acceptance must not trigger a GET that can erase it');
  assert.equal(flow.getSnapshot().refresh.status, 'cooldown');
  assert.equal(flow.getSnapshot().refresh.retryAfterSeconds, 52);
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'LLY');
});
