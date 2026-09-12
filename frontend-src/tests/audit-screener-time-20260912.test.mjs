import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { formatChartTime } from '../src/components/detail/chartTime.ts';
import { catalystSortReadiness, catalystSummaryUsable, CATALYST_SUMMARY_TTL_MS, EMPTY_CATALYST, DEFAULT_FILTERS, tierOf } from '../src/components/screener/types.ts';
import { macroToneOf } from '../src/lib/macroFit.ts';

const source = fs.readFileSync(new URL('../src/pages/Screener.tsx', import.meta.url), 'utf8');
const transpile = (code, jsx = false) => ts.transpileModule(code, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, ...(jsx ? { jsx: ts.JsxEmit.ReactJSX } : {}) },
}).outputText;
const settle = () => new Promise(setImmediate);

test('chart labels use New York trading dates in Tokyo, Shanghai, UTC and Los Angeles', () => {
  const previous = process.env.TZ;
  try {
    for (const timezone of ['Asia/Tokyo', 'Asia/Shanghai', 'UTC', 'America/Los_Angeles']) {
      process.env.TZ = timezone;
      assert.equal(formatChartTime('2026-09-11T04:00:00Z', '1d'), '2026-09-11');
      assert.equal(formatChartTime('2026-09-11T04:00:00Z', '1d', 'axis'), '09-11');
      assert.equal(formatChartTime('2026-09-11T20:00:00Z', '1d'), '2026-09-11');
      assert.equal(formatChartTime('2026-09-07T00:00:00-04:00', '1w'), '2026-09-07');
      assert.equal(formatChartTime('2026-09-11T18:30:00Z', '5m'), '2026-09-11 14:30 ET');
      assert.equal(formatChartTime('2026-09-11T18:30:00Z', '5m', 'axis'), '09-11 14:30');
    }
  } finally {
    if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous;
  }
});

test('chart formatter follows DST and preserves date-only trading dates without inventing times', () => {
  assert.equal(formatChartTime('2026-03-06T14:30:00Z', '15m'), '2026-03-06 09:30 ET');
  assert.equal(formatChartTime('2026-03-09T13:30:00Z', '15m'), '2026-03-09 09:30 ET');
  assert.equal(formatChartTime('2026-11-02T14:30:00Z', '1h'), '2026-11-02 09:30 ET');
  assert.equal(formatChartTime('2026-01-05', '1w'), '2026-01-05');
  assert.equal(formatChartTime('2026-01-05', '1d', 'axis'), '01-05');
  assert.equal(formatChartTime('2026-01-05', '5m'), '—');
  for (const value of ['', 'not a date', '2026-02-30', '2026-09-11T14:30:00']) {
    assert.equal(formatChartTime(value, '1d'), '—', `reject invalid or timezone-less input: ${value}`);
  }
});

test('axis, tooltip and last-bar wrappers share the formatter without modifying source bar timestamps', () => {
  const chart = fs.readFileSync(new URL('../src/components/detail/KlineChart.tsx', import.meta.url), 'utf8');
  const scope = { formatChartTime };
  for (const [startMarker, endMarker] of [
    ['function fmtAxisLabel(', '/** 读回 ECharts'],
    ['function lastBarText(', '/** 只认已命名'],
  ]) {
    const start = chart.indexOf(startMarker);
    vm.runInNewContext(transpile(chart.slice(start, chart.indexOf(endMarker, start))), scope);
  }
  const bars = [{ t: '2026-09-11T18:30:00Z', c: 100 }];
  const original = JSON.stringify(bars);
  assert.equal(scope.fmtAxisLabel(bars[0].t, '5m'), '09-11 14:30');
  assert.equal(scope.barTooltipTitle(bars[0].t, '5m'), '2026-09-11 14:30 ET');
  assert.equal(scope.lastBarText({ bars }, '5m'), '2026-09-11 14:30 ET');
  assert.equal(JSON.stringify(bars), original);
});

function catalystHarness(rows, fetch) {
  let dependencies;
  let cleanup;
  let now = Date.now(), nextTimer = 0;
  const timers = new Map();
  const scope = {
    Date: { now: () => now }, Promise, Object, EMPTY_CATALYST, CATALYST_BATCH_SIZE: 20, CATALYST_SUMMARY_TTL_MS,
    window: {
      setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id; },
      clearTimeout: id => timers.delete(id),
    },
    catalystSummaryUsable, catalystSortReadiness,
    useCallback: fn => fn, useQuoteSymbols: () => {},
    scanState: 'done', catalystSortActive: true, catalystRetry: 0,
    catalystLoading: false, catalysts: {}, catalystsRef: { current: {} },
    filtered: rows, pageRows: rows.slice(0, 20), expanded: null,
    catalystsApi: { batchSummaries72h: fetch },
  };
  scope.setCatalysts = next => { scope.catalysts = next; };
  scope.setCatalystLoading = next => { scope.catalystLoading = next; };
  scope.setCatalystRetry = next => { scope.catalystRetry = next(scope.catalystRetry); };
  scope.useEffect = (fn, deps) => {
    if (dependencies && deps.every((value, index) => Object.is(value, dependencies[index]))) return;
    cleanup?.();
    dependencies = deps;
    cleanup = fn();
  };
  const start = source.indexOf('  const loadCatalystSummaries = useCallback(');
  const effects = source.indexOf('  // One request scope', start);
  const end = source.indexOf('  /* ---------------- 行展开', effects);
  vm.runInNewContext(transpile(source.slice(start, effects) + '\nglobalThis.loadCatalystSummaries = loadCatalystSummaries;'), scope);
  vm.runInNewContext(transpile(`function render() {
    const catalystReadiness = catalystSortReadiness(filtered.map(r => r.ticker), catalysts, Date.now());
    ${source.slice(effects, end)}
    globalThis.retry = retryCatalystSort;
  }`), scope);
  return { scope, render: () => scope.render(), cleanup: () => cleanup?.(), advance: ms => { now += ms; },
    expire: () => {
      const [id, timer] = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      timers.delete(id); now = Math.max(now, timer.at); timer.fn();
    },
  };
}

const news = (count = 1) => ({ count, pos: count, neg: 0, neu: 0, latestAt: '2026-09-11T18:00:00Z', latestTitle: '已发布新闻' });

test('failed catalyst batches settle as errors, do not loop, and explicit retry recovers only missing summaries', async () => {
  const rows = Array.from({ length: 22 }, (_, index) => ({ ticker: `T${String(index).padStart(2, '0')}` }));
  let fail = true;
  const requests = [];
  const h = catalystHarness(rows, async tickers => {
    requests.push([...tickers]);
    if (tickers.includes('T20') && fail) throw new Error('batch unavailable');
    return Object.fromEntries(tickers.map(ticker => [ticker, news()]));
  });
  h.render();
  await settle();
  h.render();
  assert.equal(requests.length, 2, 'response patches must not restart completed batches');
  assert.equal(h.scope.catalystLoading, false);
  const failed = catalystSortReadiness(rows.map(r => r.ticker), h.scope.catalysts, Date.now());
  assert.deepEqual(failed.failed, ['T20', 'T21']);
  assert.deepEqual(failed.missing, failed.failed);
  for (let index = 0; index < 3; index++) { h.render(); await settle(); }
  assert.equal(requests.length, 2, 'failure must wait for an explicit retry, not remain in an automatic loop');
  fail = false;
  h.scope.retry();
  h.render();
  assert.equal(h.scope.catalystLoading, true);
  await settle();
  assert.deepEqual(requests.at(-1), [...failed.failed].sort());
  assert.equal(h.scope.catalystLoading, false);
  assert.deepEqual(catalystSortReadiness(rows.map(r => r.ticker), h.scope.catalysts, Date.now()), { missing: [], failed: [] });
  h.cleanup();
});

test('failed summaries block catalyst ranking rather than count as zero news', () => {
  const scope = {
    useMemo: fn => fn(), Date, Math,
    filtered: [{ ticker: 'HIGH', strengthScore: 95, changePct: 0 }, { ticker: 'LOW', strengthScore: 70, changePct: 0 }],
    sortMode: 'impact', catalystSortIncomplete: true,
    catalysts: { HIGH: { ...news(), loaded: true, failed: true }, LOW: { ...news(10), loaded: true } },
  };
  const start = source.indexOf('  const sorted = useMemo(');
  const code = source.slice(start, source.indexOf('  const totalPages', start));
  vm.runInNewContext(transpile(`function renderSort() { ${code}; return sorted; }`), scope);
  assert.deepEqual(Array.from(scope.renderSort(), row => row.ticker), ['HIGH', 'LOW']);
  scope.catalystSortIncomplete = false;
  scope.catalysts.HIGH = { ...news(0), loaded: true };
  assert.deepEqual(Array.from(scope.renderSort(), row => row.ticker), ['LOW', 'HIGH']);
});

test('expiry refresh reloads same-symbol summaries without requiring a new result object', async () => {
  let requests = 0;
  const h = catalystHarness([{ ticker: 'AAA' }], async () => { requests++; return { AAA: news(requests) }; });
  h.render();
  await settle();
  h.scope.filtered = [{ ticker: 'AAA' }];
  h.scope.pageRows = h.scope.filtered;
  h.render();
  assert.equal(requests, 1, 'same-symbol discovery must keep the current scope');
  h.expire();
  await settle();
  assert.equal(requests, 2);
  assert.equal(h.scope.catalysts.AAA.count, 2);
  assert.deepEqual(catalystSortReadiness(['AAA'], h.scope.catalysts, Date.now()), { missing: [], failed: [] });
  h.cleanup();
});

test('same-symbol 45-second discoveries do not starve a slow third catalyst batch', async () => {
  const rows = Array.from({ length: 60 }, (_, index) => ({ ticker: `T${String(index).padStart(2, '0')}` }));
  const requests = [];
  const h = catalystHarness(rows, tickers => new Promise((resolve, reject) => requests.push({ tickers, resolve, reject })));
  h.render();
  h.advance(19_000);
  requests[0].reject(new Error('first batch unavailable'));
  await settle();
  h.advance(19_000);
  requests[1].reject(new Error('second batch unavailable'));
  await settle();
  assert.equal(requests.length, 3);
  h.advance(7_000); // The normal discovery tick at 45 seconds supplies new row objects.
  h.scope.filtered = rows.map(row => ({ ...row })).reverse();
  h.scope.pageRows = h.scope.filtered.slice(0, 20);
  h.render();
  assert.equal(requests.length, 3, 'same symbol set and reordered scores must not restart the round');
  h.advance(12_000);
  requests[2].resolve(Object.fromEntries(requests[2].tickers.map(ticker => [ticker, news()])));
  await settle();
  h.render();
  assert.equal(h.scope.catalystLoading, false, 'the retry button becomes available after the complete round');
  assert.equal(rows.slice(40).filter(row => h.scope.catalysts[row.ticker]?.failed !== true && h.scope.catalysts[row.ticker]?.loaded).length, 20);
  assert.equal(catalystSortReadiness(rows.map(row => row.ticker), h.scope.catalysts, Date.now()).failed.length, 40);
  h.advance(33_000);
  h.scope.filtered = rows.map(row => ({ ...row }));
  h.render();
  assert.equal(requests.length, 3, 'later discovery cannot automatically loop through failed prefixes');
  h.cleanup();
});

test('cancelled catalyst scope cannot publish a late response into a new result scope', async () => {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  const h = catalystHarness([{ ticker: 'OLD' }], tickers => tickers.includes('OLD') ? pending : Promise.resolve({ NEW: news() }));
  h.render();
  h.scope.filtered = [{ ticker: 'NEW' }];
  h.scope.pageRows = h.scope.filtered;
  h.render();
  await settle();
  resolve({ OLD: news(10) });
  await settle();
  assert.equal(h.scope.catalysts.OLD, undefined);
  assert.equal(h.scope.catalysts.NEW.count, 1);
  assert.equal(h.scope.catalystLoading, false);
  h.cleanup();
});

test('macro filtering affects tier comparison and reset clears macro without relabeling existing rows', () => {
  const rows = [
    { ticker: 'S', strengthScore: 95, macroFit: 80 },
    { ticker: 'A', strengthScore: 85, macroFit: 80 },
    { ticker: 'B', strengthScore: 75, macroFit: 20 },
    { ticker: 'UNKNOWN', strengthScore: 96, macroFit: null },
  ];
  const requests = [];
  const scope = { useMemo: fn => fn(), useCallback: fn => fn, macroToneOf, tierOf, DEFAULT_FILTERS,
    filteredBase: rows, applied: { ...DEFAULT_FILTERS, profile: 'aggressive', tier: 'S', topN: 1 }, macroToneFilter: 'tailwind',
    setPage: () => {}, setDraft: () => {},
    runScan: filters => { requests.push(filters); },
  };
  scope.setMacroToneFilter = value => { scope.macroToneFilter = value; };
  scope.setApplied = fn => { scope.applied = fn(scope.applied); };
  const filterStart = source.indexOf('  const macroFilteredBase = useMemo(');
  const filterEnd = source.indexOf('\n  /**', filterStart);
  const countStart = source.indexOf('  const hitsByTier = useMemo(');
  const countEnd = source.indexOf('\n  /**', countStart);
  const resetStart = source.indexOf('  const patchApplied = useCallback(');
  const resetEnd = source.indexOf('  const onTierFromHistogram', resetStart);
  vm.runInNewContext(transpile(`function derive() {
    ${source.slice(filterStart, filterEnd)}
    ${source.slice(countStart, countEnd)}
    return { filtered, hitsByTier };
  }
  ${source.slice(resetStart, resetEnd)}
  globalThis.reset = resetAllFilters;`), scope);
  let result = scope.derive();
  assert.deepEqual(Array.from(result.filtered, row => row.ticker), ['S']);
  assert.equal(result.hitsByTier.S, 1);
  assert.equal(result.hitsByTier.A, 1, 'other tiers remain available for comparison');
  assert.equal(result.hitsByTier.B, 0);
  scope.macroToneFilter = 'neutral';
  assert.equal(scope.derive().filtered.length, 0);
  scope.reset();
  result = scope.derive();
  assert.equal(scope.macroToneFilter, 'all');
  assert.equal(scope.applied.profile, 'aggressive');
  assert.equal(requests[0].profile, 'balanced');
  assert.equal(requests[0].tier, 'all');
  assert.equal(result.filtered.length, 1, 'old applied tier and limit remain until the requested scan completes');
  assert.equal(result.hitsByTier.S, 2, 'unknown macro values return only after the macro filter is cleared');
});
