/**
 * 新闻筛选条（2026-10-08 第二版）：影响方向、分析状态、置信度门槛、影响分门槛、多处报道收进「更多筛选」，
 * 折叠不能让已生效的条件消失——已选条件以徽标常显，「清空条件」与条数不随展开区收起。
 * 组件用 React 桩驱动真实源码，共享 UI 件桩成元素类型字符串，props 原样保留。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { createReactStub } from './helpers/react-hooks.mjs';
import * as filtersModule from '../src/components/catalysts/filters.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(here, '../src');

function stubT(msgid, vars) {
  return vars ? msgid.replace(/\{(\w+)\}/g, (whole, key) => (vars[key] === undefined ? whole : String(vars[key]))) : msgid;
}

function compile(rel, imports) {
  const source = fs.readFileSync(path.join(srcRoot, rel), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    console,
    require(id) {
      if (id in imports) return imports[id];
      throw new Error(`Unexpected import ${id} in ${rel}`);
    },
  });
  return module.exports;
}

/* 本文件内的小组件（滑杆、条数、状态下拉）不含 hook，直接展开，好断言它们画出的内容。 */
const invokingJsx = {
  jsx: (type, props) => (typeof type === 'function' ? type(props) : { type, props }),
  jsxs: (type, props) => (typeof type === 'function' ? type(props) : { type, props }),
  Fragment: 'Fragment',
};

function childNodes(node) {
  if (!node || typeof node !== 'object' || !node.props) return [];
  const out = [];
  for (const [key, value] of Object.entries(node.props)) {
    if (key === 'children') out.push(value);
    else if (value && typeof value === 'object' && (Array.isArray(value) || 'props' in value)) out.push(value);
  }
  return out;
}

function collectText(node, out = []) {
  if (node == null || node === false || node === true) return out;
  if (typeof node === 'string' || typeof node === 'number') {
    out.push(String(node));
    return out;
  }
  if (Array.isArray(node)) {
    for (const child of node) collectText(child, out);
    return out;
  }
  if (typeof node === 'object' && node.props) for (const child of childNodes(node)) collectText(child, out);
  return out;
}
const textOf = (node) => collectText(node).join(' ').replace(/\s+/g, ' ').trim();

function findAll(node, predicate, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (Array.isArray(node)) {
    for (const child of node) findAll(child, predicate, out);
    return out;
  }
  if (node.props && predicate(node)) out.push(node);
  for (const child of childNodes(node)) findAll(child, predicate, out);
  return out;
}
const findNode = (tree, predicate) => findAll(tree, predicate)[0] ?? null;
const findButton = (tree, label) => findNode(tree, (node) => node.type === 'button' && textOf(node).startsWith(label));

function harness(initial = {}) {
  const runner = createReactStub();
  runner.React.useId = () => 'more-panel';
  const prefetches = [];
  const changes = [];
  const FilterBar = compile('components/catalysts/FilterBar.tsx', {
    react: runner.React,
    'react/jsx-runtime': invokingJsx,
    'framer-motion': { motion: new Proxy({}, { get: (_, key) => `motion.${String(key)}` }) },
    '@/components/shared/Segmented': { default: 'Segmented' },
    '@/components/shared/MenuSelect': { default: 'MenuSelect' },
    '@/components/shared/SoftBadge': { default: 'SoftBadge' },
    '@/components/shared/Switch': { default: 'Switch' },
    '@/components/icons': { default: 'Icon' },
    '@/lib/utils': { cn: (...xs) => xs.filter(Boolean).join(' ') },
    '@/lib/motion': { SPRING_POP: {} },
    './api': { catalystsContract: { themeName: (id) => `主题 ${id}` } },
    './feedPrefetch': { prefetchDefaultFeed: (hours, current) => prefetches.push([hours, current]) },
    './filters': filtersModule,
    '../../i18n/core.ts': { t: stubT },
  }).default;
  let props = {
    filters: { ...filtersModule.DEFAULT_FILTERS, ...initial },
    onChange: (next) => changes.push(next),
    total: 12,
    filtered: false,
  };
  const read = runner.mount(() => FilterBar(props));
  return {
    tree: () => read(),
    changes,
    prefetches,
    render(next) {
      props = { ...props, ...next };
      runner.rerender();
    },
    unmount: () => runner.unmount(),
  };
}

const moreControlsRendered = (tree) => [
  findNode(tree, (node) => node.type === 'Segmented' && node.props.ariaLabel === '影响方向'),
  findNode(tree, (node) => node.type === 'MenuSelect'),
  ...findAll(tree, (node) => node.type === 'input' && node.props.type === 'range'),
  findNode(tree, (node) => node.type === 'Switch'),
].filter(Boolean);

test('默认状态：时间范围常显，其余条件收在「更多筛选」里，没有条件时不出现清空按钮', () => {
  const h = harness();
  const tree = h.tree();
  const toggle = findButton(tree, '更多筛选');
  assert.ok(toggle, '必须有「更多筛选」入口');
  assert.equal(toggle.props['aria-expanded'], false);
  assert.ok(findNode(tree, (node) => node.type === 'Segmented' && node.props.value === '72'), '时间范围分段常显');
  assert.ok(findNode(tree, (node) => node.type === 'input' && node.props['aria-label'] === '按股票代码筛选'), '股票代码输入框常显');
  assert.equal(moreControlsRendered(tree).length, 0, '收起时不挂载展开区的控件');
  assert.equal(findButton(tree, '清空条件'), null);
  assert.equal(findNode(tree, (node) => node.props?.['data-testid'] === 'catalyst-more-filters-summary'), null);
  h.unmount();
});

test('折叠不清除已生效的条件：已选条件以徽标常显，清空条件与条数常显', () => {
  const h = harness({
    classification: 'bullish',
    analysisStatus: 'completed',
    minConfidence: 0.5,
    minAbsImpact: 1.5,
    multiSourceOnly: true,
  });
  h.render({ filtered: true });
  const tree = h.tree();
  assert.equal(findButton(tree, '更多筛选').props['aria-expanded'], false);
  assert.equal(moreControlsRendered(tree).length, 0, '仍然是收起状态');
  const summary = findNode(tree, (node) => node.props?.['data-testid'] === 'catalyst-more-filters-summary');
  assert.ok(summary, '收起时必须有已选条件摘要');
  const badges = findAll(summary, (node) => node.type === 'SoftBadge').map(textOf);
  assert.deepEqual(badges, ['利多', '已分析', '置信度 ≥ 50%', '影响分 ≥ 1.5', '多处报道']);
  assert.ok(findButton(tree, '清空条件'), '清空条件常显');
  assert.match(textOf(tree), /12 条 · 已筛选/, '条数与「已筛选」提示常显');
  h.unmount();
});

test('只有股票代码或时间范围生效时，清空条件也出现，并把筛选条件整体还原', () => {
  for (const initial of [{ ticker: 'NVDA' }, { windowHours: 24 }, { themeId: 'rates' }]) {
    const h = harness(initial);
    const clear = findButton(h.tree(), '清空条件');
    assert.ok(clear, `${JSON.stringify(initial)} 需要清空条件`);
    clear.props.onClick();
    assert.deepEqual({ ...h.changes.at(-1) }, filtersModule.DEFAULT_FILTERS);
    h.unmount();
  }
});

test('展开后出现全部五项控件，改动经 onChange 写回；再收起摘要仍在', () => {
  const h = harness({ classification: 'bearish' });
  findButton(h.tree(), '更多筛选').props.onClick();
  let tree = h.tree();
  assert.equal(findButton(tree, '更多筛选').props['aria-expanded'], true);
  const direction = findNode(tree, (node) => node.type === 'Segmented' && node.props.ariaLabel === '影响方向');
  assert.ok(direction);
  assert.equal(direction.props.value, 'bearish');
  assert.deepEqual(Array.from(direction.props.options, (o) => o.label), ['全部', '利多', '利空', '中性']);
  assert.deepEqual(
    findAll(tree, (node) => node.type === 'input' && node.props.type === 'range').map((node) => node.props['aria-label']),
    ['置信度 ≥', '影响分 ≥'],
    '置信度与影响分两根滑杆',
  );
  assert.ok(findNode(tree, (node) => node.type === 'MenuSelect' && node.props.ariaLabel === '分析状态'));
  assert.ok(findNode(tree, (node) => node.type === 'Switch'));
  assert.match(textOf(tree), /多处报道/, '多处报道开关的标签');

  direction.props.onChange('bullish');
  assert.equal(h.changes.at(-1).classification, 'bullish');
  assert.equal(h.changes.at(-1).ticker, '', '其他条件原样保留');

  findButton(tree, '更多筛选').props.onClick();
  tree = h.tree();
  assert.equal(findButton(tree, '更多筛选').props['aria-expanded'], false);
  assert.ok(findNode(tree, (node) => node.props?.['data-testid'] === 'catalyst-more-filters-summary'), '收起后摘要仍在');
  h.unmount();
});

test('悬停或聚焦「24 时」仍预取默认列表，不再依赖手机端的整块折叠按钮', () => {
  const h = harness();
  const windowTabs = findNode(h.tree(), (node) => node.type === 'Segmented' && node.props.value === '72');
  windowTabs.props.onOptionIntent('24');
  assert.equal(h.prefetches.length, 1);
  assert.equal(h.prefetches[0][0], 24);
  windowTabs.props.onOptionIntent('168');
  assert.equal(h.prefetches.length, 1, '只有 24 时触发预取');
  h.unmount();
});
