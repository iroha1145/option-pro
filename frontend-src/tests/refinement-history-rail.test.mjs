import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { createReactStub } from './helpers/react-hooks.mjs';

const jsx = (type, props) => ({ type, props });
function all(node, predicate) {
  if (Array.isArray(node)) return node.flatMap((child) => all(child, predicate));
  if (!node || typeof node !== 'object') return [];
  return [...(predicate(node) ? [node] : []), ...all(node.props?.children, predicate)];
}
function harness() {
  const react = createReactStub();
  let grouped = 0;
  let serverReads = 0;
  const source = fs.readFileSync(new URL('../src/components/breakouts/HistoryRail.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports, Date, Map,
    setTimeout() { throw new Error('local expansion must not wait for a timer'); },
    require(id) {
      if (id === 'react') return react.React;
      if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx };
      if (id === 'framer-motion') return { motion: { li: 'li' } };
      if (id === '@/lib/utils') return { cn: (...parts) => parts.filter(Boolean).join(' ') };
      if (id === '@/lib/motion') return { DUR_SECTION: 0.2, EASE_PAPER: [] };
      if (id === '@/lib/format') return { fmtNyDayKey() { grouped++; return '2026-10-02'; }, fmtNyHHmm: () => '14:00', fmtPrice: String };
      if (id === './types') return { LIFECYCLE_CN: {}, LIFECYCLE_TONE: {}, SETUP_CN: {} };
      if (id === '../../i18n/core.ts') return { getLocale: () => 'zh', t: (value) => value };
      if (id === '@/components/shared/EmptyState') return { default: 'EmptyState' };
      if (id === '@/components/shared/Skeleton') return { SkeletonRows: 'SkeletonRows' };
      if (id === '@/components/shared/Spinner') return { default: 'Spinner' };
      throw new Error(`Unexpected import: ${id}`);
    },
  });
  const props = { filterKey: 'all', events: Array.from({ length: 25 }, (_, i) => ({ event_id: i, ticker: `T${i}`, event_at: '2026-10-02T18:00:00Z' })), loadedCount: 25, total: null, serverHasMore: true, loadingServerMore: false, serverMoreError: null, onFetchMore: () => { serverReads++; }, stale: false, loading: false, error: null, onRetry() {}, onOpenDetail() {} };
  const read = react.mount(() => module.exports.default(props));
  return { props, react, read, get grouped() { return grouped; }, get serverReads() { return serverReads; }, rows: () => all(read(), (node) => node.type === 'li'), buttons: () => all(read(), (node) => node.type === 'button') };
}

test('history grouping is reused until events or visible count changes', () => {
  const h = harness();
  assert.equal(h.rows().length, 12);
  const grouped = h.grouped;
  h.props.stale = true;
  h.react.rerender();
  assert.equal(h.grouped, grouped);
  h.props.events = [...h.props.events];
  h.react.rerender();
  assert.equal(h.grouped, grouped + 12);
  h.react.unmount();
});

test('local history expansion is immediate, preserves appended pages, and resets on filtering', () => {
  const h = harness();
  h.buttons()[0].props.onClick();
  assert.equal(h.rows().length, 24);
  h.buttons()[0].props.onClick();
  assert.equal(h.rows().length, 25);
  h.buttons()[0].props.onClick();
  assert.equal(h.serverReads, 1);
  h.props.events = [...h.props.events, { event_id: 26, ticker: 'T26', event_at: '2026-10-02T18:00:00Z' }];
  h.react.rerender();
  assert.equal(h.rows().length, 26);
  h.props.filterKey = 'changed';
  h.react.rerender();
  assert.equal(h.rows().length, 12);
  h.react.unmount();
});
