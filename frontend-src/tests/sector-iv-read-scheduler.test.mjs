import test from 'node:test';
import assert from 'node:assert/strict';

import { SectorIvRefreshFlow } from '../src/components/sectors/ivRefreshFlow.ts';
import { SectorIvReadScheduler } from '../src/components/sectors/sectorIvReadScheduler.ts';

function refresh(status, retryAfterSeconds = 0) {
  return {
    status,
    retryAfterSeconds,
    requestedAt: null,
    startedAt: null,
    completedAt: null,
    nextRefreshAt: null,
    errorCode: status === 'failed' ? 'provider_unavailable' : null,
  };
}

function envelope(sectorId, ticker, status, retryAfterSeconds = 0, asOf = '2026-09-20T04:00:00Z') {
  return {
    sectorId,
    sectorName: sectorId,
    rows: [{
      ticker,
      name: ticker,
      price: 100,
      sectorIvRank: 50,
      atmIvPercent: 30,
      stale: status === 'failed',
      asOf,
    }],
    sourceStatus: status === 'failed' ? 'stale' : 'active',
    stale: status === 'failed',
    asOf,
    dataLimited: false,
    successCount: 1,
    requestedCount: 1,
    successRate: 100,
    failedSymbols: [],
    snapshotSource: 'sector_snapshot',
    snapshotOrigin: 'public_live',
    providers: [],
    refresh: refresh(status, retryAfterSeconds),
  };
}

class FakeClock {
  nowMs = 0;
  nextId = 1;
  tasks = new Map();

  now = () => this.nowMs;

  setTimeout = (callback, delayMs) => this.add(callback, delayMs, 0);
  setInterval = (callback, intervalMs) => this.add(callback, intervalMs, intervalMs);
  clearTimeout = (id) => this.tasks.delete(id);
  clearInterval = (id) => this.tasks.delete(id);

  add(callback, delayMs, intervalMs) {
    const id = this.nextId++;
    this.tasks.set(id, {
      callback,
      at: this.nowMs + Math.max(0, delayMs),
      intervalMs,
    });
    return id;
  }

  advance(ms) {
    const target = this.nowMs + ms;
    while (true) {
      const next = [...this.tasks.entries()]
        .filter(([, task]) => task.at <= target)
        .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
      if (!next) break;
      const [id, task] = next;
      this.nowMs = task.at;
      if (task.intervalMs > 0) task.at += task.intervalMs;
      else this.tasks.delete(id);
      task.callback();
    }
    this.nowMs = target;
  }
}

async function settle() {
  for (let index = 0; index < 8; index += 1) await Promise.resolve();
}

function wire(flow, clock, visible) {
  const scheduler = new SectorIvReadScheduler(
    () => flow.read(),
    () => visible.value,
    clock,
  );
  const unsubscribe = flow.subscribe((snapshot) => {
    scheduler.update(snapshot, snapshot.sectorId);
  });
  return { scheduler, unsubscribe };
}

test('failed deadline performs one GET, then follows queued and running to idle new data', async () => {
  const clock = new FakeClock();
  const visible = { value: true };
  const responses = [
    envelope('air-transport', 'OLD', 'failed', 5, '2026-09-20T00:00:00Z'),
    envelope('air-transport', 'OLD', 'queued'),
    envelope('air-transport', 'OLD', 'running'),
    envelope('air-transport', 'NEW', 'idle', 0, '2026-09-20T04:05:00Z'),
  ];
  let reads = 0;
  const flow = new SectorIvRefreshFlow({
    read: async () => responses[reads++],
    refresh: async () => { throw new Error('scheduler must never POST'); },
  });
  const { scheduler, unsubscribe } = wire(flow, clock, visible);

  await flow.selectSector('air-transport');
  assert.equal(reads, 1);
  assert.equal(flow.getSnapshot().refresh.status, 'failed');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'OLD');

  clock.advance(4_999);
  await settle();
  assert.equal(reads, 1, 'GET must wait for the server deadline');
  clock.advance(1);
  await settle();
  assert.equal(reads, 2);
  assert.equal(flow.getSnapshot().refresh.status, 'queued');

  clock.advance(2_999);
  await settle();
  assert.equal(reads, 2);
  clock.advance(1);
  await settle();
  assert.equal(reads, 3);
  assert.equal(flow.getSnapshot().refresh.status, 'running');

  clock.advance(3_000);
  await settle();
  assert.equal(reads, 4);
  assert.equal(flow.getSnapshot().refresh.status, 'idle');
  assert.equal(flow.getSnapshot().data.rows[0].ticker, 'NEW');
  assert.equal(flow.getSnapshot().data.asOf, '2026-09-20T04:05:00Z');

  unsubscribe();
  scheduler.dispose();
});

test('deadline reached while hidden waits until visibility returns', async () => {
  const clock = new FakeClock();
  const visible = { value: true };
  const responses = [
    envelope('air-transport', 'OLD', 'cooldown', 5),
    envelope('air-transport', 'OLD', 'queued'),
  ];
  let reads = 0;
  const flow = new SectorIvRefreshFlow({
    read: async () => responses[reads++],
    refresh: async () => { throw new Error('scheduler must never POST'); },
  });
  const { scheduler, unsubscribe } = wire(flow, clock, visible);

  await flow.selectSector('air-transport');
  visible.value = false;
  clock.advance(5_000);
  await settle();
  assert.equal(reads, 1);

  visible.value = true;
  scheduler.onVisible();
  await settle();
  assert.equal(reads, 2);
  assert.equal(flow.getSnapshot().refresh.status, 'queued');

  unsubscribe();
  scheduler.dispose();
});

test('GET 429 Retry-After suspends the three-second running poll', async () => {
  const clock = new FakeClock();
  const visible = { value: true };
  let reads = 0;
  const flow = new SectorIvRefreshFlow({
    read: async () => {
      reads += 1;
      if (reads === 1) return envelope('air-transport', 'OLD', 'queued');
      if (reads === 2) {
        throw Object.assign(new Error('rate limited'), {
          code: 429,
          retryAfter: 8,
        });
      }
      return envelope('air-transport', 'OLD', 'running');
    },
    refresh: async () => { throw new Error('scheduler must never POST'); },
  });
  const { scheduler, unsubscribe } = wire(flow, clock, visible);

  await flow.selectSector('air-transport');
  clock.advance(3_000);
  await settle();
  assert.equal(reads, 2);
  assert.equal(flow.getSnapshot().readError.code, 429);

  clock.advance(7_999);
  await settle();
  assert.equal(reads, 2, '429 must suppress repeated three-second GETs');
  clock.advance(1);
  await settle();
  assert.equal(reads, 3);
  assert.equal(flow.getSnapshot().refresh.status, 'running');

  unsubscribe();
  scheduler.dispose();
});
