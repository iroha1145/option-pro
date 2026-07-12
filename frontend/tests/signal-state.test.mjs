import assert from 'node:assert/strict';
import test from 'node:test';

import {
  interpretSignal,
  renderScoreGauge,
} from '../static/js/components/topBottomSignals.js';

test('missing technical scores remain missing instead of becoming a false neutral or zero', () => {
  const html = renderScoreGauge('顶部风险', null, null, null, 'risk');
  assert.match(html, /data-numeric>—</);
  assert.match(html, /顶部风险数据不足/);
  assert.doesNotMatch(html, />0<small>\/100/);
  assert.doesNotMatch(html, /value="0"/);
});

test('a real zero score remains a valid finite result', () => {
  const html = renderScoreGauge('顶部风险', 0, '风险较低', null, 'risk');
  assert.match(html, />0<small>\/100/);
  assert.match(html, /value="0"/);
  assert.match(html, /风险较低/);
});

test('missing technical detail values never acquire an interpretation through Number coercion', () => {
  assert.equal(interpretSignal('rsi14', { value: null }), '数据不足');
  assert.equal(interpretSignal('sma20_dist', { value: undefined }), '数据不足');
  assert.equal(interpretSignal('macd_hist', { value: '' }), '数据不足');
  assert.equal(interpretSignal('rsi14', { value: Number.NaN }), '数据不足');
  assert.equal(interpretSignal('rsi14', { value: 0 }), '超卖');
});
