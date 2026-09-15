import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createElement, Component } from 'react';
import ts from 'typescript';
import { createReactStub } from './helpers/react-hooks.mjs';
import { decideIntentPrefetch } from '../../scripts/perf/lib/round6_intent_decision.mjs';
import { readyGateFailures, summarizeReady } from '../../scripts/perf/lib/round6_ready_summary.mjs';
import { parseStaticJsImports } from '../../scripts/perf/lib/round6_bundle_graph.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = (...parts) => path.join(here, '..', 'src', ...parts);

function transpile(file, jsx = ts.JsxEmit.ReactJSX) {
  return ts.transpileModule(file, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
      jsx,
    },
  }).outputText;
}

function jsxRuntime() {
  return {
    jsx: (type, props, key) => createElement(type, { ...props, key }),
    jsxs: (type, props, key) => createElement(type, { ...props, key }),
    Fragment: 'Fragment',
  };
}

function loadCompiled(file, requireMap) {
  const compiled = transpile(file);
  const module = { exports: {} };
  const req = (id) => {
    if (id === 'react/jsx-runtime' && !requireMap[id]) return jsxRuntime();
    if (id in requireMap) return requireMap[id];
    throw new Error(`unexpected import ${id}`);
  };
  const fn = new Function('exports', 'require', 'module', compiled);
  fn(module.exports, req, module);
  return module.exports;
}

function renderNode(node) {
  if (node == null || typeof node === 'boolean') return [];
  if (typeof node === 'string' || typeof node === 'number') return [String(node)];
  if (Array.isArray(node)) return node.flatMap(renderNode);
  const { type, props = {} } = node;
  if (typeof type === 'function') {
    const isClass = type.prototype && typeof type.prototype.render === 'function';
    if (isClass) {
      const inst = new type(props);
      inst.props = props;
      inst.state = inst.state || {};
      inst.setState = (partial) => {
        const next = typeof partial === 'function' ? partial(inst.state) : partial;
        inst.state = { ...inst.state, ...next };
      };
      try {
        return renderNode(inst.render());
      } catch (error) {
        if (typeof type.getDerivedStateFromError === 'function') {
          inst.state = { ...inst.state, ...type.getDerivedStateFromError(error) };
          return renderNode(inst.render());
        }
        throw error;
      }
    }
    return renderNode(type(props));
  }
  const name = typeof type === 'symbol' ? 'Fragment' : String(type);
  const attrs = Object.entries(props)
    .filter(([key]) => key !== 'children')
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(' ');
  return [`<${name}${attrs ? ` ${attrs}` : ''}>`, ...renderNode(props.children), `</${name}>`];
}

test('earnings refresh cooldown is enforced inside onRefresh', async () => {
  const page = await readFile(src('pages', 'Earnings.tsx'), 'utf8');
  const start = page.indexOf('const onRefresh = useCallback');
  const end = page.indexOf('}, [refreshing, cooldownUntil, q, toast]);');
  assert.ok(start >= 0 && end > start, 'onRefresh must depend on cooldownUntil');
  const body = page.slice(start, end);
  assert.match(body, /async \(\) => \{/);
  assert.match(body, /cooldownUntil - Date\.now\(\)/);
  assert.doesNotMatch(body, /cooldownRemain = 0/);
  assert.match(page, /onClick=\{\(\) => void onRefresh\(\)\}/);

  const posts = [];
  const toasts = [];
  const guard = ({ refreshing, cooldownUntil, now, post }) => {
    if (refreshing) return 'busy';
    const cooldownRemain = Math.max(0, Math.ceil((cooldownUntil - now) / 1000));
    if (cooldownRemain > 0) {
      toasts.push(cooldownRemain);
      return 'cooldown';
    }
    post();
    return 'sent';
  };
  const header = () => guard({
    refreshing: false,
    cooldownUntil: 1_000_000,
    now: 940_000,
    post: () => posts.push('header'),
  });
  const banner = () => guard({
    refreshing: false,
    cooldownUntil: 1_000_000,
    now: 940_000,
    post: () => posts.push('banner'),
  });
  assert.equal(header(), 'cooldown');
  assert.equal(banner(), 'cooldown');
  assert.deepEqual(posts, []);
  assert.deepEqual(toasts, [60, 60]);
});

test('chart lazy-load rejection does not unmount the earnings page', async () => {
  const chart = await readFile(src('components', 'earnings', 'DeferredEpsChart.tsx'), 'utf8');
  const boundarySource = await readFile(src('components', 'earnings', 'ChartLoadErrorBoundary.tsx'), 'utf8');
  assert.match(chart, /ChartLoadErrorBoundary/);
  assert.match(chart, /loadEpsHatchChart/);
  assert.match(chart, /setEpsHatchChart\(loadEpsHatchChart\)/);
  assert.doesNotMatch(chart, /useMemo\(\(\) => loadEpsHatchChart/);
  assert.match(boundarySource, /getDerivedStateFromError/);
  assert.match(boundarySource, /data-eps-chart-error/);
  assert.match(boundarySource, /重试图表/);

  const { default: ChartLoadErrorBoundary } = loadCompiled(boundarySource, {
    react: { Component },
    '../../i18n/core.ts': { t: (value) => value },
  });

  class ThrowingChart extends Component {
    render() {
      throw new Error('chart chunk rejected');
    }
  }

  const retries = [];
  const html = renderNode(createElement(
    'div',
    { 'data-earnings-page': '' },
    createElement('section', { 'data-earnings-list': '' }, 'AAPL'),
    createElement('section', { 'data-earnings-analysis': '' }, 'impact'),
    createElement(
      ChartLoadErrorBoundary,
      { onRetry: () => retries.push('retry') },
      createElement(ThrowingChart),
    ),
  )).join('');

  assert.match(html, /data-earnings-list/);
  assert.match(html, /data-earnings-analysis/);
  assert.match(html, /data-eps-chart-error/);
  assert.match(html, /重试图表/);
  assert.doesNotMatch(html, /页面显示失败/);

  assert.throws(
    () => renderNode(createElement(ThrowingChart)),
    /chart chunk rejected/,
  );
  assert.deepEqual(retries, []);
});

test('command palette does not prefetch while closed', async () => {
  const paletteSource = await readFile(src('components', 'CommandPalette.tsx'), 'utf8');
  const recent = { tickers: [] };

  const mountPalette = (open) => {
    const prefetched = [];
    const requireMap = {
      react: null,
      'react/jsx-runtime': {
        jsx: (type, props, key) => createElement(type, { ...props, key }),
        jsxs: (type, props, key) => createElement(type, { ...props, key }),
        Fragment: 'Fragment',
      },
      'react-router': { useNavigate: () => () => {} },
      '@/api/client': { ApiError: class ApiError extends Error {} },
      '@/api/modules/stocks': { stocksApi: { search: async () => [] } },
      '@/hooks/useAccess': { useAccess: () => ({ isOwner: false, isSignedIn: false, username: '', logout: async () => {} }) },
      '@/hooks/useFocusTrap': { useFocusTrap() {} },
      '@/hooks/useBodyScrollLock': { useBodyScrollLock() {} },
      '@/lib/focusScope': { isTopFocusScope: () => true },
      '@/lib/utils': { cn: (...parts) => parts.filter(Boolean).join(' ') },
      '@/lib/transitions': {
        overlayClassName: () => '',
        overlayVisible: (openState, phase) => openState || phase !== 'closed',
        placeGlide() {},
        readRootDurationMs: () => 0,
        useOverlayPhase: (openState) => (openState ? 'open' : 'closed'),
      },
      '@/lib/recentTickers': {
        pushRecent() {},
        readRecent: () => recent.tickers,
      },
      '@/components/icons': { default: () => null },
      '@/components/shared/TickerLogo': { default: () => null },
      '@/components/shared/SoftBadge': { default: () => null },
      '@/components/Navbar': { NAV_ITEMS: [{ no: '01', label: '首页', path: '/' }] },
      '../i18n/core.ts': { t: (value) => value },
      '../lib/prefetchRouteChunk.ts': {
        prefetchRouteOnIntent: (pathname) => { prefetched.push(pathname); },
      },
    };

    const harness = createReactStub();
    harness.React.useLayoutEffect = harness.React.useEffect;
    harness.React.createElement = createElement;
    harness.React.Fragment = 'Fragment';
    requireMap.react = harness.React;
    const { default: CommandPalette } = loadCompiled(paletteSource, requireMap);
    const raf = globalThis.requestAnimationFrame;
    const caf = globalThis.cancelAnimationFrame;
    globalThis.requestAnimationFrame = (fn) => {
      fn();
      return 1;
    };
    globalThis.cancelAnimationFrame = () => {};
    try {
      harness.mount(() => CommandPalette({
        open,
        onClose() {},
        onOpenTicker() {},
      }));
      harness.unmount();
    } finally {
      globalThis.requestAnimationFrame = raf;
      globalThis.cancelAnimationFrame = caf;
    }
    return prefetched;
  };

  assert.deepEqual(mountPalette(false), []);
  recent.tickers = ['AAPL'];
  assert.deepEqual(mountPalette(false), []);
  const opened = mountPalette(true);
  assert.ok(opened.length >= 1, 'open palette must prefetch the highlighted row');
  assert.deepEqual([...new Set(opened)], ['/stock/AAPL']);
});

test('surfaces ready summary only counts content as success', () => {
  const summary = summarizeReady([
    { ready_class: 'error', ready_ms: 12, wall_ms: 12 },
    { ready_class: 'timeout', ready_ms: 80, wall_ms: 80 },
    { ready_class: 'content', ready_ms: 40, wall_ms: 40 },
  ], 'wall_ms');
  assert.equal(summary.n, 3);
  assert.equal(summary.ready_n, 1);
  assert.equal(summary.error_n, 1);
  assert.equal(summary.timeout_n, 1);
  assert.equal(summary.p75, 40);
  assert.deepEqual(readyGateFailures(summary, { expectedN: 3, label: 'lab' }), [
    'lab: ready_n=1 expected=3',
    'lab: error_n=1',
    'lab: timeout_n=1',
  ]);
});

test('intent prefetch stays only when every sample is ready and unpaid', () => {
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 600, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 1, chart_n: 0, extra_paid_n: 8 },
  }).decision, 'revert');
  assert.equal(decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 600, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 7, chart_n: 0, extra_paid_n: 0 },
  }).decision, 'revert');
  const keep = decideIntentPrefetch({
    immediate: { p75: 900, ready_n: 8, n: 8 },
    hover_then_click: { p75: 600, ready_n: 8, n: 8 },
    hover_only: { n: 8, ready_n: 8, chunk_n: 8, chart_n: 0, extra_paid_n: 0 },
  });
  assert.equal(keep.decision, 'keep');
});

test('bundle graph parser only follows static js imports', () => {
  assert.deepEqual(parseStaticJsImports('import{A}from"./chunk-a.js";import"./index.css";const x=import("./Home-x.js")'), [
    './chunk-a.js',
  ]);
});
