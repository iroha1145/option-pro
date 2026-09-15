import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { ApiError } from '../src/api/client.ts';
import {
  nextChoiceGeneration,
  shouldApplyRemoteAlgorithmPreference,
  shouldCommitHistoryPage,
} from '../src/lib/choiceGeneration.ts';

const source = fs.readFileSync(new URL('../src/pages/Breakouts.tsx', import.meta.url), 'utf8');
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
};

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
  const state = {
    radarSort: 't1_daily_priority',
    extraEvents: [{ event_id: 'keep' }],
    historyCursor: 'prod-page-2',
    historyMoreError: 'seed',
    persisted: [],
    toasts: [],
    eventRequests: [],
  };
  const scope = {
    ApiError,
    Promise,
    Object,
    HISTORY_PAGE_SIZE: 100,
    useCallback: (fn) => fn,
    nextChoiceGeneration,
    shouldCommitHistoryPage,
    requestedRadarAlgorithm: (value) => value,
    writeAlgorithmPreferences: (patch) => { state.local = patch; },
    persistAlgorithmChoice: async (patch) => {
      state.persisted.push(patch);
      return persistQueue.shift().promise;
    },
    invalidateQueryPaths: () => {},
    toast: { info(title) { state.toasts.push(title); } },
    __t: (text) => text,
    isSignedIn: true,
    principal: 'account:alice',
    choiceGeneration: { current: 1 },
    historyGeneration: { current: 1 },
    historyCursor: 'prod-page-2',
    historyLoadingMore: false,
    requestedSort: 'production',
    historyCursorRef: { current: 'prod-page-2' },
    requestedSortRef: { current: 'production' },
    setRadarSort: (value) => { state.radarSort = value; scope.requestedSort = value; scope.requestedSortRef.current = value; },
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
    asFullEvent: (item) => item,
    breakoutsApi: {
      events: async (query) => {
        state.eventRequests.push(query);
        return laterPage.promise;
      },
    },
  };
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
  return { state, scope, firstPersist, secondPersist, laterPage };
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
  const { state, scope, laterPage } = harness();
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
  assert.equal(state.eventRequests[0].sort_algorithm, 'production');
  assert.equal(state.eventRequests[0].cursor, 'prod-page-2');
});

test('stale history error does not surface after a sort change', async () => {
  const { state, scope, laterPage } = harness();
  const loading = scope.loadMoreHistory();
  scope.updateRadarSort('t1_daily_priority');
  laterPage.reject(new ApiError(503, 'stale page failed'));
  await loading;
  assert.equal(state.historyMoreError, null);
  assert.equal(Array.from(state.extraEvents).length, 0);
});

test('late remote radar preference does not override a newer user choice', () => {
  assert.equal(shouldApplyRemoteAlgorithmPreference({
    startedGeneration: 1,
    currentGeneration: 2,
    cancelled: false,
  }), false);
});
