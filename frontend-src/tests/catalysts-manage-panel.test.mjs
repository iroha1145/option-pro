/**
 * 管理设置（2026-10-08 第二版起是新闻页的一个栏目，不再是页面上方可折叠的卡）。
 *
 * 忙碌槽回归：按钮点击时要「抢槽」——旧写法在 setState 的更新函数里改局部变量再同步读它，
 * React 只在没有待处理更新时才立刻执行更新函数，否则延后到渲染时，第一次点击就被吞掉，
 * 槽却已被占住、按钮一直转圈。这里用一个「更新函数一律延后执行」的 useState 桩复现这种情形。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';
import { createReactStub } from './helpers/react-hooks.mjs';
import { deferred } from './helpers/deferred.mjs';

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
    Promise,
    require(id) {
      if (id in imports) return imports[id];
      throw new Error(`Unexpected import ${id} in ${rel}`);
    },
  });
  return module.exports;
}

/* 本文件内的小组件（动作按钮、分区卡片）不含 hook，直接展开，好找到真实的 button。 */
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
const findButton = (tree, label) => findAll(tree, (node) => node.type === 'button' && textOf(node) === label)[0] ?? null;

async function settle() {
  for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

/** 真实 React 不一定立刻执行 setState 的更新函数；这里一律延后到下一次渲染再执行。 */
function lazyUpdaterReact(runner) {
  const base = runner.React.useState;
  const queues = new Map();
  runner.React.useState = (initial) => {
    const [value, set] = base(initial);
    if (!queues.has(set)) {
      const queue = [];
      queues.set(set, {
        queue,
        wrapped: (next) => {
          if (typeof next === 'function') {
            queue.push(next);
            // 与 React 一致：函数参数不在 set 调用里执行，留到随后的渲染。
            queueMicrotask(() => runner.rerender());
          } else {
            set(next);
          }
        },
      });
    }
    const entry = queues.get(set);
    let current = value;
    if (entry.queue.length) {
      for (const updater of entry.queue.splice(0)) current = updater(current);
      set(current);
    }
    return [current, entry.wrapped];
  };
}

function harness({ workerAction, catalystRefresh } = {}) {
  const runner = createReactStub();
  lazyUpdaterReact(runner);
  const calls = { worker: [], refresh: [] };
  const toasts = [];
  const toast = {
    info: (title, description) => toasts.push(['info', title, description]),
    success: (title, description) => toasts.push(['success', title, description]),
    error: (title, description) => toasts.push(['error', title, description]),
  };
  const adminApi = {
    workerStatus: async () => ({ healthy: true, status: 'ok', tasks: [] }),
    runtimeSettings: async () => ({
      version: 3,
      toggles: { manualAnalysisEnabled: true, scheduledAnalysisEnabled: true },
      algorithms: { radarSortAlgorithm: 'production' },
    }),
    workerAction: async (action) => {
      calls.worker.push(action);
      return workerAction ? workerAction(action) : { requestId: null, status: 'queued', reason: null };
    },
    catalystRefresh: async (op) => {
      calls.refresh.push(op);
      return catalystRefresh ? catalystRefresh(op) : { requestId: null, status: 'queued', reason: null };
    },
  };
  class TestApiError extends Error {}
  const ManagePanel = compile('components/catalysts/ManagePanel.tsx', {
    react: runner.React,
    'react/jsx-runtime': invokingJsx,
    '@/api/client': { ApiError: TestApiError },
    '@/api/modules/admin': { adminApi },
    '@/hooks/useAccess': { useAccess: () => ({ isOwner: true }) },
    '@/hooks/useToast': { useToast: () => toast },
    '@/api/queryRegistry': { invalidateQueryPaths() {} },
    '@/api/marketRead': { resetMarketReadPrefixes() {} },
    '@/lib/algorithmView': { bumpAlgorithmViewGeneration() {} },
    './bits': { Led: 'Led' },
    '@/components/icons': { default: 'Icon' },
    '@/components/shared/Spinner': { default: 'Spinner' },
    '@/lib/utils': { cn: (...xs) => xs.filter(Boolean).join(' ') },
    '@/components/shared/Segmented': { default: 'Segmented' },
    '@/components/shared/Switch': { default: 'Switch' },
    '@/lib/format': { fmtRelative: () => '刚刚' },
    '../../i18n/core.ts': { t: stubT },
    '@/components/shared/TextSwap': { default: ({ children }) => children },
  }).default;
  const read = runner.mount(() => ManagePanel({ onDataRefreshed() {} }));
  return { read, calls, toasts, unmount: () => runner.unmount() };
}

test('管理设置打开即展开：没有折叠开关，三个分区与按钮都在', async () => {
  const h = harness();
  await settle();
  const tree = h.read();
  const section = findAll(tree, (node) => node.type === 'section')[0];
  assert.equal(section.props['aria-label'], '消息管理');
  const text = textOf(tree);
  for (const label of ['管理设置', '更新数据', '后台任务', '运行设置']) assert.match(text, new RegExp(label));
  assert.equal(findAll(tree, (node) => node.props['aria-expanded'] !== undefined).length, 0, '不再有展开/收起的开关');
  for (const label of ['更新消息', '更新日历', '检查来源', '重点股票', '选股评分', '突破雷达']) assert.ok(findButton(tree, label), label);
  h.unmount();
});

test('后台任务按钮：第一次点击就发出请求并提示「更新已入队」，忙碌槽随后释放', async () => {
  const h = harness();
  await settle();
  findButton(h.read(), '选股评分').props.onClick();
  await settle();
  assert.deepEqual(Array.from(h.calls.worker), ['strength_refresh'], '更新函数被延后执行时，第一次点击也不能被吞掉');
  assert.deepEqual(Array.from(h.toasts.at(-1)).slice(0, 2), ['success', '选股评分更新已入队']);
  assert.equal(findButton(h.read(), '选股评分').props.disabled, false, '结束后按钮可再点');
  findButton(h.read(), '重点股票').props.onClick();
  await settle();
  assert.deepEqual(Array.from(h.calls.worker), ['strength_refresh', 'focus_refresh']);
  h.unmount();
});

test('更新数据按钮：提示用不带「更新」的模板，整个面板一次只做一件事', async () => {
  const gate = deferred();
  const h = harness({ catalystRefresh: () => gate.promise });
  await settle();
  findButton(h.read(), '更新消息').props.onClick();
  await settle();
  assert.deepEqual(Array.from(h.calls.refresh), ['news']);
  assert.equal(findButton(h.read(), '更新消息').props.disabled, true, '进行中的按钮禁用');
  // 面板互斥：另一个按钮此时不会发请求。
  findButton(h.read(), '更新日历').props.onClick();
  findButton(h.read(), '选股评分').props.onClick();
  await settle();
  assert.deepEqual(Array.from(h.calls.refresh), ['news']);
  assert.deepEqual(Array.from(h.calls.worker), []);
  gate.resolve({ requestId: null, status: 'queued', reason: null });
  await settle();
  assert.deepEqual(Array.from(h.toasts.at(-1)).slice(0, 2), ['info', '更新消息已入队']);
  assert.equal(findButton(h.read(), '更新消息').props.disabled, false);
  findButton(h.read(), '更新日历').props.onClick();
  await settle();
  assert.deepEqual(Array.from(h.calls.refresh), ['news', 'calendar'], '槽释放后可以继续');
  h.unmount();
});

test('请求被拒绝：提示「未受理」并释放忙碌槽', async () => {
  const h = harness({ workerAction: () => { throw new Error('冷却中'); } });
  await settle();
  findButton(h.read(), '突破雷达').props.onClick();
  await settle();
  assert.deepEqual(Array.from(h.toasts.at(-1)).slice(0, 2), ['error', '突破雷达未受理']);
  assert.equal(findButton(h.read(), '突破雷达').props.disabled, false);
  h.unmount();
});
