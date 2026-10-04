import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { createReactStub } from './helpers/react-hooks.mjs';
import { deferred } from './helpers/deferred.mjs';

const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const jsx = (type, props) => ({ type, props });

function find(node, predicate) {
  if (Array.isArray(node)) return node.map((child) => find(child, predicate)).find(Boolean);
  if (!node || typeof node !== 'object') return null;
  if (predicate(node)) return node;
  return find(node.props?.children, predicate);
}

function harness() {
  const react = createReactStub();
  const requests = [];
  const source = fs.readFileSync(new URL('../src/components/market/macro/FactorDetails.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, Promise,
    require(id) {
      if (id === 'react') return react.React;
      if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx };
      // 摘要行的展示依赖；请求模拟与状态断言保持真实组件行为。
      if (id === 'framer-motion') return { motion: { div: 'div', span: 'span' } };
      if (id === '@/lib/motion') return { EASE_PAPER: [], GROW_X: {} };
      if (id === '@/lib/strengthColor') return { strengthBarClass: () => '' };
      if (id === '@/components/shared/ChangeBadge') return { default: 'ChangeBadge' };
      if (id === '@/api/modules/macro') return { macroApi: { module(moduleId) {
        const request = deferred();
        requests.push({ moduleId, ...request });
        return request.promise;
      } } };
      if (id === '@/api/client') return { ApiError: class ApiError extends Error {} };
      if (id === '@/components/icons') return { default: 'Icon' };
      if (id === '@/lib/utils') return { cn: (...parts) => parts.filter(Boolean).join(' ') };
      if (id === '@/components/shared/Skeleton') return { SkeletonRows: 'SkeletonRows' };
      if (id === '@/lib/scoreHints') return { MACRO_MODULE_HINTS: {} };
      if (id === '@/components/shared/InfoHint') return { default: 'InfoHint' };
      if (id === './FactorRow') return { FactorCard: 'FactorCard', FactorTableRow: 'FactorTableRow' };
      if (id.includes('i18n/core')) return { t: (value) => value };
      throw Error(`Unexpected import: ${id}`);
    },
  });
  let snapshotKey = 'old';
  const modules = [{ moduleId: 'risk', nameZh: '风险', nameEn: 'RISK', score: 40 }];
  const read = react.mount(() => module.exports.default({ modules, snapshotKey }));
  return { react, requests, read, changeSnapshot(key) { snapshotKey = key; react.rerender(); },
    open() { find(read(), (node) => node.type === 'button' && node.props?.['aria-controls'] === 'macro-factors-risk').props.onClick(); },
    shownError() { return find(read(), (node) => node.type === 'p' && node.props?.className === 'py-2 text-body-s text-ink-500') ?? null; },
    shownFactors() { return find(read(), (node) => node.type?.name === 'FactorTable')?.props.factors ?? null; },
  };
}

test('an older factor response cannot replace a newer macro snapshot', async () => {
  const h = harness();
  h.open();
  assert.equal(h.requests.length, 1);
  h.changeSnapshot('new');
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve({ factors: [{ factorId: 'new' }] });
  await settle();
  assert.equal(h.shownFactors()?.[0]?.factorId, 'new');
  h.requests[0].resolve({ factors: [{ factorId: 'old' }] });
  await settle();
  assert.equal(h.shownFactors()?.[0]?.factorId, 'new');
  h.react.unmount();
});


test('an older factor failure cannot hide newer factors or add an error', async () => {
  const h = harness();
  h.open();
  h.changeSnapshot('new');
  h.requests[1].resolve({ factors: [{ factorId: 'new' }] });
  await settle();
  h.requests[0].reject(new Error('old failure'));
  await settle();
  assert.equal(h.shownFactors()?.[0]?.factorId, 'new');
  assert.equal(h.shownError(), null);
  h.react.unmount();
});

test('returning to the same snapshot name does not revive its retired request', async () => {
  const h = harness();
  h.open();
  h.changeSnapshot('new');
  h.changeSnapshot('old');
  assert.equal(h.requests.length, 3);
  h.requests[2].resolve({ factors: [{ factorId: 'current' }] });
  await settle();
  h.requests[0].resolve({ factors: [{ factorId: 'retired' }] });
  h.requests[1].reject(new Error('retired failure'));
  await settle();
  assert.equal(h.shownFactors()?.[0]?.factorId, 'current');
  assert.equal(h.shownError(), null);
  h.react.unmount();
});

test('current factor failure remains visible and can be retried', async () => {
  const h = harness();
  h.open();
  h.requests[0].reject(new Error('current failure'));
  await settle();
  const error = h.shownError();
  assert.ok(error);
  find(error, (node) => node.type === 'button').props.onClick();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve({ factors: [{ factorId: 'retried' }] });
  await settle();
  assert.equal(h.shownFactors()?.[0]?.factorId, 'retried');
  assert.equal(h.shownError(), null);
  h.open();
  h.open();
  assert.equal(h.requests.length, 2, 'reopening the current snapshot uses its cached factors');
  h.react.unmount();
});
