import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { createReactStub } from './helpers/react-hooks.mjs';

const source = readFileSync(new URL('../src/components/charts/useChartCursor.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
} }).outputText;

const points = (count, start = 0) => Array.from({ length: count }, (_, i) => ({
  date: new Date(Date.UTC(2026, 0, start + i + 1)).toISOString().slice(0, 10),
  score: i === 1 ? null : i,
}));

function harness(initial = points(120)) {
  const react = createReactStub();
  const module = { exports: {} };
  new Function('exports', 'require', code)(module.exports, (id) => {
    if (id === 'react') return react.React;
    throw new Error(`Unexpected import: ${id}`);
  });
  let data = initial;
  const read = react.mount(() => {
    const result = module.exports.useChartCursor(data.map((p) => p.date), (i) => `${data[i].date}: ${data[i].score ?? '—'}`);
    // This is the same render-time lookup used by both chart headers.
    return { ...result, point: data[result.index ?? data.length - 1] };
  });
  const actions = [];
  const events = {};
  let disposed = false;
  read().onInit({
    on(name, listener) { events[name] = listener; },
    getZr() { return { on(name, listener) { events[name] = listener; } }; },
    isDisposed() { return disposed; },
    convertToPixel(_finder, index) { return index * 10; },
    getHeight() { return 200; },
    dispatchAction(action) { actions.push(action); },
  });
  read().prepareOption({});
  return { read, actions,
    replace(next, commit = true) {
      data = next;
      react.rerender();
      if (commit) {
        read().prepareOption({});
        read().onOptionApplied();
      }
    },
    point(index) { events.updateAxisPointer({ axesInfo: [{ value: index }] }); },
    leave() { events.globalout(); },
    key(key) { read().sliderProps.onKeyDown({ key, preventDefault() {} }); },
    dispose() { disposed = true; },
  };
}

test('events from the old chart cannot select a date before the new option is committed', () => {
  const h = harness(points(5));
  h.point(2);
  h.replace(points(5, 10), false);
  h.point(2);
  assert.equal(h.read().index, null);
  assert.equal(h.read().point.date, '2026-01-15');
  h.read().prepareOption({});
  h.point(2);
  assert.equal(h.read().index, 2);
  assert.equal(h.read().point.date, '2026-01-13');
});

test('a 120-point cursor at 100 is safe during the render that receives 30 points', () => {
  const h = harness();
  h.point(100);
  assert.equal(h.read().index, 100);
  h.replace(points(30, 90));
  assert.equal(h.read().index, null);
  assert.equal(h.read().point.date, points(30, 90).at(-1).date);
  assert.equal(h.read().sliderProps['aria-valuenow'], 29);
  assert.match(h.read().sliderProps['aria-valuetext'], /2026-04-30/);
  assert.deepEqual(h.actions.slice(-2), [{ type: 'updateAxisPointer', currTrigger: 'leave' }, { type: 'hideTip' }]);
});

test('same-length replacement resets the date, while refreshed scores preserve the selected date', () => {
  const h = harness(points(5));
  h.point(2);
  h.replace(points(5).map((p) => ({ ...p, score: 80 })));
  assert.equal(h.read().index, 2);
  assert.equal(h.read().point.date, '2026-01-03');
  assert.match(h.read().sliderProps['aria-valuetext'], /80/);
  h.replace(points(5, 10));
  assert.equal(h.read().index, null);
  assert.equal(h.read().point.date, '2026-01-15');
  assert.equal(h.read().sliderProps['aria-valuenow'], 4);
});

test('Escape and blur release the axis pointer and highlight as well as the tooltip', () => {
  const h = harness(points(5));
  for (const release of [() => h.key('Escape'), () => h.read().sliderProps.onBlur()]) {
    h.key('Home');
    assert.equal(h.read().index, 0);
    release();
    assert.equal(h.read().index, null);
    assert.equal(h.read().sliderProps['aria-valuenow'], 4);
    assert.deepEqual(h.actions.slice(-2), [{ type: 'updateAxisPointer', currTrigger: 'leave' }, { type: 'hideTip' }]);
  }
});

test('missing scores, endpoint keys and pointer events keep their date semantics', () => {
  const h = harness(points(5));
  h.key('Home');
  h.key('ArrowLeft');
  assert.equal(h.read().index, 0);
  h.key('ArrowRight');
  assert.equal(h.read().sliderProps['aria-valuetext'], '2026-01-02: —');
  h.key('End');
  h.key('ArrowRight');
  assert.equal(h.read().index, 4);
  for (const invalid of [-1, 5, 1.5]) h.point(invalid);
  assert.equal(h.read().index, 4);
  h.leave();
  assert.equal(h.read().index, null);
  h.replace([]);
  assert.equal(h.read().sliderProps.tabIndex, -1);
  assert.equal(h.read().sliderProps['aria-valuetext'], undefined);
  h.replace(points(2));
  assert.equal(h.read().sliderProps['aria-valuenow'], 1);
  h.dispose();
  h.read().sliderProps.onBlur();
});
