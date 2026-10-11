import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { deferred } from './helpers/deferred.mjs';
import { createReactStub } from './helpers/react-hooks.mjs';

const settle = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function prefetchHarness() {
  const source = fs.readFileSync(new URL('../src/lib/prefetchRouteChunk.ts', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
  } }).outputText;
  const module = { exports: {} };
  const requests = [];
  const navigator = { connection: { saveData: false } };
  vm.runInNewContext(code, { module, exports: module.exports, navigator, Promise, window: { location: { pathname: '/' } },
    require(id) {
      const routes = ['@/pages/Home', '@/pages/Watchlist', '@/pages/Screener', '@/pages/Breakouts', '@/pages/Sectors', '@/pages/Earnings', '@/pages/Catalysts', '@/pages/Market', '@/pages/CtaTrend', '@/pages/Login', '@/pages/StockDetail'];
      if (!routes.includes(id)) throw new Error(`Unexpected import: ${id}`);
      const request = deferred();
      requests.push({ id, ...request });
      return request.promise;
    },
  });
  return { api: module.exports, requests, navigator };
}

test('a failed speculative route load can be retried on the next intent', async () => {
  const h = prefetchHarness();
  h.api.prefetchRouteOnIntent('/earnings');
  await settle();
  assert.equal(h.requests.length, 1);
  h.requests[0].reject(new Error('temporary chunk failure'));
  await settle();
  h.api.prefetchRouteOnIntent('/earnings');
  await settle();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve({});
  await settle();
  h.api.prefetchRouteOnIntent('/earnings');
  await settle();
  assert.equal(h.requests.length, 2, 'a successful preload stays cached');
});

test('intent preloads keep the two-request limit while a chunk is pending', async () => {
  const h = prefetchHarness();
  h.api.prefetchRouteOnIntent('/earnings');
  h.api.prefetchRouteOnIntent('/screener');
  h.api.prefetchRouteOnIntent('/breakouts');
  await settle();
  assert.equal(h.requests.length, 2);
  h.requests[0].resolve({});
  await settle();
  h.api.prefetchRouteOnIntent('/breakouts');
  await settle();
  assert.equal(h.requests.length, 3);
  h.requests[1].resolve({});
  h.requests[2].resolve({});
  await settle();
});


test('save-data skips speculative loads while the active page can still load', async () => {
  const h = prefetchHarness();
  h.navigator.connection.saveData = true;
  h.api.prefetchRouteOnIntent('/earnings');
  await settle();
  assert.equal(h.requests.length, 0);
  h.api.prefetchRouteChunk('/earnings');
  await settle();
  assert.equal(h.requests.length, 1);
  h.requests[0].resolve({});
  await settle();
});

test('a failed active-route preload is handled without preventing navigation retry', async () => {
  const h = prefetchHarness();
  h.api.prefetchRouteChunk('/earnings');
  await settle();
  h.requests[0].reject(new Error('temporary active chunk failure'));
  await settle();
  h.api.prefetchRouteChunk('/earnings');
  await settle();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve({});
  await settle();
});


test('idle shell preloads wait for identity, honor save-data, and share the two-request limit', async () => {
  const h = prefetchHarness();
  const react = createReactStub();
  let identity = false;
  let idle = null;
  const source = fs.readFileSync(new URL('../src/components/Layout.tsx', import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports,
    window: { addEventListener() {}, removeEventListener() {} },
    require(id) {
      if (id === 'react') return react.React;
      if (id === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
      if (id === 'react-router') return { useLocation: () => ({ pathname: '/' }), useNavigate: () => () => {}, useNavigationType: () => 'POP', Outlet: 'Outlet' };
      if (id === '@/hooks/useAccess') return { useAccess: () => ({ role: 'visitor', username: null, hasConfirmedIdentity: identity }) };
      if (id === '@/hooks/useShell') return { ShellContext: { Provider: 'Provider' } };
      if (id === '@/lib/afterLoadIdle') return { afterLoadIdle: (run) => { idle = run; return () => { idle = null; }; } };
      if (id === '@/lib/prefetchRouteChunk') return h.api;
      if (id === '@/lib/recentTickers') return { pushRecent() {} };
      if (id === '@/api/client') return { isMock: false };
      if (id === '../i18n/core.ts') return { t: (value) => value };
      const components = {
        '@/components/Navbar': 'Navbar',
        '@/components/IndexTape': 'IndexTape',
        '@/components/QuoteConnection': 'QuoteConnection',
        '@/components/Footer': 'Footer',
        '@/components/shared/RouteErrorBoundary': 'RouteErrorBoundary',
        '@/components/shared/PageFallback': 'PageFallback',
        '@/components/shared/StatusNotice': 'StatusNotice',
        '@/components/MobileDock': 'MobileDock',
        '@/components/CommandPalette': 'CommandPalette',
      };
      if (Object.hasOwn(components, id)) return { default: components[id] };
      throw new Error(`Unexpected import: ${id}`);
    },
  });
  react.mount(() => module.exports.default());
  assert.equal(idle, null);
  identity = true;
  react.rerender();
  assert.equal(typeof idle, 'function');
  h.navigator.connection.saveData = true;
  idle();
  await settle();
  assert.equal(h.requests.length, 0);
  h.navigator.connection.saveData = false;
  idle();
  await settle();
  assert.deepEqual(h.requests.map((request) => request.id), ['@/pages/Catalysts', '@/pages/Watchlist']);
  h.api.prefetchRouteOnIntent('/earnings');
  await settle();
  assert.equal(h.requests.length, 2);
  h.requests.forEach((request) => request.resolve({}));
  await settle();
  react.unmount();
});
