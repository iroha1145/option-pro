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
  data.charts = [{ id: 'revenue', title: '收入', caption: '美元原值', kind: 'bar', unit: 'usd', rows: [{ label: '2026-06-30', value: 11536000000 }] }];
  const content = html(report.createStockAnalysisReport(result(), data));
  assert.match(content, /<b>115\.36亿<\/b>/);
  assert.match(content, /<td>11,536,000,000<\/td>/);
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
const quota = { limit: 1, used: 1, remaining: 0, period_start: '2026-10-09T20:00:00Z', reset_at: '2026-10-12T20:00:00Z' };
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
