// 「市场综合研判」mock 与归一器契约（node --experimental-strip-types --test）
//
// 覆盖：mocks/marketBrief.ts 与 tests/fixtures/market_brief_sample.json（三方共用的接口样例）逐字段
// 一致、能通过 mapLatest；live 路径读注册表、还没有研判时的 missing 响应；脏数据的防御性归一
// （列表项、代码、链接）；POST /runs 走 postCreate；冷启动恢复只在 live 用；演示模式的「现在生成」
// 约 8 秒后换成一份手动研判。全部离线。
//
// 转译后在当前 realm 求值（同 macro-conditions-contract）：跨 realm 的对象会让 deepStrictEqual 误报。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(here, '..', 'src');
const SAMPLE_PATH = path.resolve(here, '..', '..', 'tests', 'fixtures', 'market_brief_sample.json');
const CORE = { t: (msgid) => msgid, getLocale: () => 'zh', localeTag: () => 'zh-CN' };

function loadModule(rel, resolve) {
  const compiled = ts.transpileModule(fs.readFileSync(path.join(srcDir, rel), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', compiled)(module, module.exports, resolve);
  return module.exports;
}

const unexpected = (id) => {
  throw new Error(`unexpected import: ${id}`);
};
const LIVE = loadModule('api/live.ts', (id) => (id.includes('i18n/core') ? CORE : unexpected(id)));
const sample = () => JSON.parse(fs.readFileSync(SAMPLE_PATH, 'utf8'));

/** 每个用例一套新的桩与模块实例：mock 模块里有「手动生成」的进程内状态。 */
function harness({ mock = false } = {}) {
  const state = { mock, calls: [], registryBody: sample(), persisted: null, postReply: { data: undefined } };
  const MOCK = loadModule('mocks/marketBrief.ts', unexpected);
  const client = {
    get isMock() {
      return state.mock;
    },
    mockOr: (fixture, live) => (state.mock ? fixture() : live()),
    postCreate: async (url, body) => {
      state.calls.push(['POST', url, body]);
      return state.postReply;
    },
  };
  const API = loadModule('api/modules/marketBrief.ts', (id) => {
    if (id === '../client') return client;
    if (id === '../queryRegistry') {
      return {
        registryGet: async (url) => {
          state.calls.push(['GET', url]);
          return state.registryBody;
        },
        restorePersistedQuery: async (url) => {
          state.calls.push(['RESTORE', url]);
          return state.persisted;
        },
      };
    }
    if (id === '../live') return LIVE;
    if (id === '@/mocks/marketBrief') return MOCK;
    return unexpected(id);
  });
  return { state, MOCK, API };
}

test('mock 与接口样例 JSON 逐字段一致', () => {
  const { MOCK } = harness();
  assert.deepEqual(MOCK.getMarketBriefLatest(), sample());
  assert.deepEqual(MOCK.MARKET_BRIEF_SAMPLE, sample());
  assert.notEqual(MOCK.getMarketBriefLatest(), MOCK.getMarketBriefLatest(), '每次读取都是新对象，调用方改不到样例本身');
});

test('mock 通过 mapLatest：顶层 camelCase，result 保留 schema 字段名', async () => {
  const { MOCK, API } = harness({ mock: true });
  const latest = API.mapLatest(MOCK.getMarketBriefLatest());
  assert.equal(latest.status, 'ok');
  assert.equal(latest.schemaVersion, 'market-brief-v1');
  assert.equal(latest.latestAttempt, null);
  assert.deepEqual(latest.nextSlot, { slot: 'pre_open', at: '2026-10-09T12:40:00Z' });
  assert.equal(latest.snapshotSavedAt, '2026-10-09T03:41:13Z');
  const brief = latest.brief;
  assert.equal(brief.runId, 'mb_20261008_post_close_5f1c3a9e');
  assert.equal(brief.tradingDate, '2026-10-08');
  assert.equal(brief.slot, 'post_close');
  assert.equal(brief.trigger, 'scheduled');
  assert.equal(brief.generatedAt, '2026-10-09T03:41:12Z');
  assert.deepEqual(brief.model, { id: 'claude-opus-5-5', label: 'Claude Opus 5.5', effort: 'xhigh' });
  assert.deepEqual(brief.coverage, {
    universeSize: 5894,
    scoredCount: 4410,
    quotesValid: 5,
    breadthBasis: 'sector_etf_proxy_11',
    dataThrough: {
      indices: '2026-10-08T20:05:12Z',
      market_signals: '2026-10-08T20:02:40Z',
      eod_batch: '2026-10-08',
      macro: '2026-10-08',
      news: '2026-10-09T03:20:00Z',
      calendar: '2026-10-09T03:20:00Z',
    },
    missingBlocks: [{ block: 'sector_iv', reason: 'snapshot_missing' }],
  });
  assert.deepEqual(brief.externalSources, [
    { url: 'https://www.federalreserve.gov/monetarypolicy/fomcminutes20261001.htm', title: 'FOMC Minutes, October 2026' },
    { url: 'https://www.bls.gov/schedule/news_release/ppi.htm', title: 'PPI release schedule' },
  ]);
  assert.deepEqual(brief.validationWarnings, []);
  assert.equal(brief.webSearchCount, 6);

  const raw = sample().brief.result;
  const result = brief.result;
  assert.equal(result.headline, raw.headline);
  assert.equal(result.regime, 'narrow_leadership');
  assert.equal(result.evidence_sufficiency, 'medium');
  assert.deepEqual(result.internals, { summary: raw.internals.summary, points: raw.internals.points, evidence_ids: raw.internals.evidence_ids, breadth_vs_index: 'diverges' });
  assert.deepEqual(result.macro_check, { summary: raw.macro_check.summary, points: raw.macro_check.points, evidence_ids: raw.macro_check.evidence_ids, verdict: 'mixed' });
  assert.deepEqual(result.sectors, raw.sectors);
  assert.deepEqual(result.key_news, raw.key_news);
  assert.deepEqual(result.watch_items, raw.watch_items);
  assert.deepEqual(result.invalidators, raw.invalidators);
  assert.equal(result.prior_review, raw.prior_review);
  assert.equal('output_language' in result, false, '只保留界面用到的字段');

  assert.deepEqual(await API.marketBriefApi.latest(), latest, 'mock 模式的 latest() 就是归一后的样例');
});

test('live：latest 读注册表；还没有研判时 brief 为 null，仍给最近一次失败与下一个时段', async () => {
  const { state, API } = harness();
  const ok = await API.marketBriefApi.latest();
  assert.equal(ok.brief.runId, 'mb_20261008_post_close_5f1c3a9e');
  assert.deepEqual(state.calls, [['GET', '/market-brief/latest']]);

  state.registryBody = {
    status: 'missing',
    schema_version: 'market-brief-v1',
    brief: null,
    latest_attempt: { error_code: 'evidence_unavailable', at: '2026-10-09T12:45:00Z', slot: 'pre_open', trading_date: '2026-10-09' },
    next_slot: { slot: 'post_close', at: '2026-10-09T20:45:00Z' },
    snapshot_saved_at: '2026-10-09T12:45:01Z',
  };
  const missing = await API.marketBriefApi.latest();
  assert.equal(missing.status, 'missing');
  assert.equal(missing.brief, null);
  assert.deepEqual(missing.latestAttempt, {
    runId: null,
    tradingDate: '2026-10-09',
    slot: 'pre_open',
    trigger: null,
    status: null,
    at: '2026-10-09T12:45:00Z',
    errorCode: 'evidence_unavailable',
  });
  assert.deepEqual(missing.nextSlot, { slot: 'post_close', at: '2026-10-09T20:45:00Z' });

  const bare = API.mapLatest({ brief: { result: {} }, next_slot: { slot: 'lunch', at: 'x' } });
  assert.equal(bare.status, 'ok', '没有 status 字段时按 brief 是否存在推断');
  assert.equal(bare.nextSlot, null, '认不出的时段不显示');
  assert.equal(API.mapLatest({ brief: { run_id: 'x' } }).brief, null, '没有 result 的 brief 当作没有');
  assert.equal(API.mapLatest(null).status, 'missing');
});

test('脏数据的防御性归一：空串与非字符串剔除，代码大写并校验，缺字段为 null 不造值', () => {
  const { API } = harness();
  const result = API.mapResult({
    headline: '   ',
    internals: { summary: ' 摘要 ', points: ['甲', 3, '  ', null, '乙'], evidence_ids: 'idx:^GSPC' },
    macro_check: null,
    sectors: [{ name: '', change: 'noise' }, { name: '半导体', change: 'boom', note: '' }, 'oops'],
    key_news: [
      { title_zh: '', priced_in: 'yes' },
      { title_zh: '标题', priced_in: 'maybe', tickers: ['mu', 'bad ticker!', '^GSPC', 7, 'brk.b'] },
    ],
    watch_items: [{ what: '' }, { what: '观察', why: 5 }],
    invalidators: 'nope',
    prior_review: '',
  });
  assert.equal(result.headline, null);
  assert.deepEqual(result.internals, { summary: '摘要', points: ['甲', '乙'], evidence_ids: [], breadth_vs_index: 'unknown' });
  assert.deepEqual(result.macro_check, { summary: null, points: [], evidence_ids: [], verdict: 'unknown' });
  assert.deepEqual(result.sectors, [{ name: '半导体', change: 'unknown', note: null, evidence_ids: [] }]);
  assert.deepEqual(result.key_news, [{ evidence_id: null, title_zh: '标题', what_is_new: null, priced_in: 'unclear', tickers: ['MU', '^GSPC', 'BRK.B'] }]);
  assert.deepEqual(result.watch_items, [{ what: '观察', why: null, revise_if: null }]);
  assert.deepEqual(result.invalidators, []);
  assert.equal(result.prior_review, null);
  assert.equal(result.evidence_sufficiency, null);

  const coverage = API.mapLatest({
    brief: { result: {}, coverage: { universe_size: '5894', data_through: { news: null, macro: ' 2026-10-08 ' }, missing_blocks: ['news', { block: '' }, { block: 'macro', reason: 'disabled' }] } },
  }).brief.coverage;
  assert.equal(coverage.universeSize, 5894);
  assert.equal(coverage.scoredCount, null);
  assert.deepEqual(coverage.dataThrough, { macro: '2026-10-08' });
  assert.deepEqual(coverage.missingBlocks, [{ block: 'news', reason: null }, { block: 'macro', reason: 'disabled' }]);
});

test('外部来源只保留 http(s) 链接并去重', () => {
  const { API } = harness();
  const sources = API.mapLatest({
    brief: {
      result: {},
      external_sources: [
        { url: 'javascript:alert(1)', title: 'x' },
        { url: 'https://example.com/a', title: 'A' },
        { url: 'https://example.com/a', title: 'A again' },
        { url: 'ftp://example.com/b' },
        { url: 'not a url' },
        { url: 'http://example.org/c' },
      ],
    },
  }).brief.externalSources;
  assert.deepEqual(sources, [
    { url: 'https://example.com/a', title: 'A' },
    { url: 'http://example.org/c', title: null },
  ]);
});

test('POST /runs 走 postCreate；202 不带正文也能归一', async () => {
  const { state, API } = harness();
  state.postReply = {
    data: { request_id: 'req-1', status: 'queued', reason: '', reused: false, cooldown_until: null, error_code: null },
  };
  assert.deepEqual(await API.marketBriefApi.trigger(), { requestId: 'req-1', status: 'queued', reason: null, cooldownUntil: null, errorCode: null });
  state.postReply = { data: undefined };
  assert.deepEqual(await API.marketBriefApi.trigger('pre_open'), { requestId: null, status: null, reason: null, cooldownUntil: null, errorCode: null });
  assert.deepEqual(state.calls, [
    ['POST', '/market-brief/runs', {}],
    ['POST', '/market-brief/runs', { slot: 'pre_open' }],
  ]);
});

test('冷启动恢复：live 读持久记录并归一，mock 模式不读', async () => {
  const live = harness();
  assert.equal(await live.API.marketBriefApi.restore(), null);
  live.state.persisted = sample();
  const restored = await live.API.marketBriefApi.restore();
  assert.equal(restored.brief.runId, 'mb_20261008_post_close_5f1c3a9e');
  assert.deepEqual(live.state.calls, [['RESTORE', '/market-brief/latest'], ['RESTORE', '/market-brief/latest']]);

  const mock = harness({ mock: true });
  assert.equal(await mock.API.marketBriefApi.restore(), null);
  assert.deepEqual(mock.state.calls, []);
});

test('演示模式的「现在生成」：约 8 秒后换成一份手动研判，期间重复提交报告已在跑', async () => {
  const { MOCK, API } = harness({ mock: true });
  const realNow = Date.now;
  let clock = Date.parse('2026-10-09T14:00:00Z');
  Date.now = () => clock;
  try {
    const first = await API.marketBriefApi.trigger();
    assert.equal(first.status, 'queued');
    assert.equal(first.reason, null);
    const again = await API.marketBriefApi.trigger();
    assert.equal(again.reason, 'already_running');
    assert.equal(again.errorCode, 'market_brief_in_progress');
    assert.equal(MOCK.getMarketBriefLatest().brief.run_id, 'mb_20261008_post_close_5f1c3a9e', '未到时间仍是原研判');
    clock += 8_000;
    const done = API.mapLatest(MOCK.getMarketBriefLatest());
    assert.equal(done.brief.runId, 'mb_20261008_post_close_5f1c3a9e_manual_1');
    assert.equal(done.brief.trigger, 'manual');
    assert.equal(done.brief.generatedAt, '2026-10-09T14:00:08.000Z');
    assert.equal(done.snapshotSavedAt, '2026-10-09T14:00:08.000Z');
    clock += 60_000;
    assert.equal(MOCK.getMarketBriefLatest().brief.run_id, 'mb_20261008_post_close_5f1c3a9e_manual_1', '之后读取保持同一份');
    assert.equal((await API.marketBriefApi.trigger()).status, 'queued', '上一份完成后可以再排一次');
  } finally {
    Date.now = realNow;
  }
});
