import test from 'node:test';
import assert from 'node:assert/strict';
import { CH, escapeTooltipText, insightDotRow, insightTooltipBody } from '../src/lib/chart.ts';

test('chart tooltip text preserves content without interpreting markup', () => {
  assert.equal(escapeTooltipText('涨跌 < 5% & "观察"'), '涨跌 &lt; 5% &amp; &quot;观察&quot;');
  assert.equal(escapeTooltipText("'"), '&#39;');
  assert.equal(escapeTooltipText(null), '');
  assert.equal(escapeTooltipText(undefined), '');
  assert.equal(escapeTooltipText(0), '0');
});

test('snapshot labels, values, dates and footnotes cannot create tooltip elements', () => {
  const hostile = '<img src=x onerror="alert(1)">';
  const row = insightDotRow(CH.brand500, hostile, hostile);
  const tooltip = insightTooltipBody(hostile, [row], hostile);
  assert.equal(tooltip.includes('<img'), false);
  assert.equal(tooltip.split('&lt;img').length - 1, 4);
  assert.equal(tooltip.includes('&amp;lt;'), false, 'safe row markup must not be double-escaped');
  assert.ok(tooltip.includes('<span style="font-weight:600">'));
  assert.ok(tooltip.includes(`background:${CH.brand500}`));
});

test('tooltip palette rejects attribute breaks and CSS URLs', () => {
  for (const color of ['red;position:fixed;inset:0', '"><img src=x>', 'url(https://example.invalid/pixel)']) {
    const row = insightDotRow(color, '仓位', '+15');
    assert.equal(row.includes(color), false);
    assert.ok(row.includes(`background:${CH.brand500};`));
  }
  assert.ok(insightDotRow('#aBcD12', '仓位', '+15').includes('background:#aBcD12;'));
});
