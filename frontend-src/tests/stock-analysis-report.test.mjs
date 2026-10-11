import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source = (file) => fs.readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
function load(file, imports = {}) {
  const js = ts.transpileModule(source(file), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', js)(module, module.exports, id => {
    if (id in imports) return imports[id];
    if (id.includes('i18n/core')) return { localeTag: () => 'zh-CN', t: (key, params = {}) => key.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? `{${name}}`)) };
    throw new Error(`Unexpected import ${id}`);
  });
  return module.exports;
}
const report = load('components/detail/stockReportTemplate.ts');
const schema = load('components/open-intelligent-ui/schema.ts');
const evidence = load('api/evidenceSources.ts');
const live = load('api/live.ts');
const normalize = load('api/aiJobNormalize.ts', { './live.ts': live, './live': live, './evidenceSources.ts': evidence, './client.ts': { ApiError: class extends Error {} } });
const view = load('components/detail/stockAnalysisTypes.ts', { '../../api/aiJobNormalize.ts': normalize });
const result = () => ({ summary: '原始摘要', asset: 'AAPL', report: {
  version: 'stock-report-v1', headline: '公司研究',
  sections: ['company', 'financials', 'valuation', 'technical', 'options', 'events', 'market'].map(id => ({ id, title: `${id} title`, summary: `${id} text`, points: [`${id} point`], evidence_ids: [`${id}:1`], source_urls: ['javascript:alert(1)'] })),
  watch_items: [{ what: '观察事项', why: '原因', revise_if: '反证条件' }], data_gaps: ['缺口'] },
  key_levels: { support: ['旧支撑依据'], resistance: ['旧阻力依据'] }, options_flow_read: { net_direction: 'unknown', confidence: 22, warnings: ['主动方未知'] }, trend_bias_confidence: 63, invalidation_signals: ['旧失效条件'], contradictions: ['旧反证'],
});
const visuals = () => ({ version: 'stock-report-visuals-v1', as_of: '2026-10-09T20:00:00Z', charts: [{ id: 'price', title: '价格', caption: 'USD · 2026-10-08—2026-10-09', kind: 'line', unit: 'usd', rows: [{ label: '2026-10-08', value: 20 }, { label: '2026-10-09', value: 21.1 }] }], coverage: [{ key: 'finance', label: '财务', status: 'missing' }], notes: ['未补造数据'] });
const html = (data) => data.html.join('');
test('large chart labels fit narrow cards while the source table retains original amounts', () => {
  const data = visuals();
  data.charts = [{ id: 'quarterly_revenue', title: '季度收入', caption: '来源：雅虎财经（Yahoo Finance）；资料截至：2026-06-30；财报期末数据', kind: 'bar', unit: 'usd', rows: [{ label: '2026-06-30', value: 11536000000 }] }];
  const content = html(report.createStockAnalysisReport(result(), data));
  assert.match(content, /<b>115\.36亿<\/b>/);
  assert.match(content, /<td>11,536,000,000<\/td>/);
});
test('chart units are visible in the heading without opening source details or relying on captions', () => {
  for (const [id, unit, label] of [['quarterly_revenue', 'usd', '美元'], ['analyst_targets', 'usd', '美元'], ['daily_volume', 'shares', '股'], ['structure_scores', 'score', '分'], ['earnings_surprise', 'ratio', '比例']]) {
    const data = visuals();
    data.charts = [{ id, title: '图表', caption: '来源：已保存的市场数据；资料截至：2026-06-30', kind: 'bar', unit, rows: [{ label: '2026-06-30', value: 10 }] }];
    const content = html(report.createStockAnalysisReport(result(), data));
    const heading = content.match(/<figcaption>(.*?)<\/figcaption>/s)?.[1];
    assert.ok(heading?.includes(`<strong>图表 · ${label}</strong>`), `${id} must show its unit outside collapsed details`);
    assert.match(content, /<b>10<\/b>/);
    assert.match(content, /<td>10<\/td>/);
  }
});
test('source times show the New York instant without changing reporting dates or prose', () => {
  assert.equal(report.evidenceTimeLabel('财报期 2026-06-30'), '财报期 2026-06-30');
  assert.match(report.evidenceTimeLabel('2026-10-10T21:00:00Z'), /2026\/10\/10 17:00 纽约时间/);
  const data = result(); data.report.sections[0].summary = '原文资料为2026-10-10T21:00:00Z。';
  assert.match(html(report.createStockAnalysisReport(data, visuals())), /原文资料为2026-10-10T21:00:00Z。/);
});
test('complete seven-section report preserves prose, evidence, risks and old technical fields', () => {
  const content = report.createStockAnalysisReport(result(), visuals());
  assert.ok(schema.normalizeOpenGenUIContent(content));
  for (const id of ['company','financials','valuation','technical','options','events','market']) assert.match(html(content), new RegExp(`${id} text`));
  for (const text of ['原始摘要','反证条件','缺口','旧支撑依据','主动方未知','旧失效条件','旧反证','63/100','22/100']) assert.ok(html(content).includes(text));
  assert.match(html(content), /21\.1/);
  assert.match(html(content), /<svg/);
  assert.equal(content.jsFunctions, ''); assert.deepEqual(content.jsExpressions, []);
  assert.doesNotMatch(html(content), /javascript:/);
});
test('malicious model prose and chart labels are escaped, supplied executable content is ignored', () => {
  const attack = '<img src=x onerror="alert(1)"><script>fetch("https://evil.test")</script>';
  const row = result(); row.report.headline = attack; row.report.sections[0].summary = attack;
  row.html = ['<iframe src="https://evil.test"></iframe>']; row.jsFunctions = 'alert(1)';
  const data = visuals(); data.charts[0].title = attack; data.charts[0].rows[0].label = attack;
  const content = report.createStockAnalysisReport(row, data);
  assert.doesNotMatch(html(content), /<img|<script|<iframe|onerror="/);
  assert.match(html(content), /&lt;script&gt;/);
  assert.equal(content.jsFunctions, ''); assert.deepEqual(content.jsExpressions, []);
});
test('absent or corrupt visuals never invent numeric charts; legacy summary stays readable', () => {
  const content = report.createStockAnalysisReport({ summary: '旧结果', key_levels: { support: ['旧支撑'] } }, undefined);
  assert.match(html(content), /旧结果/); assert.match(html(content), /旧支撑/); assert.doesNotMatch(html(content), /<svg|<figure/);
  assert.equal(report.createStockAnalysisReport({}, visuals()), null);
  const data = visuals(); data.charts[0].unit = 'invalid';
  assert.deepEqual(report.parseStockVisuals(data).charts, []);
  data.charts[0].unit = 'usd'; data.charts[0].rows = [{ label: 'x', value: Infinity }, { label: 'y', value: NaN }];
  assert.deepEqual(report.parseStockVisuals(data).charts, []);
  data.as_of = 'invalid'; assert.equal(report.parseStockVisuals(data), null);
});
test('visuals enforce finite values, known units and bounded line/bar payloads', () => {
  const data = visuals(); data.charts = Array.from({ length: 9 }, (_, i) => ({ id: `c${i}`, title: 'title', caption: 'caption', kind: i % 2 ? 'bar' : 'line', unit: 'count', rows: Array.from({ length: 280 }, (_, j) => ({ label: `p${j}`, value: j })) }));
  const parsed = report.parseStockVisuals(data);
  assert.equal(parsed.charts.length, 6); assert.equal(parsed.charts[0].rows.length, 250); assert.equal(parsed.charts[1].rows.length, 16);
  data.charts = [data.charts[0], data.charts[0]]; assert.equal(report.parseStockVisuals(data).charts.length, 1);
});
const quota = { limit: 10, used: 10, remaining: 0, period_start: '2026-10-09T20:00:00Z', reset_at: '2026-10-12T20:00:00Z' };
const saved = { job_id: 'job-saved', job_type: 'signal_analysis', status: 'completed', result: result(), visuals: visuals() };
test('exhausted quota retains shared completed report and activity; invalid ticker/version/quota fail closed', () => {
  const body = { schema_version: 'stock-analysis-view-v1', ticker: 'AAPL', enabled: true, can_generate: false, quota, latest_job: saved, active_job: { ...saved, job_id: 'job-active', status: 'running', result: null } };
  const parsed = view.parseStockAnalysisView(body, 'AAPL');
  assert.equal(parsed.quota.remaining, 0); assert.equal(parsed.canGenerate, false); assert.equal(parsed.latestJob.status, 'succeeded'); assert.equal(parsed.activeJob.status, 'in_progress');
  assert.deepEqual(parsed.latestJob.visuals, visuals());
  assert.equal(view.parseStockAnalysisView(body, 'MSFT'), null);
  assert.equal(view.parseStockAnalysisView({ ...body, schema_version: 'bad' }, 'AAPL'), null);
  const bad = view.parseStockAnalysisView({ ...body, can_generate: true, quota: { ...quota, remaining: 1 } }, 'AAPL');
  assert.equal(bad.canGenerate, false); assert.equal(bad.quota, null); assert.ok(bad.latestJob);
  const wrongAsset = view.parseStockAnalysisView({ ...body, latest_job: { ...saved, result: { asset: 'MSFT', summary: 'wrong' } } }, 'AAPL');
  assert.equal(wrongAsset.latestJob, null);
});
test('ten-use quota accepts every valid remaining count and rejects corrupt counters', () => {
  const body = { schema_version: 'stock-analysis-view-v1', ticker: 'AAPL', enabled: true, can_generate: true, latest_job: saved, latest_job_cached: false, cache_reset_at: quota.reset_at };
  for (let used = 0; used <= 10; used++) {
    const parsed = view.parseStockAnalysisView({ ...body, quota: { ...quota, used, remaining: 10 - used } }, 'AAPL');
    assert.equal(parsed.quota.used, used);
    assert.equal(parsed.quota.remaining, 10 - used);
    assert.equal(parsed.canGenerate, used < 10);
    assert.equal(parsed.cacheResetAt, quota.reset_at);
  }
  for (const invalid of [
    { limit: 1, used: 0, remaining: 1 }, { limit: 10, used: -1, remaining: 11 },
    { limit: 10, used: 11, remaining: -1 }, { limit: 10, used: 1.5, remaining: 8.5 },
    { limit: 10, used: 5, remaining: 6 }, { limit: 10, used: '0', remaining: 10 },
  ]) {
    const parsed = view.parseStockAnalysisView({ ...body, quota: { ...quota, ...invalid } }, 'AAPL');
    assert.equal(parsed.quota, null); assert.equal(parsed.canGenerate, false); assert.equal(parsed.cacheResetAt, null);
    assert.ok(parsed.latestJob, 'corrupt quota must not hide a valid historical report');
  }
});
test('only a confirmed current report with the shared reset timestamp is shown as cached', () => {
  const body = { schema_version: 'stock-analysis-view-v1', ticker: 'AAPL', enabled: true, can_generate: false, quota, latest_job: saved, latest_job_cached: true, cache_reset_at: quota.reset_at };
  assert.equal(view.parseStockAnalysisView(body, 'AAPL').latestJobCached, true);
  for (const changed of [{ latest_job_cached: false }, { cache_reset_at: '2026-10-13T20:00:00Z' }, { latest_job: null }]) {
    assert.equal(view.parseStockAnalysisView({ ...body, ...changed }, 'AAPL').latestJobCached, false);
  }
});

test('absolute 0–100 score bars draw 10 and 20 as ten and twenty percent, never full width', () => {
  const data = visuals();
  data.charts = [{ id: 'structure-score', title: '结构评分', caption: '0–100 分', kind: 'bar', unit: 'score', rows: [{ label: '趋势', value: 10 }, { label: '动量', value: 20 }] }];
  const content = report.createStockAnalysisReport(result(), data);
  const bars = [...html(content).matchAll(/<i style="left:([\d.]+)%;width:([\d.]+)%"/g)];
  assert.deepEqual(bars.map(match => [Number(match[1]), Number(match[2])]), [[0, 10], [0, 20]]);
  assert.doesNotMatch(html(content), /width:100\.000%/);
  for (const bad of [-1, 101, Infinity, NaN]) {
    data.charts[0].rows[1].value = bad;
    assert.deepEqual(report.parseStockVisuals(data).charts, [], `invalid score ${bad} must not be drawn`);
    assert.doesNotMatch(html(report.createStockAnalysisReport(result(), data)), /<figure/);
  }
});
test('score line charts also keep the absolute scale', () => {
  const data = visuals();
  data.charts[0] = { id: 'scores', title: '评分历史', caption: '0–100 分', kind: 'line', unit: 'score', rows: [{ label: '第一日', value: 10 }, { label: '第二日', value: 20 }] };
  const content = report.createStockAnalysisReport(result(), data);
  assert.match(html(content), /points="30\.00,138\.00 570\.00,126\.00"/);
  assert.match(html(content), /y="18">100<\/text>/);
});
test('risk and opportunity colors follow their own score bands and retain readable meanings', () => {
  const cases = [
    ['top_score', 29, 'positive', '顶部风险低'], ['top_score', 30, 'caution', '正常震荡风险'],
    ['top_score', 50, 'caution', '需要停止追高'], ['top_score', 65, 'negative', '阶段性顶部风险高'],
    ['top_score', 80, 'negative', '极端过热，等待反转确认'],
    ['bottom_score', 29, 'neutral', '没有底部迹象'], ['bottom_score', 30, 'caution', '可能只是超跌'],
    ['bottom_score', 50, 'caution', '开始出现底部条件'], ['bottom_score', 65, 'positive', '阶段性底部概率较高'],
    ['bottom_score', 80, 'caution', '恐慌释放充分，仍需价格确认'],
    ['dip_buy_quality', 29, 'negative', '回调买点弱'], ['dip_buy_quality', 30, 'caution', '回调承接一般'],
    ['dip_buy_quality', 50, 'caution', '趋势回调买点中等'], ['dip_buy_quality', 70, 'positive', '高质量趋势回调'],
    ['data_quality', 100, 'price', '综合资料质量'],
  ];
  for (const [key, value, tone, meaning] of cases) {
    const data = visuals();
    data.charts = [{ id: 'structure_scores', title: '评分', caption: '0–100 分', kind: 'bar', unit: 'score', rows: [{ label: 'Translated label', evidence_id: `scores.${key}`, value }] }];
    const output = html(report.createStockAnalysisReport(result(), data));
    assert.match(output, new RegExp(`class="bar-row tone-${tone} has-meaning"`), `${key} ${value}`);
    assert.ok(output.includes(`<small>${meaning}</small>`));
  }
  const data = visuals();
  data.charts = [{ id: 'structure_scores', title: '评分', caption: '0–100 分', kind: 'bar', unit: 'score', rows: [{ label: '顶部结构分数', evidence_id: 'unrecognized', value: 90 }] }];
  assert.match(html(report.createStockAnalysisReport(result(), data)), /class="bar-row tone-neutral"/);
});
test('chart categories stay neutral to magnitude, while earnings surprise distinguishes both directions and zero', () => {
  const categories = { price_history: 'price', quarterly_revenue: 'revenue', daily_volume: 'volume', analyst_targets: 'estimate' };
  for (const [id, tone] of Object.entries(categories)) {
    const data = visuals();
    data.charts = [{ id, title: 'title', caption: 'source', kind: 'bar', unit: 'usd', rows: [{ label: 'negative', value: -10 }, { label: 'positive', value: 100 }] }];
    const output = html(report.createStockAnalysisReport(result(), data));
    assert.match(output, new RegExp(`<figure class="tone-${tone}">`));
    assert.equal([...output.matchAll(new RegExp(`class="bar-row tone-${tone}"`, 'g'))].length, 2);
    assert.doesNotMatch(output, /has-meaning/);
  }
  const data = visuals();
  data.charts = [{ id: 'earnings_surprise', title: '盈利超预期比例', caption: 'ratio', kind: 'bar', unit: 'ratio', rows: [{ label: 'below', value: -0.1 }, { label: 'above', value: 0.1 }, { label: 'equal', value: 0 }] }];
  const output = html(report.createStockAnalysisReport(result(), data));
  assert.match(output, /tone-negative has-meaning"><span class="bar-label">below<small>低于预期/);
  assert.match(output, /tone-positive has-meaning"><span class="bar-label">above<small>超出预期/);
  assert.match(output, /tone-neutral has-meaning"><span class="bar-label">equal<small>符合预期/);
});
test('every backend coverage status is readable and unknown internal values are hidden', () => {
  const statuses = { available: '可用', ok: '可用', missing: '缺失', stale: '已过期', partial: '部分资料', unavailable: '暂无资料', timeout: '资料读取超时', not_applicable: '不适用', omitted_size: '因资料过长已省略' };
  for (const [status, expected] of Object.entries(statuses)) assert.equal(report.coverageStatusLabel(status), expected);
  assert.equal(report.coverageStatusLabel('__proto__'), '状态未知');
  const data = visuals();
  data.coverage = [...Object.keys(statuses), 'internal-error-detail'].map(status => ({ key: status, label: '资料项目', status }));
  const output = html(report.createStockAnalysisReport(result(), data));
  for (const expected of Object.values(statuses)) assert.ok(output.includes(expected));
  assert.match(output, /状态未知/); assert.doesNotMatch(output, /internal-error-detail|not_applicable|omitted_size/);
});
