// 「市场报告」图文版（node --experimental-strip-types --test）
//
// 覆盖：完成态协议（只有 html/css，没有脚本）、模型正文转义、图表数值只取 visuals、缺图不补、
// 宏观数据早于交易日的提醒、界面文字全部经 t()；渲染组件不再有开发/演示/网址参数的开关，
// 用 Shadow DOM 同步写入而不是 iframe。全部离线。
//
// 转译后在当前 realm 求值（同 market-brief-contract）：跨 realm 的对象会让 deepStrictEqual 误报。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(here, '..', 'src');
const read = (rel) => fs.readFileSync(path.join(srcDir, rel), 'utf8');
const SAMPLE = JSON.parse(fs.readFileSync(path.resolve(here, '..', '..', 'tests', 'fixtures', 'market_brief_sample.json'), 'utf8'));
const CJK = /[぀-ヿ㐀-䶿一-鿿＀-￯]/;

function loadModule(rel, resolve) {
  const compiled = ts.transpileModule(read(rel), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', compiled)(module, module.exports, resolve);
  return module.exports;
}
const unexpected = (id) => {
  throw new Error(`unexpected import: ${id}`);
};
const interpolate = (msgid, vars) => (vars
  ? msgid.replace(/\{(\w+)\}/g, (whole, key) => (vars[key] === undefined || vars[key] === null ? whole : String(vars[key])))
  : msgid);

/** marked=true 时 t() 把译文包进 ⟪⟫：剩下的中文就是没经过 t() 的字面量。 */
function harness({ marked = false } = {}) {
  const core = { t: (msgid, vars) => (marked ? `⟪${interpolate(msgid, vars)}⟫` : interpolate(msgid, vars)), getLocale: () => 'zh', localeTag: () => 'zh-CN' };
  const numeric = loadModule('lib/numericFormat.ts', unexpected);
  const format = loadModule('lib/format.ts', (id) => (id.includes('i18n/core') ? core : id === './numericFormat.ts' ? numeric : unexpected(id)));
  const text = loadModule('components/home/marketBriefText.ts', (id) => (id.includes('i18n/core') ? core : id === '@/lib/format' ? format : unexpected(id)));
  const live = loadModule('api/live.ts', (id) => (id.includes('i18n/core') ? core : unexpected(id)));
  const api = loadModule('api/modules/marketBrief.ts', (id) => {
    if (id === '../client') return { isMock: false, mockOr: (_fixture, liveCall) => liveCall(), postCreate: unexpected };
    if (id === '../queryRegistry') return { registryGet: unexpected, restorePersistedQuery: unexpected };
    if (id === '../live') return live;
    if (id === '@/mocks/marketBrief') return {};
    return unexpected(id);
  });
  const report = loadModule('components/home/marketBriefReport.ts', (id) => {
    if (id.includes('i18n/core')) return core;
    if (id === '@/lib/format') return format;
    if (id === './marketBriefText') return text;
    return unexpected(id);
  });
  const schema = loadModule('components/open-intelligent-ui/schema.ts', unexpected);
  return { api, report, schema };
}

const sampleBrief = (api, mutate) => {
  const body = structuredClone(SAMPLE);
  mutate?.(body.brief);
  return api.mapLatest(body).brief;
};
const htmlOf = (content) => content.html.join('');
const chartOf = (html, id) => {
  const start = html.indexOf(`data-chart="${id}"`);
  if (start < 0) return null;
  const end = html.indexOf('</figure>', start);
  return html.slice(start, end < 0 ? html.indexOf('</p></div>', start) : end);
};

test('样例报告生成完成态内容：只有 html/css，没有脚本、外链、内嵌框架或表单', () => {
  const { api, report, schema } = harness();
  const content = report.createMarketBriefReport(sampleBrief(api));
  const normalized = schema.normalizeOpenGenUIContent(content);
  assert.ok(normalized, '必须是完成态');
  assert.equal(normalized.jsFunctions, '');
  assert.deepEqual(JSON.parse(JSON.stringify(normalized.jsExpressions)), []);
  const html = htmlOf(content);
  assert.doesNotMatch(html, /<(script|iframe|form|object|embed|link|img)\b/i);
  assert.doesNotMatch(html, /\s(src|href|on\w+)=/i);
  assert.doesNotMatch(content.css, /@import|url\(|expression\(/i);
  assert.doesNotMatch(content.css, /<\/style/i);
  // 颜色读宿主的 CSS 变量：深浅色与涨跌配色由页面决定。
  for (const token of ['--up-600', '--down-600', '--up-700', '--down-700', '--ink-900', '--card', '--line', '--brand-600']) {
    assert.ok(content.css.includes(`var(${token}`), token);
  }
  assert.doesNotMatch(content.css, /font-weight:\s*[6-9]00|font-weight:\s*bold/, '字重只用 400/500');
});

test('图表数值只取 visuals：涨跌幅、超额收益、宏观分数与广度照原值画，坐标取整齐的上限', () => {
  const { api, report } = harness();
  const html = htmlOf(report.createMarketBriefReport(sampleBrief(api)));
  const market = chartOf(html, 'market');
  for (const value of ['+0.62%', '+0.91%', '+0.04%', '−0.35%']) assert.ok(market.includes(value), value);
  assert.ok(market.includes('−1%') && market.includes('+1%'), '最大 0.91 → 坐标 ±1%');
  assert.ok(market.includes('left:50.00%;width:31.00%'), '0.62 / 1 × 50% = 31%');
  assert.ok(market.includes('left:32.50%;width:17.50%'), '−0.35 从中线往左 17.5%');
  assert.match(market, /data-id="idx:\^RUT"/);
  const sectors = chartOf(html, 'sectors');
  assert.ok(sectors.includes('+8.42') && sectors.includes('−3.71'));
  assert.ok(sectors.includes('−10') && sectors.includes('+10'), '最大 8.42 → 坐标 ±10');
  assert.ok(!sectors.includes('%<'), '超额收益是百分点，数值不带 %');
  const macro = chartOf(html, 'macro');
  for (const value of ['61.2', '38.9', '49.6']) assert.ok(macro.includes(value), value);
  assert.ok(macro.includes('left:0.00%;width:61.20%'));
  const breadth = html.slice(html.indexOf('data-chart="breadth"'));
  assert.match(breadth, /6\/11/);
  assert.match(breadth, /54\.5%/);
  assert.equal((breadth.match(/class="filled"/g) ?? []).length, 6);
  assert.match(html, /单位：百分点，相对标普 500 · 数据交易日 10-08 · 只列报告引用的主题/);
  assert.match(html, /数据时点 纽约时间 10-08 16:05/, '证据时点换成纽约时间');
  assert.match(html, /数据截至 10-08/);
});

test('没有 visuals 就不生成图文版；缺哪张图就不画哪张，不用演示值补', () => {
  const { api, report } = harness();
  assert.equal(report.createMarketBriefReport(sampleBrief(api, (brief) => { brief.visuals = null; })), null);
  assert.equal(report.createMarketBriefReport(sampleBrief(api, (brief) => { delete brief.visuals; })), null);
  const onlyMacro = sampleBrief(api, (brief) => {
    brief.visuals.market = null;
    brief.visuals.breadth = null;
    brief.visuals.sectors = null;
  });
  const html = htmlOf(report.createMarketBriefReport(onlyMacro));
  assert.equal(chartOf(html, 'market'), null);
  assert.equal(chartOf(html, 'sectors'), null);
  assert.ok(!html.includes('data-chart="breadth"'));
  assert.ok(chartOf(html, 'macro'));
  assert.ok(html.includes(SAMPLE.brief.result.internals.summary), '正文照常');
  assert.ok(html.includes(SAMPLE.brief.result.sectors[0].note), '行业文字照常');
});

test('宏观数据早于报告交易日时单独提醒；快照不新鲜时图注写明；演示模式顶部标注示例', () => {
  const { api, report } = harness();
  const fresh = htmlOf(report.createMarketBriefReport(sampleBrief(api)));
  assert.ok(!fresh.includes('早于报告交易日'));
  assert.ok(!fresh.includes('快照可能不是最新'));
  assert.ok(!fresh.includes('演示数据'));
  const lagging = sampleBrief(api, (brief) => {
    brief.trading_date = '2026-10-09';
    brief.visuals.macro.data_through = '2026-10-02';
    brief.visuals.market.stale = true;
  });
  const html = htmlOf(report.createMarketBriefReport(lagging, { demo: true, year: '2026' }));
  assert.match(html, /宏观数据截至 2026-10-02，早于报告交易日 2026-10-09，不是报告当天的读数。/);
  assert.match(chartOf(html, 'market'), /快照可能不是最新/);
  assert.match(html, /演示数据：图表数值与正文均为示例/);
});

test('模型正文先转义再放进 HTML；正文不经 t()', () => {
  const { api, report } = harness();
  const malicious = '</p><script>window.top.location="https://example.com"</script><img src=x onerror=alert(1)><p>';
  const brief = sampleBrief(api, (raw) => {
    raw.result.headline = malicious;
    raw.result.internals.summary = malicious;
    raw.result.key_news[0].what_is_new = malicious;
    raw.result.watch_items[0].why = malicious;
    raw.result.sectors[0].name = malicious;
  });
  const html = htmlOf(report.createMarketBriefReport(brief));
  assert.doesNotMatch(html, /<script|<img/i);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&quot;https:\/\/example\.com&quot;/);
  const source = read('components/home/marketBriefReport.ts');
  assert.doesNotMatch(source, /\bt\(\s*(?:result|item|news|sector|read|watch|brief)\b/, 'AI 正文不经 t()');
});

test('界面文字与程序给的标签全部经 t()：英文正文时，t() 之外不剩中文', () => {
  const { api, report } = harness({ marked: true });
  const brief = sampleBrief(api, (raw) => {
    const r = raw.result;
    r.headline = 'Headline';
    r.internals.summary = 'Internals summary';
    r.internals.points = ['Point one', 'Point two'];
    r.macro_check.summary = 'Macro summary';
    r.macro_check.points = ['Macro point'];
    r.sectors = r.sectors.map((sector, index) => ({ ...sector, name: `Sector ${index}`, note: 'Sector note' }));
    r.key_news = [0, 1, 2].map((index) => ({ ...r.key_news[0], title_zh: `News ${index}`, what_is_new: 'What is new' }));
    r.watch_items = r.watch_items.map((item) => ({ ...item, what: 'Watch', why: 'Because', revise_if: 'Revise' }));
    r.invalidators = ['Invalidator'];
    raw.trading_date = '2026-10-09';
    raw.visuals.macro.data_through = '2026-10-02';
    raw.visuals.market.stale = true;
    raw.visuals.breadth.total = 9;
  });
  const html = htmlOf(report.createMarketBriefReport(brief, { demo: true }));
  const outside = html.replace(/⟪[^⟫]*⟫/g, '').replace(/<[^>]+>/g, ' ');
  assert.ok(!CJK.test(outside), `t() 之外还有中文：${(outside.match(/.{0,20}[぀-ヿ㐀-䶿一-鿿＀-￯].{0,20}/u) ?? [''])[0]}`);
  for (const label of ['⟪标普500⟫', '⟪罗素2000⟫', '⟪半导体⟫', '⟪信用⟫', '⟪覆盖 9/11⟫', '⟪其余 1 条新闻⟫']) assert.ok(html.includes(label), label);
});

test('visuals 归一：样例原样保留；坏数值、越界、重复 id、错 id 前缀逐行丢掉，整张图没行就是 null', () => {
  const { api } = harness();
  const visuals = JSON.parse(JSON.stringify(sampleBrief(api).visuals));
  assert.deepEqual(visuals, {
    market: { asOf: '2026-10-08T20:05:12Z', stale: false, rows: SAMPLE.brief.visuals.market.rows },
    breadth: { above: 6, total: 11, expected: 11, asOf: '2026-10-08T20:02:40Z', stale: false },
    sectors: { servedSession: '2026-10-08', rows: SAMPLE.brief.visuals.sectors.rows },
    macro: { dataThrough: '2026-10-08', rows: SAMPLE.brief.visuals.macro.rows },
  });
  const dirty = api.mapVisuals({
    market: {
      as_of: 'not a time',
      stale: 'yes',
      rows: [
        { id: 'idx:^GSPC', label: '标普500', value: '0.6' },
        { id: 'idx:^IXIC', label: '纳指综合', value: Number.NaN },
        { id: 'idx:^DJI', label: '道琼斯', value: 31 },
        { id: 'idx:^N225', label: '日经', value: 0.3 },
        { id: 'idx:^RUT', label: '罗素2000', value: -1.25 },
        { id: 'idx:^RUT', label: '罗素2000', value: 9 },
      ],
    },
    breadth: { above: 7, total: 6, expected: 11, as_of: '2026-10-08T20:02:40Z' },
    sectors: { served_session: '2026-10-08', rows: [{ id: 'sector:energy', label: '能源', value: 1 }, { id: 'theme:energy', label: '', value: 2 }] },
    macro: { data_through: '2026-10-02', rows: [{ id: 'macro:rates', label: '利率', value: 100.5 }, { id: 'macro:risk', label: '风险', value: 0 }] },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(dirty)), {
    market: { asOf: null, stale: false, rows: [{ id: 'idx:^RUT', label: '罗素2000', value: -1.25 }] },
    breadth: null,
    sectors: null,
    macro: { dataThrough: '2026-10-02', rows: [{ id: 'macro:risk', label: '风险', value: 0 }] },
  });
  assert.equal(api.mapVisuals({ market: null, breadth: { above: 1.5, total: 11, expected: 11 } }), null);
  assert.equal(api.mapVisuals('visuals'), null);
  assert.equal(api.mapLatest({ brief: { result: {} } }).brief.visuals, null, '旧响应没有 visuals 字段 → 文字版');
});

test('渲染组件：没有开发、演示或网址参数开关，用 Shadow DOM 同步写入，不再引用 iframe 渲染器', () => {
  // 只看代码，不看注释（注释里会说明为什么不用 iframe）。
  const output = read('components/home/MarketBriefModelOutput.tsx').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  const card = read('components/home/MarketBriefCard.tsx');
  assert.doesNotMatch(output, /import\.meta\.env|useSearchParams|searchParams|params\.get\(|'openui'|sample=/);
  assert.doesNotMatch(output, /open-intelligent-ui\/renderer|websandbox|iframe|lazy\(/i);
  assert.match(output, /useLayoutEffect\(/);
  assert.match(output, /host\.shadowRoot \?\? host\.attachShadow\(\{ mode: 'open' \}\)/);
  assert.match(output, /normalizeOpenGenUIContent\(createMarketBriefReport\(brief, \{ demo: isMock, year \}\)\)/);
  assert.match(output, /content\.jsFunctions \|\| content\.jsExpressions\?\.length\) return null/, '有脚本就退回文字版');
  assert.match(output, /\}, \[html, css\]\);/, '只有内容变了才重写影子根');
  assert.doesNotMatch(card, /useMarketBriefPreview|marketBriefPreview/);
  assert.match(card, /const data = latestQ\.data;/);
  for (const gone of [
    'mocks/marketBriefServerArchive.ts',
    'mocks/marketBriefOpenUI.ts',
    'components/home/marketBriefPreview.ts',
    'components/open-intelligent-ui/renderer.tsx',
    'components/open-intelligent-ui/frame-content.ts',
    'components/open-intelligent-ui/websandbox-loader.ts',
  ]) {
    assert.equal(fs.existsSync(path.join(srcDir, gone)), false, `${gone} 已删除`);
  }
  const pkg = JSON.parse(fs.readFileSync(path.resolve(here, '..', 'package.json'), 'utf8'));
  assert.equal(pkg.dependencies?.['@jetbrains/websandbox'], undefined, 'iframe 渲染器的依赖已移除');
  const content = read('components/home/MarketBriefContent.tsx');
  assert.match(content, /<MarketBriefModelOutput brief=\{brief\} year=\{year\}>/);
});

test('后端可能给出的图表标签（指数短名、主题名、宏观分项名）都有英日译文', async () => {
  const repo = path.resolve(here, '..', '..');
  const readRepo = (rel) => fs.readFileSync(path.join(repo, rel), 'utf8');
  const labels = new Set();
  for (const [, label] of readRepo('backend/app/services/market_brief/visuals.py').matchAll(/"idx:\^[A-Z]+": "([^"]+)"/g)) labels.add(label);
  for (const [, label] of readRepo('backend/app/services/sectors.py').matchAll(/"name": "([^"]+)"/g)) labels.add(label);
  const prose = /_PROSE_SAFE_THEME_NAMES[^=]*= \{([^}]*)\}/.exec(readRepo('backend/app/services/market_brief/evidence.py'));
  for (const [, label] of (prose?.[1] ?? '').matchAll(/"[a-z_]+": "([^"]+)"/g)) labels.add(label);
  for (const [, label] of readRepo('backend/app/services/macro_conditions/registry.py').matchAll(/ModuleSpec\("[a-z_]+", "([^"]+)"/g)) labels.add(label);
  for (const expected of ['标普500', '罗素2000', '半导体', '中概股', '外部冲击']) assert.ok(labels.has(expected), `没读到 ${expected}`);
  const { DICT } = await import(new URL('../src/i18n/dict/index.ts', import.meta.url).href);
  const missing = [...labels].filter((label) => !DICT[label]?.[0] || !DICT[label]?.[1]);
  assert.deepEqual(missing, []);
});
