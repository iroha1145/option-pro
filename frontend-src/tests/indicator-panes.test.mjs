import test from 'node:test';
import assert from 'node:assert/strict';
import { indicatorLayout, selectIndicatorPanes, formatIndicatorValue, indicatorCursorIndex } from '../src/components/detail/chart-indicators/layout.ts';

for (const narrow of [false, true]) {
  for (const count of [0, 1, 3, 6]) {
    test(`pixel panes retain independent heights (${count}, narrow=${narrow})`, () => {
      const layout = indicatorLayout(420, count, narrow);
      assert.equal(layout.grids.length, count + 2);
      assert.equal(layout.headers.length, count + 1);
      assert.equal(layout.grids[0].height, indicatorLayout(420, 0, narrow).grids[0].height);
      for (let i = 1; i < layout.grids.length; i++) {
        const prev = layout.grids[i - 1], grid = layout.grids[i], header = layout.headers[i - 1];
        assert.ok(header.top >= prev.top + prev.height + 16);
        assert.ok(grid.top >= header.top + header.height);
        assert.ok(grid.height >= (i === 1 ? 72 : 108));
        assert.equal(grid.left, layout.grids[0].left);
        assert.equal(grid.right, layout.grids[0].right);
      }
      const last = layout.grids.at(-1);
      assert.equal(layout.height - last.top - last.height, 36);
    });
  }
}
test('selection never mutates layer visibility, series values, or the input list', () => {
  const panes = Object.freeze([{ id: 'rsi' }, { id: 'macd' }, { id: 'obv' }].map(Object.freeze));
  assert.deepEqual(selectIndicatorPanes(panes, 'single', 'macd'), [panes[1]]);
  assert.equal(selectIndicatorPanes(panes, 'all', 'macd'), panes);
  assert.deepEqual(selectIndicatorPanes(panes, 'single', 'disabled'), [panes[0]]);
  assert.deepEqual(selectIndicatorPanes([], 'single', 'macd'), []);
});
for (const [value, output] of [[null,'—'],[NaN,'—'],[Infinity,'—'],[0,'0'],[-0,'0'],[100,'100'],[1.25,'1.25'],[-1.25,'−1.25'],[0.001234,'0.001234'],[0.000002,'2.00e-6'],[-3e9,'−3B'],[2.25e6,'2.25M']]) {
  test(`indicator value ${value} formats without lost scale`, () => assert.equal(formatIndicatorValue(value), output));
}
test('cursor selects the actual bar index, not another axis value', () => {
  assert.equal(indicatorCursorIndex({ axesInfo: [{ axisDim: 'y', value: 50 }, { axisDim: 'x', value: 7 }] }, 10), 7);
  for (const value of [-1, 10, 1.2, '3', NaN]) assert.equal(indicatorCursorIndex({ axesInfo: [{ axisDim: 'x', value }] }, 10), null);
  assert.equal(indicatorCursorIndex(null, 10), null);
});
test('invalid dimensions are bounded and finite', () => {
  const layout = indicatorLayout(NaN, Infinity);
  assert.ok(Number.isFinite(layout.height));
  assert.equal(layout.grids.length, 2);
  assert.equal(indicatorLayout(0, -1).grids.length, 2);
});
