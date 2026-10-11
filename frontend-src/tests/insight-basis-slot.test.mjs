// 大号读数（个股页头价格）的比较基准「较昨收」：涨跌徽标和涨跌额读到之前不渲染，读到后价格变宽、两者插到基准前面，
// 基准会被推走 240px 以上。所以 sm 以上读到之前基准占位但不可见，读到后在最终位置出现；手机上它独占一行，照常显示。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { cn } from '../src/lib/utils.ts';

const file = fileURLToPath(new URL('../src/components/shared/InsightCard.tsx', import.meta.url));
const jsx = (type, props, key) => ({ type, props, key });
const imports = {
  'react/jsx-runtime': { jsx, jsxs: jsx },
  '@/lib/utils': { cn },
  '@/lib/format': { fmtPrice: String },
  '@/components/shared/ChangeBadge': { default: 'ChangeBadge' },
  '@/lib/insightTone': { toneOf: () => 'flat' },
};

function loadInsightValue() {
  const module = { exports: {} };
  const { outputText } = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  vm.runInNewContext(outputText, {
    module, exports: module.exports,
    require(id) {
      if (Object.hasOwn(imports, id)) return imports[id];
      throw new Error(`Unexpected import ${id}`);
    },
  });
  return module.exports.InsightValue;
}

function find(node, match) {
  if (!node || typeof node !== 'object') return null;
  if (Array.isArray(node)) return node.map((child) => find(child, match)).find(Boolean) ?? null;
  if (match(node)) return node;
  return find(node.props?.children, match);
}

const InsightValue = loadInsightValue();
const basisClass = (props) => find(InsightValue({ value: '$1', basis: '较昨收', ...props }), (node) => node.type === 'span' && node.props.children === '较昨收').props.className;

test('the large reading keeps its basis out of sight until a change figure exists, from sm up', () => {
  // The header passes undefined while loading (no badge is rendered), and null for the amount.
  for (const props of [{ size: 'xl' }, { size: 'xl', changePct: undefined, change: null }, { size: 'xl', change: undefined }]) {
    const className = basisClass(props);
    assert.match(className, /(^|\s)sm:invisible(\s|$)/); // keeps its place in the row, not painted
    assert.doesNotMatch(className, /(^|\s)invisible/); // phones: shown as before
    assert.match(className, /\bmax-sm:basis-full\b/); // phones: still on its own row
  }
});

test('the basis appears with the change figures, including a loaded reading whose change is missing', () => {
  // A badge showing "—" for a missing change is a final state, the basis stays beside it as before.
  for (const props of [{ changePct: 1.2 }, { changePct: null }, { change: 2.3 }, { changePct: null, change: -0.5 }, { changePct: 0, change: 0 }]) {
    assert.doesNotMatch(basisClass({ size: 'xl', ...props }), /invisible/);
  }
});

test('the medium reading never hides its basis', () => {
  for (const props of [{}, { changePct: undefined }, { changePct: 1.2 }]) {
    const className = basisClass(props);
    assert.doesNotMatch(className, /invisible/);
    assert.doesNotMatch(className, /max-sm:basis-full/);
  }
});
