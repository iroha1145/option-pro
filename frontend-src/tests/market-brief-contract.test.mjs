// 首页「市场综合研判」前端契约（node --experimental-strip-types --test）
//
// 覆盖：Home.tsx 挂载位置与读注册表配置；schema.py 每个枚举值都有中文标签、归一器原样保留
// （直接读 schema.py 的 Literal 定义，不在测试里另抄一份清单）；失败原因码对齐 errors.py 与
// api/market_brief.py；缺失块名对齐 evidence.py 的 _SOURCES；覆盖条、「上一份」「最近一次失败」
// 「手动生成跟进」的判定；文案不写免责、AI 正文不经 t()。全部离线。
//
// 源码带 `@/` 别名，node 解析不了：沿用 macro-conditions-contract 的做法，用 typescript 转译后
// 在当前 realm 里求值（跨 realm 的对象会让 deepStrictEqual 因原型不同而误报）。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(here, '..', 'src');
const repoDir = path.resolve(here, '..', '..');
const read = (rel) => fs.readFileSync(path.join(srcDir, rel), 'utf8');
const readRepo = (rel) => fs.readFileSync(path.join(repoDir, rel), 'utf8');
const CJK = /[一-鿿]/;

/** i18n/core 的最小桩：回退原文（与 zh 界面一致），{name} 占位符按 core.ts 同款规则替换。 */
function stubT(msgid, vars) {
  return vars
    ? msgid.replace(/\{(\w+)\}/g, (whole, key) => (vars[key] === undefined || vars[key] === null ? whole : String(vars[key])))
    : msgid;
}
const CORE = { t: stubT, getLocale: () => 'zh', localeTag: () => 'zh-CN' };

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
const NUMERIC = loadModule('lib/numericFormat.ts', unexpected);
const FORMAT = loadModule('lib/format.ts', (id) => (id.includes('i18n/core') ? CORE : id === './numericFormat.ts' ? NUMERIC : unexpected(id)));
const TEXT = loadModule('components/home/marketBriefText.ts', (id) =>
  id.includes('i18n/core') ? CORE : id === '@/lib/format' ? FORMAT : unexpected(id),
);
const LIVE = loadModule('api/live.ts', (id) => (id.includes('i18n/core') ? CORE : unexpected(id)));
const MOCK = loadModule('mocks/marketBrief.ts', unexpected);
const API = loadModule('api/modules/marketBrief.ts', (id) => {
  if (id === '../client') return { isMock: true, mockOr: (fixture) => fixture(), postCreate: async () => ({ data: {} }) };
  if (id === '../queryRegistry') return { registryGet: unexpected, restorePersistedQuery: unexpected };
  if (id === '../live') return LIVE;
  if (id === '@/mocks/marketBrief') return MOCK;
  return unexpected(id);
});

/** schema.py 的 `Name = Literal["a", "b"]` → { Name: ['a', 'b'] }。 */
function schemaLiterals() {
  const schema = readRepo('backend/app/services/market_brief/schema.py');
  const out = {};
  for (const [, name, body] of schema.matchAll(/^(\w+) = Literal\[([^\]]+)\]$/gm)) {
    out[name] = [...body.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  }
  return out;
}

test('Home 在指数带之后、行2 之前挂市场综合研判卡', () => {
  const home = read('pages/Home.tsx');
  const indexBand = home.indexOf("aria-label={t('指数概览')}");
  const card = home.indexOf('<MarketBriefCard className="mt-6 md:mt-8" />');
  const row2 = home.indexOf('行2：市场状态 + 雷达信号');
  assert.ok(indexBand > 0 && card > indexBand && row2 > card, '卡片应位于指数带与行2 之间');
  assert.ok(home.lastIndexOf('</section>', card) > indexBand, '指数带 section 应先闭合');
  assert.match(home, /^import MarketBriefCard from '@\/components\/home\/MarketBriefCard';$/m);
  assert.equal(home.split('<MarketBriefCard').length, 2, '首页只挂一张');
});

test('latest 走共享读注册表：60 秒新鲜窗口、持久化最多恢复 3 天；手动生成走 postCreate', () => {
  assert.match(
    read('api/queryRegistry.ts'),
    /'\/market-brief\/latest': \{ ttlMs: 60_000, persist: true, maxRestoreAgeMs: 3 \* DAY_MS \}/,
  );
  const api = read('api/modules/marketBrief.ts');
  assert.match(api, /MARKET_BRIEF_LATEST_PATH = '\/market-brief\/latest'/);
  assert.match(api, /registryGet<unknown>\(MARKET_BRIEF_LATEST_PATH\)\.then\(mapLatest\)/);
  assert.match(api, /restorePersistedQuery<unknown>\(MARKET_BRIEF_LATEST_PATH\)/);
  assert.match(api, /postCreate<unknown>\('\/market-brief\/runs', slot \? \{ slot \} : \{\}\)/);
  const card = read('components/home/MarketBriefCard.tsx');
  assert.match(card, /usePolling\(\(\) => marketBriefApi\.latest\(\), POLL_MS, \[\], \{ restore: marketBriefApi\.restore \}\)/);
  assert.match(card, /const POLL_MS = 60_000;/);
  assert.match(card, /const FOLLOW_INTERVAL_MS = 20_000;/);
  assert.match(card, /const FOLLOW_TIMEOUT_MS = 25 \* 60_000;/);
  // 跟进时必须先硬失效再强制读，否则 60 秒新鲜窗口里的强制刷新拿回的还是旧研判。
  assert.match(card, /invalidateQueryPaths\(\[MARKET_BRIEF_LATEST_PATH\], \{ reload: true \}\);\s*refreshLatest\(\{ force: true \}\);/);
});

test('schema.py 的每个枚举值都有中文标签，归一器原样保留、认不出时回落', () => {
  const literals = schemaLiterals();
  const pairs = [
    ['BriefSlot', TEXT.SLOT_LABEL],
    ['Regime', TEXT.REGIME_LABEL],
    ['Sufficiency', TEXT.SUFFICIENCY_LABEL],
    ['Consistency', TEXT.BREADTH_LABEL],
    ['MacroVerdict', TEXT.VERDICT_LABEL],
    ['SectorChange', TEXT.CHANGE_LABEL],
    ['PricedIn', TEXT.PRICED_IN_LABEL],
  ];
  for (const [name, labels] of pairs) {
    assert.ok(literals[name]?.length, `schema.py 里找不到 ${name} = Literal[...]`);
    assert.deepEqual(Object.keys(labels).sort(), [...literals[name]].sort(), `${name} 的标签与 schema.py 不一致`);
    for (const [value, label] of Object.entries(labels)) {
      assert.match(label, CJK, `${name}.${value} 缺中文标签`);
    }
  }
  assert.deepEqual(Object.keys(TEXT.REGIME_TONE).sort(), [...literals.Regime].sort());
  assert.deepEqual(
    Object.entries(TEXT.REGIME_TONE).filter(([, tone]) => tone !== 'neutral'),
    [['broad_advance', 'up'], ['risk_off', 'down']],
    '只有方向明确的两档用涨跌色',
  );
  for (const regime of literals.Regime) assert.equal(API.mapResult({ regime }).regime, regime);
  for (const level of literals.Sufficiency) assert.equal(API.mapResult({ evidence_sufficiency: level }).evidence_sufficiency, level);
  for (const value of literals.Consistency) {
    assert.equal(API.mapResult({ internals: { breadth_vs_index: value } }).internals.breadth_vs_index, value);
  }
  for (const verdict of literals.MacroVerdict) assert.equal(API.mapResult({ macro_check: { verdict } }).macro_check.verdict, verdict);
  for (const change of literals.SectorChange) {
    assert.equal(API.mapResult({ sectors: [{ name: '半导体', change }] }).sectors[0].change, change);
  }
  for (const priced of literals.PricedIn) {
    assert.equal(API.mapResult({ key_news: [{ title_zh: '标题', priced_in: priced }] }).key_news[0].priced_in, priced);
  }
  const fallback = API.mapResult({ regime: 'moonshot', evidence_sufficiency: 'certain' });
  assert.equal(fallback.regime, 'uncertain');
  assert.equal(fallback.evidence_sufficiency, null, '证据充分度认不出时显「—」，不冒充「低」');
  assert.equal(fallback.internals.breadth_vs_index, 'unknown');
  assert.equal(fallback.macro_check.verdict, 'unknown');
});

test('失败原因码：任务列出的码给短句，errors.py 与 POST /runs 的拒绝码全部覆盖，其余显示原码', () => {
  const expected = {
    provider_auth_failed: '密钥无效',
    provider_rate_limited: '供应商限流',
    provider_server_error: '供应商故障',
    provider_unavailable: '无法连接',
    provider_refusal: '模型拒绝了本次请求',
    output_truncated: '输出被截断',
    output_not_json: '输出格式错误',
    schema_validation_failed: '输出未通过校验',
    evidence_unavailable: '证据不足未生成',
    budget_exceeded: '超出 token 上限',
    daily_run_limit_reached: '今日次数已用完',
  };
  for (const [code, text] of Object.entries(expected)) assert.equal(TEXT.attemptErrorText(code), text, code);

  const errors = readRepo('backend/app/services/market_brief/errors.py');
  const constants = Object.fromEntries([...errors.matchAll(/^([A-Z_]+) = "([a-z_]+)"/gm)].map((m) => [m[1], m[2]]));
  const runSet = /RUN_ERROR_CODES = frozenset\(\{([\s\S]*?)\}\)/.exec(errors)?.[1] ?? '';
  const runCodes = [...runSet.matchAll(/([A-Z_]+),/g)].map((m) => constants[m[1]]);
  assert.ok(runCodes.length >= 10 && runCodes.every(Boolean), 'errors.py 的 RUN_ERROR_CODES 解析失败');
  for (const code of runCodes) assert.match(TEXT.ATTEMPT_ERROR_TEXT[code] ?? '', CJK, `运行失败码 ${code} 缺短句`);

  const api = readRepo('backend/app/api/market_brief.py');
  const refusals = [...api.matchAll(/detail=\{\s*"code": "([a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(refusals.length >= 5, 'api/market_brief.py 的拒绝码解析失败');
  for (const code of refusals) {
    assert.notEqual(TEXT.triggerFailureText({ code: 409, bizCode: code, message: 'Conflict' }), 'Conflict', `拒绝码 ${code} 缺提示`);
  }

  assert.equal(TEXT.attemptErrorText('provider_refusal:cyber'), '模型拒绝了本次请求', '带后缀的码按前缀认');
  assert.equal(TEXT.attemptErrorText('brand_new_code'), 'brand_new_code');
  assert.equal(TEXT.attemptErrorText(null), '原因未知');
  assert.equal(TEXT.triggerFailureText({ code: 429, bizCode: 'daily_run_limit_reached' }), '今日次数已用完');
  assert.equal(TEXT.triggerFailureText({ code: 429 }), '请求过于频繁，请稍后再试');
  assert.equal(TEXT.triggerFailureText({ message: '请求超时，请重试' }), '请求超时，请重试');
});

test('缺失块名覆盖 evidence.py 的全部证据来源', () => {
  const evidence = readRepo('backend/app/services/market_brief/evidence.py');
  const sources = /_SOURCES[^=]*= \(([\s\S]*?)\n\)/.exec(evidence)?.[1] ?? '';
  const blocks = [...sources.matchAll(/\("([a-z_]+)", _\w+\)/g)].map((m) => m[1]);
  assert.ok(blocks.length >= 8, 'evidence.py 的 _SOURCES 解析失败');
  for (const block of blocks) assert.match(TEXT.BLOCK_LABEL[block] ?? '', CJK, `证据块 ${block} 缺中文名`);
});

test('覆盖条按样例给出：股票池、已评分、广度口径、最早两项数据截止、缺失块', () => {
  const brief = API.mapLatest(MOCK.getMarketBriefLatest()).brief;
  const items = TEXT.coverageItems(brief.coverage, '2026').map(({ label, value, warn }) => [label, value, Boolean(warn)]);
  assert.deepEqual(items, [
    ['股票池', '5,894', false],
    ['已评分', '4,410', false],
    ['广度口径', '11 只行业 ETF 代理', false],
    ['全市场扫描截止', '10-08', false],
    ['宏观截止', '10-08', false],
    ['缺', '板块波动率快照', true],
  ]);
  const later = TEXT.coverageItems(
    {
      ...brief.coverage,
      missingBlocks: [],
      dataThrough: { news: '2026-10-09T03:20:00Z', indices: '2026-10-08T20:05:12Z', mystery: '2026-01-01' },
    },
    '2026',
  );
  assert.deepEqual(
    later.slice(3).map(({ label, value }) => [label, value]),
    [['指数截止', '10-08 16:05'], ['新闻截止', '10-08 23:20']],
    '时刻换成美东；没登记的来源不进覆盖条；没有缺失块就不显示「缺」',
  );
  assert.equal(TEXT.shortDate('2025-12-31', '2026'), '2025-12-31', '跨年保留年份');
});

test('时段写法：交易日取 trading_date，生成时间与下一个时段都按美东', () => {
  assert.equal(TEXT.slotTag('post_close', '2026-10-08', '2026'), '收盘后 · 10-08');
  assert.equal(TEXT.generatedText('2026-10-08T21:05:00Z', '2026-10-08', '2026'), '生成于 美东 17:05');
  assert.equal(TEXT.generatedText('2026-10-09T03:41:12Z', '2026-10-08', '2026'), '生成于 美东 23:41');
  assert.equal(TEXT.generatedText('2026-10-09T12:10:00Z', '2026-10-08', '2026'), '生成于 美东 10-09 08:10', '次日补发带日期');
  assert.equal(
    TEXT.nextSlotText({ slot: 'pre_open', at: '2026-10-09T12:40:00Z' }, '2026'),
    '下一个时段：开盘前 · 10-09 美东 08:40',
  );
  assert.equal(TEXT.nextSlotText(null, '2026'), null);
});

test('「上一份」：工作日美东 09:00 之后交易日仍不是今天才标；周末与节假日交易时段不标', () => {
  const brief = { tradingDate: '2026-10-08' };
  const nextClose = { slot: 'post_close', at: '2026-10-09T20:45:00Z' };
  assert.equal(TEXT.isPreviousBrief(brief, nextClose, Date.parse('2026-10-09T13:30:00Z')), true, '周五 09:30');
  assert.equal(TEXT.isPreviousBrief(brief, nextClose, Date.parse('2026-10-09T12:30:00Z')), false, '周五 08:30 开盘前那份还没到点');
  assert.equal(TEXT.isPreviousBrief({ tradingDate: '2026-10-09' }, nextClose, Date.parse('2026-10-09T18:00:00Z')), false);
  assert.equal(
    TEXT.isPreviousBrief({ tradingDate: '2026-10-09' }, { slot: 'pre_open', at: '2026-10-12T12:40:00Z' }, Date.parse('2026-10-10T15:00:00Z')),
    false,
    '周六不标',
  );
  assert.equal(
    TEXT.isPreviousBrief({ tradingDate: '2026-10-09' }, { slot: 'pre_open', at: '2026-10-12T12:40:00Z' }, Date.parse('2026-10-10T22:00:00Z')),
    false,
    '周六晚上也不标',
  );
  assert.equal(TEXT.isPreviousBrief({ tradingDate: '2026-10-09' }, null, Date.parse('2026-10-11T15:00:00Z')), false, '周日、读不到下一个时段也不标');
  assert.equal(
    TEXT.isPreviousBrief({ tradingDate: '2026-11-25' }, { slot: 'pre_open', at: '2026-11-27T13:40:00Z' }, Date.parse('2026-11-26T15:00:00Z')),
    false,
    '感恩节交易时段：下一个时段已是改天开盘前',
  );
  assert.equal(
    TEXT.isPreviousBrief(brief, { slot: 'pre_open', at: '2026-10-12T12:40:00Z' }, Date.parse('2026-10-09T22:00:00Z')),
    true,
    '周五收盘后仍是周四的研判：当天两份都没有',
  );
  assert.equal(TEXT.isPreviousBrief({ tradingDate: null }, nextClose, Date.parse('2026-10-09T13:30:00Z')), false);
});

test('最近一次失败：只认比当前研判更新的失败记录，并写成一行说明', () => {
  const brief = API.mapLatest(MOCK.getMarketBriefLatest()).brief;
  const attempt = API.mapLatest({
    latest_attempt: { error_code: 'provider_auth_failed', at: '2026-10-09T12:45:00Z', slot: 'pre_open', trading_date: '2026-10-09' },
  }).latestAttempt;
  assert.deepEqual(attempt, {
    runId: null,
    tradingDate: '2026-10-09',
    slot: 'pre_open',
    trigger: null,
    status: null,
    at: '2026-10-09T12:45:00Z',
    errorCode: 'provider_auth_failed',
  });
  assert.equal(TEXT.failedAttemptAfter(attempt, brief), attempt);
  assert.equal(TEXT.failedAttemptAfter(attempt, null), attempt, '还没有研判时失败照样显示');
  assert.equal(TEXT.failedAttemptAfter({ ...attempt, at: '2026-10-08T21:00:00Z' }, brief), null, '早于当前研判的失败不显示');
  assert.equal(TEXT.failedAttemptAfter({ ...attempt, runId: brief.runId }, brief), null);
  assert.equal(TEXT.failedAttemptAfter({ ...attempt, errorCode: null, status: 'completed' }, brief), null);
  assert.equal(TEXT.attemptFailureText(attempt, '2026'), '最近一次 开盘前 · 10-09 生成失败：密钥无效');
  assert.equal(
    TEXT.attemptFailureText({ ...attempt, slot: null, errorCode: 'mystery' }, '2026'),
    '最近一次生成失败：mystery',
  );
});

test('手动生成跟进：新研判为 ready，新的失败记录为 failed，其余继续等', () => {
  const clickedAt = Date.parse('2026-10-09T12:40:00Z');
  const before = API.mapLatest(MOCK.getMarketBriefLatest());
  const baseline = TEXT.followBaseline(before, clickedAt);
  assert.equal(TEXT.followOutcome(baseline, before), null);
  assert.equal(TEXT.followOutcome(baseline, null), null);
  const fresh = { ...before, brief: { ...before.brief, runId: 'mb_20261009_pre_open_0a1b2c3d', generatedAt: '2026-10-09T12:52:00Z' } };
  assert.equal(TEXT.followOutcome(baseline, fresh), 'ready');
  const failed = { ...before, latestAttempt: { runId: null, tradingDate: '2026-10-09', slot: 'pre_open', trigger: null, status: null, at: '2026-10-09T12:50:00Z', errorCode: 'output_truncated' } };
  assert.equal(TEXT.followOutcome(baseline, failed), 'failed');
  const running = { ...before, latestAttempt: { ...failed.latestAttempt, errorCode: null, status: 'running' } };
  assert.equal(TEXT.followOutcome(baseline, running), null, '进行中的记录不算结果');

  // 点击时读不到（错误态）：之后读到的若是早就存在的研判或失败，不算这次的结果，继续等。
  const blind = TEXT.followBaseline(null, clickedAt);
  assert.equal(TEXT.followOutcome(blind, before), null, '样例研判生成于点击前十多小时');
  assert.equal(TEXT.followOutcome(blind, { ...before, brief: null, latestAttempt: { ...failed.latestAttempt, at: '2026-10-08T21:00:00Z' } }), null);
  assert.equal(TEXT.followOutcome(blind, fresh), 'ready');
  assert.equal(TEXT.followOutcome(blind, { ...before, brief: null, latestAttempt: null }), null);
  const skewed = { ...fresh, brief: { ...fresh.brief, generatedAt: '2026-10-09T12:35:00Z' } };
  assert.equal(TEXT.followOutcome(blind, skewed), 'ready', '服务器时钟略慢于浏览器也认');
});

test('POST /runs 的受理结果：新排队与已在跑都跟进，冷却中不跟进，同一分钟重复且已结束只重读', () => {
  const now = Date.parse('2026-10-09T13:00:00Z');
  const reply = (body) => TEXT.triggerReply(API.mapTrigger(body), now);
  assert.deepEqual(reply({ request_id: 'r1', status: 'queued', reason: '' }), { follow: true, refresh: false, title: null, description: null });
  assert.deepEqual(reply({ status: 'running', reason: 'already_running', error_code: 'market_brief_in_progress' }), {
    follow: true,
    refresh: false,
    title: '研判正在生成',
    description: '完成后自动显示',
  });
  assert.deepEqual(reply({ status: 'completed', reason: 'cooldown', error_code: 'market_brief_cooldown', cooldown_until: '2026-10-09T13:04:10Z' }), {
    follow: false,
    refresh: false,
    title: '手动生成冷却中',
    description: '约 5 分钟后可再次生成',
  });
  assert.deepEqual(reply({ status: 'completed', reason: 'idempotent' }), { follow: false, refresh: true, title: null, description: null });
  assert.equal(reply({ status: 'queued', reason: 'idempotent' }).follow, true);
  assert.equal(reply({}).follow, true, '202 不带正文时按新排队处理');
});

test('卡片：Owner 用 .btn-ai 并在途 aria-busy，访客有登录提示，重试是 control-button；不写免责，AI 正文不经 t()', () => {
  const card = read('components/home/MarketBriefCard.tsx');
  const content = read('components/home/MarketBriefContent.tsx');
  const text = read('components/home/marketBriefText.ts');
  assert.match(card, /className="btn-ai"[\s\S]{0,240}disabled=\{busy \|\| latestQ\.loading\}\s*aria-busy=\{busy\}/);
  assert.match(card, /t\('登录后可手动生成'\)/);
  assert.match(card, /className="control-button"/);
  assert.match(card, /<StaleStrip onRetry=\{retry\}/);
  assert.match(card, /aria-label=\{t\('市场综合研判'\)\}/);
  assert.match(content, /className="data-coverage-strip/);
  assert.match(content, /InfoHint hint=\{SCORE_HINTS\.marketBriefSufficiency\}/);
  assert.match(content, /target="_blank"\s+rel="noreferrer"/);
  for (const [name, source] of [['MarketBriefCard', card], ['MarketBriefContent', content], ['marketBriefText', text]]) {
    assert.doesNotMatch(source, /仅供参考|不构成|免责|投资建议/, `${name} 不写免责与合规腔`);
    assert.doesNotMatch(source, /font-semibold|font-bold/, `${name} 字重只用 400/500`);
  }
  assert.doesNotMatch(content, /\bt\(\s*(?:result|item|news|sector|source|brief)\b/, 'AI 正文不经 t()');
  const hint = read('lib/scoreHints.ts');
  assert.match(hint, /marketBriefSufficiency: \{[\s\S]{0,400}不是上涨概率/);
});
