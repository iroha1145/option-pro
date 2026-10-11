/**
 * 首页「财报临近」改为读 GET /earnings/upcoming/highlights?limit=6：
 * - 选行规则：后端 _home_highlight_rows、前端 selectHomeEarnings（演示模式）与改版前
 *   首页在浏览器里对整份日历的排序，对同一份共用夹具逐行一致（后端那半在
 *   tests/test_earnings_home_highlights.py）；
 * - 首页只滤纽约日、截 6 行，不再排序，跨过纽约午夜也与改版前一致；
 * - 注册表：新路径有 fresh 窗口、不持久化，财报页刷新的前缀失效仍连带清掉首页条目；
 * - 财报页仍读完整 /earnings/upcoming。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

import {
  invalidateQueryPaths,
  queryConfigFor,
  registryGet,
  resetQueryRegistry,
  setQueryPrincipal,
} from '../src/api/queryRegistry.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, '..', 'src');
const FIXTURE = JSON.parse(fs.readFileSync(
  path.resolve(here, '..', '..', 'tests', 'fixtures', 'earnings_home_highlights_cases.json'),
  'utf8',
));
const source = (relative) => fs.readFileSync(path.join(src, relative), 'utf8');
const plain = (value) => JSON.parse(JSON.stringify(value));
const tickers = (rows) => plain(rows.map((row) => row.ticker));

/** 把 TS 模块转译成 CommonJS 放进 vm；stubs 按导入说明符替换依赖。 */
function loadModules(stubs, clock) {
  const modules = new Map();
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }
  function load(filename) {
    if (!path.extname(filename)) filename += '.ts';
    if (modules.has(filename)) return modules.get(filename).exports;
    const module = { exports: {} };
    modules.set(filename, module);
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
    }).outputText;
    vm.runInNewContext(output, {
      module, exports: module.exports, Intl, TextEncoder, Date: clock === undefined ? Date : FixedDate,
      require(id) {
        if (id.includes('i18n/core')) return { t: (value) => value, getLocale: () => 'zh', localeTag: () => 'zh-CN' };
        if (id === '@/api/client') return { ApiError: class extends Error {} };
        if (id in stubs) return stubs[id];
        return load(id.startsWith('@/') ? path.join(src, id.slice(2)) : path.resolve(path.dirname(filename), id));
      },
    });
    return module.exports;
  }
  return load;
}

function liveEarnings({ registryCalls = [], clock } = {}) {
  const load = loadModules({
    '../client': {
      get: async () => { throw new Error('unexpected get'); },
      post: async () => { throw new Error('unexpected post'); },
      toQuery: () => '',
      mockOr: (_fixture, live) => live(),
    },
    '../queryRegistry': {
      registryGet: async (requestPath) => { registryCalls.push(requestPath); return { earnings: [] }; },
      restorePersistedQuery: async () => null,
    },
    '@/mocks/fixtures2': {},
  }, clock);
  return {
    api: load(path.join(src, 'api/modules/earnings.ts')),
    types: load(path.join(src, 'components/earnings/types.ts')),
  };
}

/** 改版前 Home.tsx（main 14c1924e，271–287 行）的选行代码，原样保留作对照。 */
function oldHomeCard(items, nyToday, poolTickers, { isFeaturedRow, exNum }) {
  return items
    .filter((it) => it.date >= nyToday)
    .slice()
    .sort((a, b) => {
      const ra = a;
      const rb = b;
      const fa = isFeaturedRow(ra, poolTickers) ? 0 : 1;
      const fb = isFeaturedRow(rb, poolTickers) ? 0 : 1;
      if (fa !== fb) return fa - fb;
      if (a.date !== b.date) return a.date.localeCompare(b.date);
      return (exNum(rb, 'marketCap') ?? -1) - (exNum(ra, 'marketCap') ?? -1);
    })
    .slice(0, 6);
}

/** 新首页对接口结果做的事（Home.tsx 的 earnings useMemo）。 */
function newHomeCard(items, nyToday, limit) {
  return items.filter((it) => it.date >= nyToday).slice(0, limit);
}

const addDay = (iso) => new Date(Date.parse(`${iso}T12:00:00Z`) + 86_400_000).toISOString().slice(0, 10);

for (const testCase of FIXTURE.cases) {
  test(`选行与夹具、改版前首页一致：${testCase.name}`, () => {
    const { api, types } = liveEarnings();
    // 后端行先过真实的 mapUpcomingPayload，和线上首页拿到的对象同形状。
    const items = api.mapUpcomingPayload({ earnings: testCase.rows }).items;
    const pool = new Set(FIXTURE.pool);
    const today = testCase.market_date;
    const tomorrow = addDay(today);
    const limit = testCase.limit;
    const oldCard = (day) => tickers(oldHomeCard(items, day, pool, types).slice(0, limit));

    assert.deepEqual(oldCard(today), testCase.expected.today);
    assert.deepEqual(oldCard(tomorrow), testCase.expected.tomorrow);

    const served = types.selectHomeEarnings(items, today, pool, limit);
    assert.deepEqual(tickers(served), testCase.expected.highlights);
    assert.ok(served.length <= 2 * limit);
    // 页面开着跨过纽约午夜：只滤掉今天的行，不重新请求也和整份日历现算一致。
    assert.deepEqual(tickers(newHomeCard(served, today, limit)), oldCard(today));
    assert.deepEqual(tickers(newHomeCard(served, tomorrow, limit)), oldCard(tomorrow));
  });
}

test('首页读新接口、只滤纽约日截 6 行，不再自己排序，也不再依赖关注池', () => {
  const home = source('pages/Home.tsx');
  assert.match(home, /const earningsQ = usePolling\(\(\) => earningsApi\.homeHighlights\(\), 300_000\);/);
  assert.doesNotMatch(home, /earningsApi\.upcoming\(\)/);
  const memo = home.slice(home.indexOf('const earnings = useMemo('), home.indexOf('/* 关注池异动'));
  assert.match(memo, /\.filter\(\(it\) => it\.date >= nyToday\)\.slice\(0, HOME_EARNINGS_ROWS\)/);
  assert.doesNotMatch(memo, /\.sort\(/);
  assert.doesNotMatch(home, /poolTickers|isFeaturedRow/);
  // 加载 / 错误 / 空态仍由同一个轮询状态驱动，文案不变。
  assert.match(home, /loading=\{earningsQ\.loading\}\s*error=\{earningsQ\.error\}\s*refreshing=\{earningsQ\.refreshing\}\s*onRetry=\{\(\) => earningsQ\.refresh\(\)\}\s*isEmpty=\{earnings\.length === 0\}\s*emptyTitle=\{t\('未来 30 天暂无财报'\)\}/);
  assert.match(home, /updatedAt=\{earningsQ\.lastUpdatedAt\}/);
});

test('线上模式请求 highlights?limit=6，并用同一个 mapUpcomingPayload 解析', async () => {
  const registryCalls = [];
  const { api } = liveEarnings({ registryCalls });
  assert.equal(api.HOME_EARNINGS_ROWS, 6);
  const result = await api.earningsApi.homeHighlights();
  assert.deepEqual(registryCalls, ['/earnings/upcoming/highlights?limit=6']);
  assert.deepEqual(plain(result.items), []);
  const mapped = api.mapUpcomingPayload({
    earnings: FIXTURE.cases[1].rows, as_of: '2026-07-22T14:00:00+00:00', data_limited: false,
    source_status: 'degraded', providers: ['Finnhub'], market_date: '2026-07-22', limit: 6,
  });
  assert.equal(mapped.asOf, '2026-07-22T14:00:00+00:00');
  assert.equal(mapped.sourceStatus, 'degraded');
  assert.deepEqual(plain(mapped.providers), ['Finnhub']);
});

for (const instant of ['2026-07-22T14:00:00Z', '2026-09-12T03:30:00Z', '2026-01-31T12:00:00Z']) {
  test(`演示模式与改版前的演示首页显示同样 6 行（${instant}）`, async () => {
    const clock = Date.parse(instant);
    const load = loadModules({}, clock);
    const fixtures2 = load(path.join(src, 'mocks/fixtures2.ts'));
    const fixtures = load(path.join(src, 'mocks/fixtures.ts'));
    const types = load(path.join(src, 'components/earnings/types.ts'));
    const { DEFAULT_WATCHLIST_TICKERS } = load(path.join(src, 'lib/personalWatchlist.ts'));
    const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(clock));
    // 改版前的演示首页：整份演示日历 + stocksApi.watchlist() 演示分支给出的关注池。
    const pool = new Set(fixtures.getWatchlist(true)
      .filter((row) => DEFAULT_WATCHLIST_TICKERS.includes(row.ticker))
      .map((row) => row.ticker.toUpperCase()));
    const oldCard = oldHomeCard(fixtures2.getEarningsUpcoming(), today, pool, types);
    const api = loadModules({
      '../client': { get: async () => null, post: async () => null, toQuery: () => '', mockOr: (fixture) => fixture() },
      '../queryRegistry': { registryGet: async () => { throw new Error('mock mode must not fetch'); }, restorePersistedQuery: async () => null },
      '@/mocks/fixtures2': fixtures2,
    }, clock)(path.join(src, 'api/modules/earnings.ts'));
    const result = await api.earningsApi.homeHighlights();
    assert.ok(oldCard.length > 0);
    assert.deepEqual(tickers(newHomeCard(result.items, today, 6)), tickers(oldCard));
  });
}

test('财报页仍读完整日历，刷新仍按 /earnings/upcoming 前缀硬失效', () => {
  const page = source('pages/Earnings.tsx');
  assert.match(page, /usePolling\(\(\) => earningsApi\.upcoming\(\), 1_800_000/);
  assert.match(page, /restore: restoreUpcomingFromCache/);
  assert.equal((page.match(/invalidateQueryPaths\(\['\/earnings\/upcoming'\], \{ reload: true \}\)/g) ?? []).length, 2);
});

test('注册表：新路径有 fresh 窗口、不持久化；财报页的前缀失效连带清掉首页条目', async () => {
  const highlights = '/earnings/upcoming/highlights?limit=6';
  assert.deepEqual(queryConfigFor(highlights), { ttlMs: 60_000 });
  assert.equal(queryConfigFor('/earnings/upcoming').persist, true);

  const originalFetch = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), cache: init?.cache });
    return new Response(JSON.stringify({ earnings: [], version: seen.length }), {
      status: 200, headers: { 'Content-Type': 'application/json', ETag: `"v${seen.length}"` },
    });
  };
  try {
    resetQueryRegistry();
    setQueryPrincipal('visitor\0');
    assert.equal((await registryGet(highlights)).version, 1);
    assert.equal((await registryGet(highlights)).version, 1, 'fresh 窗口内不重复请求');
    invalidateQueryPaths(['/earnings/upcoming'], { reload: true });
    assert.equal((await registryGet(highlights)).version, 2);
    assert.equal(seen.length, 2);
    assert.match(seen[0].url, /\/api\/earnings\/upcoming\/highlights\?limit=6$/);
    assert.equal(seen[1].cache, 'reload');
  } finally {
    globalThis.fetch = originalFetch;
    resetQueryRegistry();
  }
});
