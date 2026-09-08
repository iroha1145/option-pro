import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import * as flow from '../src/lib/screenerScanFlow.ts';
import { ApiError, toQuery } from '../src/api/client.ts';
import { marketGet, resetMarketReadState } from '../src/api/marketRead.ts';
import * as live from '../src/api/live.ts';

const source = fs.readFileSync(new URL('../src/pages/Screener.tsx', import.meta.url), 'utf8');
const parameters = { universe: 'themes', timeframe: 'all', profile: 'balanced', top: 20, sector_id: null, min_price: 5, min_avg_dollar_volume: 10000000, include_options: true };
const completedAt = '2026-09-04T21:00:00Z';
const completed = { requestId: 'new', status: 'completed', details: { parameters, result: { completed_at: completedAt, score_version: 'v2', published: true } } };
const envelope = (ticker = 'NEW') => ({ rows: [{ ticker, price: 180 }], stale: false, sourceStatus: 'active', snapshotSavedAt: completedAt, scanCompletedAt: completedAt, scoreVersion: 'v2' });
const deferred = () => { let resolve; let reject; const promise = new Promise((r, fail) => { resolve = r; reject = fail; }); return { promise, resolve, reject }; };

function harness(overrides = {}) {
  const state = { rows: null, history: [], scanPhase: null };
  let pending = null;
  const scope = {
    ...flow, ApiError, Date, Promise, Object, Map, Boolean,
    setTimeout: (cb) => { queueMicrotask(cb); return 1; },
    useCallback: (fn) => fn,
    isOwner: true, isMock: false,
    principal: 'owner',
    scanSeq: { current: 0 },
    scanIdentity: { current: 'owner' },
    mounted: { current: true },
    getMarketReadGeneration: () => 0,
    __t: (text) => text,
    summarizeFilters: () => '',
    buildStrengthScanRequest: (filters) => ({ apiParams: { ...parameters, ...filters }, refreshParameters: { ...parameters, ...filters } }),
    detailsRef: { current: {} },
    resetMarketReadPaths: () => {},
    readPendingStrengthTask: () => pending,
    writePendingStrengthTask: (value) => { pending = value; },
    clearPendingStrengthTask: (id) => { if (!id || pending?.requestId === id) pending = null; },
    runtimeApi: { workerAction: async () => completed, workerActionStatus: async () => completed, waitForWorkerAction: async () => completed },
    strengthApi: { scanEnvelope: async () => envelope() },
    ...overrides,
  };
  for (const match of source.matchAll(/\bset([A-Z]\w*)\(/g)) {
    const name = match[1];
    const key = name[0].toLowerCase() + name.slice(1);
    scope[`set${name}`] = (value) => { state[key] = typeof value === 'function' ? value(state[key]) : value; };
  }
  const start = source.indexOf('  const runScan = useCallback(');
  const end = source.indexOf('\n  useEffect(', start);
  const code = ts.transpileModule(`${source.slice(start, end)}\nglobalThis.runScan = runScan;`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, scope);
  return { state, scope, runScan: scope.runScan, seedPending: (value) => { pending = value; }, pending: () => pending };
}

test('failed recovered task is discarded so a new click can create a replacement', async () => {
  let posts = 0;
  const failed = { ...completed, requestId: 'failed', status: 'failed', errorCode: 'strength_input_unavailable' };
  const h = harness({ runtimeApi: {
    workerActionStatus: async () => failed,
    workerAction: async () => { posts++; return completed; },
    waitForWorkerAction: async () => { throw new ApiError(503, 'failed', { payload: failed }); },
  } });
  h.seedPending({ requestId: 'failed', parameters, storedAt: Date.now() });
  assert.equal(await h.runScan({}, { forceRefresh: true }), true);
  assert.equal(posts, 1);
});

test('a missing recovered task is discarded instead of permanently blocking retries', async () => {
  let posts = 0;
  const h = harness({ runtimeApi: {
    workerActionStatus: async () => { throw new ApiError(404, 'missing'); },
    workerAction: async () => { posts++; return completed; },
  } });
  h.seedPending({ requestId: 'gone', parameters, storedAt: Date.now() });
  assert.equal(await h.runScan({}, { forceRefresh: true }), true);
  assert.equal(posts, 1);
});

test('superseded stale read does not submit an obsolete worker action or change current phase', async () => {
  const old = deferred();
  let posts = 0;
  const h = harness({
    runtimeApi: { workerAction: async () => { posts++; return completed; } },
    strengthApi: { scanEnvelope: async (params) => params.sector_id === 'old' ? old.promise : envelope('CURRENT') },
  });
  const prior = h.runScan({ sector_id: 'old' });
  assert.equal(await h.runScan({ sector_id: 'new' }), true);
  old.resolve({ ...envelope('OLD'), stale: true });
  await prior;
  assert.equal(posts, 0);
  assert.equal(h.state.rows[0].ticker, 'CURRENT');
  assert.equal(h.state.scanPhase, 'reused');
});

test('completed worker cannot report success with a pre-publication snapshot', async () => {
  const h = harness({ strengthApi: { scanEnvelope: async () => ({ ...envelope('OLD'), snapshotSavedAt: '2026-09-03T21:00:00Z' }) } });
  assert.equal(await h.runScan({}, { forceRefresh: true }), false);
  assert.equal(h.state.scanState, 'error');
  assert.equal(h.state.rows, null);
  assert.equal(h.pending(), null, 'an unverified completed task must not block the next refresh');
});

test('identity invalidation cancels stale owner continuation before it submits a task', async () => {
  const old = deferred();
  let generation = 0;
  let posts = 0;
  const h = harness({
    getMarketReadGeneration: () => generation,
    strengthApi: { scanEnvelope: () => old.promise },
    runtimeApi: { workerAction: async () => { posts++; return completed; } },
  });
  const scan = h.runScan({});
  generation++;
  old.resolve({ ...envelope(), stale: true });
  await scan;
  assert.equal(posts, 0);
  assert.equal(h.state.rows, null);
});

function discoveryHarness() {
  const old = deferred();
  const h = harness();
  const listeners = new Map();
  const scope = {
    ...h.scope, scanState: 'done', applied: {},
    strengthApi: { scanEnvelope: () => old.promise },
    document: { visibilityState: 'visible', addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: () => {} },
    window: { setInterval: () => 1, clearInterval: () => {} },
    useEffect: (fn) => { scope.cleanup = fn(); },
  };
  scope.scanSeq.current = 1;
  const start = source.indexOf("  useEffect(() => {\n    if (scanState !== 'done'");
  const end = source.indexOf('\n  /* 成交额', start);
  vm.runInNewContext(ts.transpileModule(source.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, scope);
  return { old, h, scope, tick: () => listeners.get('visibilitychange')() };
}

test('late discovery response cannot overwrite a subsequently selected parameter set', async () => {
  const { old, h, scope, tick } = discoveryHarness();
  tick();
  scope.scanSeq.current++;
  h.state.scanMeta = envelope('CURRENT');
  h.state.rows = h.state.scanMeta.rows;
  old.resolve({ ...envelope('OLD'), snapshotSavedAt: '2026-09-05T21:00:00Z' });
  await new Promise(setImmediate);
  assert.equal(h.state.rows[0].ticker, 'CURRENT');
});

test('late discovery response cannot update an unmounted page', async () => {
  const { old, h, scope, tick } = discoveryHarness();
  tick();
  scope.cleanup();
  h.state.scanMeta = envelope('CURRENT');
  h.state.rows = h.state.scanMeta.rows;
  old.resolve({ ...envelope('OLD'), snapshotSavedAt: '2026-09-05T21:00:00Z' });
  await new Promise(setImmediate);
  assert.equal(h.state.rows[0].ticker, 'CURRENT');
});

test('publication visibility retry stays bounded and never creates a second worker task', async () => {
  let posts = 0;
  let reads = 0;
  const h = harness({
    runtimeApi: { workerAction: async () => { posts++; return completed; } },
    strengthApi: { scanEnvelope: async () => { reads++; return reads === 1 ? { ...envelope('OLD'), snapshotSavedAt: '2026-09-03T21:00:00Z' } : envelope(); } },
  });
  assert.equal(await h.runScan({}, { forceRefresh: true }), true);
  assert.equal(posts, 1);
  assert.equal(reads, 2);
  assert.equal(h.state.rows[0].ticker, 'NEW');
});

test('post-task missing publication cannot trigger another refresh loop', async () => {
  let posts = 0;
  let reads = 0;
  const h = harness({
    runtimeApi: { workerAction: async () => { posts++; return completed; } },
    strengthApi: { scanEnvelope: async () => { reads++; throw new ApiError(503, 'missing', { bizCode: 'strength_snapshot_unavailable' }); } },
  });
  assert.equal(await h.runScan({}, { forceRefresh: true }), false);
  assert.equal(posts, 1);
  assert.equal(reads, 3);
});

test('runtime forwards real queued/running progress and stops polling a superseded scan', async () => {
  const runtimeSource = fs.readFileSync(new URL('../src/api/modules/runtime.ts', import.meta.url), 'utf8');
  const exports = {};
  const context = {
    exports, Date, Promise, DOMException,
    window: { setTimeout: (cb) => { queueMicrotask(cb); return 1; } },
    require: (name) => {
      if (name.includes('client')) return { ApiError };
      if (name.includes('screenerScanFlow')) return flow;
      return {};
    },
  };
  vm.runInNewContext(ts.transpileModule(runtimeSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, context);
  const api = exports.runtimeApi;
  const statuses = ['queued', 'running', 'completed'];
  let polls = 0;
  const phases = [];
  api.workerActionStatus = async () => ({ ...completed, status: statuses[polls++] });
  await api.waitForWorkerAction('task', 5000, { onProgress: (action) => phases.push(action.status) });
  assert.deepEqual(phases, statuses);
  let current = true;
  api.workerActionStatus = async () => { polls++; return { ...completed, status: 'running' }; };
  await assert.rejects(api.waitForWorkerAction('task', 5000, { shouldContinue: () => current, onProgress: () => { current = false; } }), (error) => error.name === 'AbortError');
  assert.equal(polls, 4);
});


test('expired scan GET cannot silently reuse fresh-looking metadata after a server failure', async () => {
  const strengthSource = fs.readFileSync(new URL('../src/api/modules/strength.ts', import.meta.url), 'utf8');
  const start = strengthSource.indexOf('function liveScan(');
  const end = strengthSource.indexOf('\n}\n', start) + 3;
  const scope = { ...live, marketGet, toQuery, mapScanRow: (row) => row, mapTierDistribution: () => null, applyParams: (rows) => rows };
  vm.runInNewContext(ts.transpileModule(strengthSource.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, scope);
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  let now = originalNow();
  let requests = 0;
  Date.now = () => now;
  globalThis.fetch = async () => {
    requests++;
    return new Response(JSON.stringify(requests === 1 ? { rows: [], _stale: false, source_status: 'active' } : { message: 'unavailable' }), { status: requests === 1 ? 200 : 503, headers: { 'Content-Type': 'application/json' } });
  };
  resetMarketReadState();
  try {
    assert.equal((await scope.liveScan(parameters)).stale, false);
    now += 31000;
    await assert.rejects(scope.liveScan(parameters), (error) => error.code === 503);
    assert.equal(requests, 2);
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
    resetMarketReadState();
  }
});


test('discovery failure marks retained results unverified without advancing successful times', async () => {
  const { old, h, tick } = discoveryHarness();
  h.state.scanMeta = envelope('RETAINED');
  h.state.rows = h.state.scanMeta.rows;
  h.state.lastScanAt = 123;
  h.state.queryCheckedAt = 456;
  tick();
  old.reject(new Error('offline'));
  await new Promise(setImmediate);
  assert.equal(h.state.scanMeta.stale, true);
  assert.equal(h.state.scanMeta.sourceStatus, 'unknown');
  assert.equal(h.state.rows[0].ticker, 'RETAINED');
  assert.equal(h.state.lastScanAt, 123);
  assert.equal(h.state.queryCheckedAt, 456);
});

test('visible discovery marks locally expired metadata stale before waiting for its GET', async () => {
  const { old, h, tick } = discoveryHarness();
  h.state.scanMeta = { ...envelope('EXPIRED'), cacheExpiresAt: new Date(Date.now() - 1000).toISOString() };
  h.state.lastScanAt = 123;
  h.state.queryCheckedAt = 456;
  tick();
  const markedBeforeResponse = h.state.scanMeta.stale;
  // A still-cached body must not reverse the local expiration decision.
  old.resolve({ ...h.state.scanMeta, stale: false, sourceStatus: 'active' });
  await new Promise(setImmediate);
  assert.equal(markedBeforeResponse, true);
  assert.equal(h.state.scanMeta.stale, true);
});

test('a late discovery failure cannot mark newer parameters or an unmounted page stale', async () => {
  for (const dispose of [false, true]) {
    const { old, h, scope, tick } = discoveryHarness();
    tick();
    if (dispose) scope.cleanup();
    else scope.scanSeq.current++;
    h.state.scanMeta = envelope('CURRENT');
    old.reject(new Error('old connection failed'));
    await new Promise(setImmediate);
    assert.equal(h.state.scanMeta.stale, false);
    assert.equal(h.state.scanMeta.sourceStatus, 'active');
  }
});
