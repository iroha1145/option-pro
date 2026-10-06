import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { createReactStub } from './helpers/react-hooks.mjs';

const source = readFileSync(new URL('../src/components/charts/ReactECharts.tsx', import.meta.url), 'utf8');
const code = ts.transpileModule(source, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
} }).outputText;

test('cursor callback changes do not rebuild the chart; new options use the latest callback after commit', () => {
  const react = createReactStub();
  const module = { exports: {} };
  const commits = [];
  const readouts = [];
  let chartOption;
  const chart = {
    setOption(option) { chartOption = option; commits.push(option); },
    dispose() {},
    resize() {},
    isDisposed() { return false; },
  };
  new Function('exports', 'require', 'ResizeObserver', code)(module.exports, (id) => {
    if (id === 'react') return react.React;
    if (id === 'react/jsx-runtime') return {
      jsx(_type, props) { props.ref.current = {}; return props; },
    };
    if (id === '@/lib/chart') return { echarts: { init() { return chart; } } };
    throw new Error(`Unexpected import: ${id}`);
  }, class { observe() {} disconnect() {} });

  const firstOption = { xAxis: { data: ['2026-01-01', '2026-01-02'] } };
  let props = { option: firstOption, onOptionApplied: () => readouts.push({ chartOption, index: null }) };
  react.mount(() => module.exports.default(props));
  assert.deepEqual(readouts, [{ chartOption: firstOption, index: null }]);

  for (const index of [0, 1, null]) {
    props = { ...props, onOptionApplied: () => readouts.push({ chartOption, index }) };
    react.rerender();
  }
  assert.deepEqual(commits, [firstOption]);
  assert.equal(readouts.length, 1);

  const nextOption = { xAxis: { data: ['2026-02-01', '2026-02-02'] } };
  props = { option: nextOption, onOptionApplied: () => readouts.push({ chartOption, index: 1 }) };
  react.rerender();
  assert.deepEqual(commits, [firstOption, nextOption]);
  assert.deepEqual(readouts.at(-1), { chartOption: nextOption, index: 1 });
  react.unmount();
});
