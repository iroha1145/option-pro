import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(path.join(here, '../src/mocks/marketBriefServerArchive.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const module = { exports: {} };
new Function('module', 'exports', 'require', compiled)(module, module.exports, id => { throw new Error(`Unexpected runtime import: ${id}`); });
const { SERVER_BRIEF_ARCHIVE: archive, SOURCE_COMMIT, SERVER_BRIEF_ARCHIVE_FETCHED_AT } = module.exports;

test('four completed server reports retain their public identity and source revision', () => {
  assert.deepEqual(archive.map(item => item.runId), [
    'mb_20261009_post_close_dace9ea4', 'mb_20261009_pre_open_a86cbde1',
    'mb_20261008_post_close_18b187a8', 'mb_20261008_pre_open_93d7e377',
  ]);
  assert.deepEqual(archive.map(item => item.label), ['10-09 收盘后', '10-09 开盘前', '10-08 收盘后', '10-08 开盘前']);
  assert.equal(SOURCE_COMMIT, '858707ed984b8b32e21cfcf191abf39ef000444b');
  assert.equal(SERVER_BRIEF_ARCHIVE_FETCHED_AT, '2026-10-10T16:22:42.015053+00:00');
  for (const item of archive) {
    assert.equal(item.response.status, 'ok');
    assert.equal(item.response.schema_version, 'market-brief-v1');
    assert.equal(item.response.brief.run_id, item.runId);
    assert.equal(item.response.snapshot_saved_at, item.response.brief.generated_at);
    assert.equal(item.response.latest_attempt, null);
    assert.equal(item.response.next_slot, null);
    assert.equal(item.response.brief.web_search_count, null);
    assert.equal(item.visuals.source, 'server');
    assert.equal(item.visuals.sourceLabel, '服务器研判存档 · 图表来自该次证据快照');
  }
});

test('charts contain actual US index, cited theme and macro values without demo fallback', () => {
  const expectedIndices = [[0.59, 0.64, 0.83], [-0.47, -1.25, 0.1], [-0.47, -1.25, 0.1], [-0.22, -0.22, -0.66]];
  const expectedThemes = [
    [-18.35, 10.68, 4.19, 4.48, -4.78, 0.34, -2.8],
    [6.75, 5.87, -10.52, -1.27, -4.97, -6.95, -11.91],
    [6.75, 5.87, -10.52, 0.48, -6.95, -4.97],
    [6.99, 5.7, 4.69, -12.34, -8.01, -5, -2.97],
  ];
  archive.forEach((item, index) => {
    const { market, sectors, macro, breadth } = item.visuals;
    assert.deepEqual(market.rows.map(row => row.value), expectedIndices[index]);
    assert.deepEqual(market.rows.map(row => row.evidenceId), ['idx:^GSPC', 'idx:^IXIC', 'idx:^DJI']);
    assert.equal(market.unit, 'percent');
    assert.deepEqual(sectors.rows.map(row => row.value), expectedThemes[index]);
    assert.equal(sectors.unit, 'percentage_points');
    assert.ok(sectors.caption.includes('百分点'));
    assert.equal(sectors.rows.some(row => row.label === 'AI 与云'), false);
    const references = new Set(item.response.brief.result.sectors.flatMap(row => row.evidence_ids));
    for (const row of sectors.rows) assert.ok(references.has(row.evidenceId));
    assert.ok(sectors.rows.length <= 8);
    assert.equal(macro.unit, 'score');
    assert.ok(macro.rows.length <= 8);
    for (const row of macro.rows) assert.ok(Number.isFinite(row.value) && row.value >= 0 && row.value <= 100);
    assert.ok(macro.caption.includes(index === 3 ? '2026-09-30' : '2026-10-02'));
    assert.ok(item.visuals.notes.some(note => note.includes('早于报告交易日')));
    assert.deepEqual([breadth.above, breadth.total, breadth.expected], [3, 11, 11]);
    assert.equal((100 * breadth.above / breadth.total).toFixed(2), '27.27');
    assert.ok(item.visuals.notes.some(note => note.includes('隐含波动率未绘图')));
  });
  assert.deepEqual(archive[0].visuals.macro.rows.map(row => row.value), [40.9, 51.7, 72, 27.2, 34.6, 58.1, 62.4]);
});

test('each chart keeps its own evidence date, including the next-day manually generated report', () => {
  assert.ok(archive[0].visuals.market.caption.includes('2026-10-09T23:58:05Z'));
  assert.ok(archive[0].visuals.sectors.caption.includes('2026-10-09'));
  for (const index of [1, 2, 3]) assert.ok(archive[index].visuals.market.caption.includes('最近收盘快照（非实时）'));
  assert.ok(archive[1].visuals.sectors.caption.includes('2026-10-08'));
  const manual = archive[2];
  assert.equal(manual.response.brief.trading_date, '2026-10-08');
  assert.equal(manual.response.brief.generated_at, '2026-10-09T10:08:06.060443Z');
  assert.equal(manual.visuals.breadth.asOf, '2026-10-09T09:53:13Z');
  assert.ok(manual.visuals.market.caption.includes('2026-10-09T09:56:19Z'));
  assert.ok(manual.visuals.sectors.caption.includes('2026-10-08'));
  assert.ok(archive[3].visuals.sectors.caption.includes('2026-10-07'));
});

test('archive only retains public projection and chart evidence identifiers', () => {
  for (const item of archive) {
    assert.deepEqual(Object.keys(item.response.brief).sort(), [
      'run_id', 'trading_date', 'slot', 'trigger', 'generated_at', 'model', 'coverage',
      'result', 'external_sources', 'validation_warnings', 'web_search_count',
    ].sort());
    assert.deepEqual(Object.keys(item.response.brief.coverage).sort(), [
      'universe_size', 'scored_count', 'quotes_valid', 'breadth_basis', 'data_through', 'missing_blocks',
    ].sort());
    for (const external of item.response.brief.external_sources) assert.deepEqual(Object.keys(external).sort(), ['title', 'url']);
  }
  assert.equal(/raw_output_text|source_sha256|prompt_version|started_at|evidence_bytes|MARKET_BRIEF_SAMPLE|\bvia\b/.test(source), false);
});

const inputPath = path.join(here, '../output/openui-server/reports.json');
test('when the local source archive exists, every projected result and plotted number matches its run', { skip: !fs.existsSync(inputPath) }, () => {
  const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  for (const item of archive) {
    const record = input.reports.find(record => record.run_id === item.runId);
    assert.ok(record);
    assert.deepEqual(item.response.brief.result, record.result);
    assert.equal(item.response.brief.generated_at, record.completed_at);
    assert.deepEqual(item.response.brief.validation_warnings, record.validation_warnings);
    const indices = record.evidence.indices.rows.filter(row => ['^GSPC', '^IXIC', '^DJI', '^RUT'].includes(row.symbol) && finite(row.change_percent));
    assert.deepEqual(item.visuals.market.rows.map(row => row.value), indices.map(row => row.change_percent));
    assert.ok(item.visuals.market.caption.includes(record.evidence.indices.as_of));
    const themeIds = [...new Set(record.result.sectors.flatMap(row => row.evidence_ids).filter(id => id.startsWith('theme:')))];
    const themes = themeIds.map(id => record.evidence.themes.rows.find(row => row.id === id)).filter(row => row && finite(row.excess_vs_spy_1mo)).slice(0, 8);
    assert.deepEqual(item.visuals.sectors?.rows.map(row => row.value) ?? [], themes.map(row => row.excess_vs_spy_1mo));
    if (!themes.length) assert.equal(item.visuals.sectors, null, 'missing theme evidence must not produce demo values');
    const modules = record.evidence.macro.modules.filter(row => finite(row.score) && row.score >= 0 && row.score <= 100).slice(0, 8);
    assert.deepEqual(item.visuals.macro.rows.map(row => row.value), modules.map(row => row.score));
    assert.ok(item.visuals.macro.caption.includes(record.evidence.macro.data_through));
    assert.equal(item.visuals.breadth.asOf, record.evidence.internals.market_signals.as_of);
  }
});
