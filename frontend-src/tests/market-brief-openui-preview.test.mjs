import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

function load(relative) {
  const source = fs.readFileSync(new URL(relative, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', compiled)(module, module.exports, id => {
    throw new Error(`The preview must be self-contained: ${id}`);
  });
  return module.exports;
}

const { createMarketBriefOpenUIPreview } = load('../src/mocks/marketBriefOpenUI.ts');
const { normalizeOpenGenUIContent } = load('../src/components/open-intelligent-ui/schema.ts');
const { SERVER_BRIEF_ARCHIVE } = load('../src/mocks/marketBriefServerArchive.ts');
const fixture = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/market_brief_sample.json', import.meta.url), 'utf8'));

test('existing report fixture produces a completed, self-contained, labeled OpenIntelligentUI output', () => {
  const content = createMarketBriefOpenUIPreview(fixture.brief);
  assert.ok(normalizeOpenGenUIContent(content));
  const html = content.html.join('');
  assert.match(html, /图表数值与事件均为模拟数据/);
  assert.match(html, /0\.00%/);
  assert.match(html, /-3\.70/);
  assert.doesNotMatch(html, /-3\.70%/);
  assert.match(html, /超额收益（百分点）/);
  assert.match(html, /6\/11/);
  assert.match(html, /54\.55%/);
  assert.match(html, /行业隐含波动率数据缺失/);
  assert.doesNotMatch(html, /<(script|iframe|form)\b|\b(src|href)=/i);
  assert.equal(content.jsFunctions, '');
  assert.deepEqual(content.jsExpressions, []);
  assert.doesNotMatch(content.css, /@import|url\(/i);
});

test('server presentation keeps actual values, independent dates and original prose', () => {
  for (const item of SERVER_BRIEF_ARCHIVE) {
    const raw = item.response.brief;
    const brief = { ...fixture.brief, result: raw.result, tradingDate: raw.trading_date, generatedAt: raw.generated_at };
    const html = createMarketBriefOpenUIPreview(brief, item.visuals).html.join('');
    assert.match(html, /服务器研判存档/);
    assert.doesNotMatch(html, /模拟数据|协调世界时/);
    assert.match(html, /3\/11/);
    assert.match(html, /27\.27%/);
    assert.ok(html.includes(raw.result.headline));
    assert.ok(html.includes(raw.result.internals.summary));
    assert.ok(html.includes(raw.result.macro_check.summary));
    for (const row of item.visuals.market.rows) assert.ok(html.includes(`${row.value.toFixed(2)}%`));
    if (item.runId === 'mb_20261008_post_close_18b187a8') {
      assert.match(html, /研判交易日 2026-10-08 · 生成于 2026-10-09 06:08（纽约时间）/);
      assert.match(html, /证据时点：2026-10-09 05:56（纽约时间）/);
      assert.match(html, /主题数据交易日：2026-10-08/);
      assert.match(html, /数据截至：2026-10-02/);
    }
  }
});

test('missing server chart evidence stays absent instead of using demonstration values', () => {
  const visuals = { source: 'server', sourceLabel: '服务器研判存档', market: null, sectors: null, macro: null, breadth: null, notes: [] };
  const html = createMarketBriefOpenUIPreview(fixture.brief, visuals).html.join('');
  assert.doesNotMatch(html, /<figure>|class="breadth"|演示数据|72\.0分/);
  assert.ok(html.includes(fixture.brief.result.internals.summary));
});

test('model prose is escaped before insertion into the local preview HTML', () => {
  const brief = structuredClone(fixture.brief);
  const malicious = '</p><script>window.top.location="https://example.com"</script><p>';
  brief.result.headline = malicious;
  brief.result.internals.summary = malicious;
  brief.result.key_news[0].what_is_new = malicious;
  const html = createMarketBriefOpenUIPreview(brief).html.join('');
  assert.doesNotMatch(html, /<script>/i);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&quot;https:\/\/example\.com&quot;/);
});
