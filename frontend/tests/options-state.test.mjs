import assert from 'node:assert/strict';
import test from 'node:test';

import {
  optionAlertMeta,
  renderAlerts,
  renderOptionChain,
} from '../static/js/components/optionChain.js';

test('option alerts never turn legacy call or put fields into a market direction', () => {
  const meta = optionAlertMeta({
    type: 'call',
    inferred_direction: 'bullish',
    signal: 'bullish',
  });
  assert.deepEqual(meta, {
    icon: 'help',
    tone: 'neutral',
    label: '方向无法判断',
  });

  const html = renderAlerts([{
    type: 'call',
    strike: 200,
    inferred_direction: 'bullish',
    signal: 'bullish',
  }]);
  assert.match(html, /看涨合约/);
  assert.match(html, /方向无法判断/);
  assert.match(html, /合约类型不代表交易方向/);
  assert.doesNotMatch(html, /方向推断偏多|方向推断偏空/);
});

test('option-chain methodology does not describe missing trade-side data as an inference', () => {
  const html = renderOptionChain({
    underlying_price: 100,
    calls: [{ strike: 105, volume: 10, open_interest: 20 }],
    puts: [{ strike: 95, volume: 8, open_interest: 15 }],
  });
  assert.match(html, /合约类型和虚实值不代表交易方向/);
  assert.doesNotMatch(html, /方向.*推断/);
});
