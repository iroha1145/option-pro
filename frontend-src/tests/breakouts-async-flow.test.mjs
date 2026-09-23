import { deferred } from './helpers/deferred.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { ApiError } from '../src/api/client.ts';
import {
  nextChoiceGeneration,
  shouldApplyRemoteAlgorithmPreference,
  historyPageDecision,
  shouldCommitHistoryPage,
} from '../src/lib/choiceGeneration.ts';

const source = fs.readFileSync(new URL('../src/pages/Breakouts.tsx', import.meta.url), 'utf8');

function extract(name, startNeedle, endNeedle) {
  const start = source.indexOf(startNeedle);
  const end = source.indexOf(endNeedle, start);
  return ts.transpileModule(
    `${source.slice(start, end)}\nglobalThis.${name} = ${name};`,
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } },
  ).outputText;
}

function harness() {
  const firstPersist = deferred();
  const secondPersist = deferred();
  const persistQueue = [firstPersist, secondPersist];
  const laterPage = deferred();
  const pageQueue = [laterPage];
  const state = {
    radarSort: 't1_daily_priority',
    extraEvents: [{ event_id: 'keep' }],
    historyCursor: 'prod-page-2',
    historyMoreError: 'seed',
    historyLoadingMore: false,
    persisted: [],
    toasts: [],
    eventRequests: [],
    invalidations: [],
  };
  const scope = {
    ApiError,
    Promise,
    Object,
    HISTORY_PAGE_SIZE: 100,
    useCallback: (fn) => fn,
    nextChoiceGeneration,
    shouldCommitHistoryPage,
    historyPageDecision,
    writeAlgorithmPreferences: (patch) => { state.local = patch; },
    persistAlgorithmChoice: async (patch) => {
      state.persisted.push(patch);
      return persistQueue.shift().promise;
    },
    invalidateQueryPaths: (paths) => { state.invalidations.push(paths); },
    bumpAlgorithmViewGeneration: () => {},
    toast: { info(title) { state.toasts.push(title); } },
    __t: (text) => text,
    isSignedIn: true,
    principal: 'account:alice',
    choiceGeneration: { current: 1 },
    historyGeneration: { current: 1 },
    historyRequestId: { current: 1 },
    historyCursor: 'prod-page-2',
    historyLoadingMore: false,
    radarSort: 'production',
    historyCursorRef: { current: 'prod-page-2' },
    requestedSortRef: { current: 'production' },
    setRadarSort: (value) => { state.radarSort = value; scope.radarSort = value; scope.requestedSortRef.current = value; },
    setExtraEvents: (value) => {
      state.extraEvents = typeof value === 'function' ? value(state.extraEvents) : value;
    },
    setHistoryCursor: (value) => {
      state.historyCursor = value;
      scope.historyCursor = value;
      scope.historyCursorRef.current = value;
    },
    setHistoryMoreError: (value) => { state.historyMoreError = value; },
    setHistoryLoadingMore: (value) => {
      state.historyLoadingMore = value;
      scope.historyLoadingMore = value;
    },
    breakoutsApi: {
      events: async (query) => {
        state.eventRequests.push(query);
        const slot = pageQueue.shift();
        if (!slot) throw new Error('unexpected history page request');
        return slot.promise;
      },
    },
  };
  vm.runInNewContext(extract(
    'beginHistoryEpoch',
    '  const beginHistoryEpoch = () => {',
    '\n  const applyHistoryFirstPage = (nextCursor: string | null) => {',
  ), scope);
  vm.runInNewContext(extract(
    'applyHistoryFirstPage',
    '  const applyHistoryFirstPage = (nextCursor: string | null) => {',
    '\n  useEffect(() => {\n    setRadarSort(',
  ), scope);
  vm.runInNewContext(extract(
    'loadMoreHistory',
    '  const loadMoreHistory = useCallback(',
    '\n  const personal = usePersonalWatchlist();',
  ), scope);
  vm.runInNewContext(extract(
    'updateRadarSort',
    '  const updateRadarSort = useCallback(',
    '\n  const effectiveRadar = currentQ.data?.effectiveAlgorithm',
  ), scope);
  return { state, scope, firstPersist, secondPersist, laterPage, pageQueue };
}

test('later production choice wins when an earlier T1 persist returns last', async () => {
  const { state, scope, firstPersist, secondPersist } = harness();
  scope.updateRadarSort('t1_daily_priority');
  scope.updateRadarSort('production');
  firstPersist.resolve({ radarSortAlgorithm: 't1_daily_priority', syncError: { code: 503 } });
  await new Promise(setImmediate);
  assert.equal(state.radarSort, 'production');
  assert.equal(state.toasts.length, 0);
  secondPersist.resolve({ radarSortAlgorithm: 'production', persisted: true });
  await new Promise(setImmediate);
  assert.equal(state.radarSort, 'production');
  assert.deepEqual(state.persisted.map((item) => item.radarSortAlgorithm), ['t1_daily_priority', 'production']);
});

test('stale production page two does not commit after a T1 switch', async () => {
  const { state, scope, laterPage, pageQueue } = harness();
  const loading = scope.loadMoreHistory();
  scope.updateRadarSort('t1_daily_priority');
  laterPage.resolve({
    items: [{ event_id: 'stale-prod-page-2' }],
    nextCursor: 'prod-page-3',
  });
  await loading;
  assert.equal(Array.from(state.extraEvents).length, 0);
  assert.equal(state.historyCursor, null);
  assert.equal(state.historyMoreError, null);
  assert.equal(state.historyLoadingMore, false);
  assert.equal(state.eventRequests[0].sort_algorithm, 'production');
  assert.equal(state.eventRequests[0].cursor, 'prod-page-2');
  scope.applyHistoryFirstPage('t1-page-2');
  assert.equal(state.historyCursor, 't1-page-2');
  assert.equal(state.historyLoadingMore, false);
  const nextPage = deferred();
  pageQueue.push(nextPage);
  const reload = scope.loadMoreHistory();
  assert.equal(state.historyLoadingMore, true);
  nextPage.resolve({
    items: [{ event_id: 't1-page-2-row' }],
    nextCursor: 't1-page-3',
  });
  await reload;
  assert.equal(state.extraEvents[0].event_id, 't1-page-2-row');
  assert.equal(state.historyCursor, 't1-page-3');
  assert.equal(state.historyLoadingMore, false);
  assert.equal(state.eventRequests[1].sort_algorithm, 't1_daily_priority');
  assert.equal(state.eventRequests[1].cursor, 't1-page-2');
});

test('stale history error does not surface after a sort change', async () => {
  const { state, scope, laterPage, pageQueue } = harness();
  const loading = scope.loadMoreHistory();
  scope.updateRadarSort('t1_daily_priority');
  laterPage.reject(new ApiError(503, 'stale page failed'));
  await loading;
  assert.equal(state.historyMoreError, null);
  assert.equal(Array.from(state.extraEvents).length, 0);
  assert.equal(state.historyLoadingMore, false);
  scope.applyHistoryFirstPage('t1-page-2');
  const nextPage = deferred();
  pageQueue.push(nextPage);
  const reload = scope.loadMoreHistory();
  nextPage.resolve({
    items: [{ event_id: 't1-after-error' }],
    nextCursor: null,
  });
  await reload;
  assert.equal(state.extraEvents[0].event_id, 't1-after-error');
  assert.equal(state.historyLoadingMore, false);
});

test('first-page refresh releases loading so the new cursor can load', async () => {
  const { state, scope, laterPage, pageQueue } = harness();
  const loading = scope.loadMoreHistory();
  assert.equal(state.historyLoadingMore, true);
  scope.applyHistoryFirstPage('prod-page-2-fresh');
  laterPage.resolve({
    items: [{ event_id: 'stale-prod-page-2' }],
    nextCursor: 'prod-page-3',
  });
  await loading;
  assert.equal(Array.from(state.extraEvents).length, 0);
  assert.equal(state.historyCursor, 'prod-page-2-fresh');
  assert.equal(state.historyLoadingMore, false);
  const nextPage = deferred();
  pageQueue.push(nextPage);
  const reload = scope.loadMoreHistory();
  assert.equal(state.historyLoadingMore, true);
  nextPage.resolve({
    items: [{ event_id: 'fresh-page-2' }],
    nextCursor: 'prod-page-3-fresh',
  });
  await reload;
  assert.equal(state.extraEvents[0].event_id, 'fresh-page-2');
  assert.equal(state.historyCursor, 'prod-page-3-fresh');
  assert.equal(state.historyLoadingMore, false);
});

test('same-generation load more still rejects a second in-flight request', async () => {
  const { state, scope, laterPage } = harness();
  const first = scope.loadMoreHistory();
  const second = scope.loadMoreHistory();
  assert.equal(state.historyLoadingMore, true);
  laterPage.resolve({
    items: [{ event_id: 'only-once' }],
    nextCursor: 'prod-page-3',
  });
  await first;
  await second;
  assert.equal(state.eventRequests.length, 1);
  assert.equal(state.extraEvents[1].event_id, 'only-once');
  assert.equal(state.historyLoadingMore, false);
});

test('stale T1 cursor restarts first page and does not append old rows', async () => {
  const { state, scope, laterPage, pageQueue } = harness();
  scope.radarSort = 't1_daily_priority';
  scope.requestedSortRef.current = 't1_daily_priority';
  const loading = scope.loadMoreHistory();
  laterPage.resolve({
    items: [{ event_id: 'should-not-append' }],
    nextCursor: 't1-page-3',
    cursorStale: true,
    restartRequired: true,
  });
  await loading;
  assert.equal(Array.from(state.extraEvents).length, 0);
  assert.equal(state.historyCursor, null);
  assert.equal(state.historyMoreError, null);
  assert.equal(state.historyLoadingMore, false);
  assert.equal(
    state.invalidations.some((paths) => Array.isArray(paths) && paths[0] === '/breakouts/events'),
    true,
  );
  scope.applyHistoryFirstPage('t1-page-2-fresh');
  const nextPage = deferred();
  pageQueue.push(nextPage);
  const reload = scope.loadMoreHistory();
  nextPage.resolve({
    items: [{ event_id: 'fresh-after-restart' }],
    nextCursor: 't1-page-3-fresh',
  });
  await reload;
  assert.equal(state.extraEvents[0].event_id, 'fresh-after-restart');
  assert.equal(state.historyCursor, 't1-page-3-fresh');
  assert.equal(state.historyLoadingMore, false);
});

test('late remote radar preference does not override a newer user choice', () => {
  assert.equal(shouldApplyRemoteAlgorithmPreference({
    startedGeneration: 1,
    currentGeneration: 2,
    cancelled: false,
  }), false);
});
