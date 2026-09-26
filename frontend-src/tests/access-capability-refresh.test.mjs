import { deferred } from './helpers/deferred.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { createReactStub } from './helpers/react-hooks.mjs';
import { SHARED_UI_STUBS } from './helpers/shared-ui-stubs.mjs';

const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const jsx = { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
function compile(name, imports, env) {
  const module = { exports: {} };
  const source = fs.readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, {
    module, exports: module.exports, Date, Error, Promise, ...env,
    require(id) {
      if (id === 'react/jsx-runtime') return jsx;
      if (id.includes('i18n/core')) return { t: value => value };
      if (id in imports) return imports[id];
      if (id in SHARED_UI_STUBS) return SHARED_UI_STUBS[id];
      throw Error(`Unexpected import: ${id}`);
    },
  });
  return module.exports;
}
const owner = { access_mode: 'password', logged_in: true };
const visitor = { access_mode: 'password', logged_in: false };

function harness(t) {
  const identities = [], capabilities = [], writes = [];
  const listeners = new Map(), timers = new Map();
  let serial = 0;
  const win = {
    setTimeout: (fn, delay) => { const id = ++serial; timers.set(id, { fn, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    setInterval: fn => { listeners.set('interval', fn); return ++serial; },
    clearInterval() {},
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); },
  };
  const env = { window: win, document: { addEventListener() {}, removeEventListener() {}, visibilityState: 'visible' } };
  // Keep the real access response mapper and enrichment logic; only control HTTP completion.
  const api = compile('api/modules/access.ts', {
    '../client': {
      mockOr: (_, live) => live(),
      get: path => {
        const d = { path, ...deferred() };
        (path === '/access/status' ? identities : capabilities).push(d);
        return d.promise;
      },
      post: path => { const d = { path, ...deferred() }; writes.push(d); return d.promise; },
    },
    '../live': { asRec: value => value ?? {}, pickB: (row, key) => row[key], pickS: (row, key) => row[key] },
    '@/mocks/session': {},
  }, env).accessApi;
  const runner = createReactStub();
  runner.React.createContext = () => ({ Provider: 'provider' });
  runner.React.useMemo = (fn, deps) => runner.React.useCallback(fn, deps)();
  const { AccessProvider } = compile('hooks/useAccess.tsx', {
    react: runner.React,
    '@/api/modules/access': { accessApi: api }, '@/api/modules/account': { accountApi: {} },
    '@/api/client': { ApiError: Error, PRINCIPAL_INVALID_EVENT: 'invalid' },
    '@/lib/identityRetry': { identityRetryDelayMs: () => 1000 },
    '@/api/sharedRead': { dropSharedReads() {} }, '@/api/queryRegistry': { setQueryPrincipal() {} },
    '@/api/marketRead': { resetMarketReadState() {} }, '@/components/catalysts/api': { clearCatalystReadCache() {} },
    '@/lib/viewPreferenceWrites': { bindPreferenceWritePrincipal() {}, invalidatePreferenceWriteQueue() {} },
  }, env);
  const read = runner.mount(() => AccessProvider({ children: null }));
  t.after(() => runner.unmount());
  const access = () => read().props.value;
  function resolveCapabilities(enabled = true, pending = capabilities.splice(0)) {
    for (const d of pending) d.resolve(d.path === '/ai/status'
      ? { enabled: true } : { settings: { ai: { manual_analysis_enabled: enabled } } });
  }
  async function ready() {
    identities.shift().resolve(owner); await settle(); resolveCapabilities(); await settle();
    assert.equal(access().aiAvailable, true);
  }
  async function flushImmediate() {
    for (const [id, timer] of [...timers]) {
      if (timer.delay === 0) { timers.delete(id); timer.fn(); }
    }
    await settle();
  }
  return { access, identities, capabilities, writes, listeners, env, resolveCapabilities, ready, flushImmediate };
}

function mountCard(t, h, confirmation, ticker = 'NVDA') {
  const runner = createReactStub();
  let reads = 0;
  const report = confirmation ? { status: 'not_requested', result: null } : {
    status: 'succeeded',
    result: { ticker: 'NVDA', summary: 'Saved analysis', expectation: 'Saved expectation', impacted: [], outputLanguage: 'zh-CN' },
  };
  const noop = () => {}, shell = { openTicker: noop }, toast = { success: noop, error: noop };
  const imports = {
    react: runner.React, 'framer-motion': { AnimatePresence: 'Presence', motion: { div: 'motion.div' } },
    '@/api/client': { ApiError: Error },
    '@/api/modules/earnings': { earningsApi: { reportAnalysis: async () => { reads++; return report; } } },
    '@/hooks/useAccess': { useAccess: h.access }, '@/hooks/useToast': { useToast: () => toast },
    '@/hooks/useShell': { useShell: () => shell }, '@/lib/utils': { cn: (...xs) => xs.join(' ') },
    './types': { exNum: () => null },
  };
  for (const name of ['@/components/shared/AnalysisIcon', '@/components/shared/SoftBadge', '@/components/icons', '@/components/shared/SourceNote', './PulseDot']) {
    imports[name] = { default: name };
  }
  imports['@/components/shared/Skeleton'] = { SkeletonText: 'SkeletonText' };
  const Card = compile('components/earnings/ImpactCard.tsx', imports, h.env).default;
  const props = { ticker, row: ticker ? { date: '2026-09-14' } : null, calendarRevision: 1, onAnalyzed: noop };
  const read = runner.mount(() => Card(props));
  t.after(() => runner.unmount());
  return { read, rerender: runner.rerender, reads: () => reads };
}
function buttons(node) {
  if (!node || typeof node !== 'object') return [];
  if (Array.isArray(node)) return node.flatMap(buttons);
  return [...(node.type === 'button' ? [node] : []), ...buttons(node.props?.children)];
}

for (const confirmation of [false, true]) {
  test(`same-owner background probes preserve ${confirmation ? 'the analysis confirmation' : 'the saved analysis'} without extra report reads`, async t => {
    const h = harness(t); await h.ready();
    const card = mountCard(t, h, confirmation); await h.flushImmediate();
    if (confirmation) buttons(card.read()).find(node => JSON.stringify(node.props.children).includes('生成分析')).props.onClick();
    const text = confirmation ? '为 {ticker} 生成本次财报分析。' : 'Saved analysis';
    assert.ok(JSON.stringify(card.read()).includes(text));
    for (const trigger of ['focus', 'interval']) {
      h.listeners.get(trigger)(); h.identities.shift().resolve(owner); await settle(); card.rerender();
      assert.ok(JSON.stringify(card.read()).includes(text), `${trigger} must retain the current card while capabilities are pending`);
      assert.equal(h.access().aiAvailable, true);
      await h.flushImmediate(); h.resolveCapabilities(); await settle(); card.rerender(); await h.flushImmediate();
      assert.ok(JSON.stringify(card.read()).includes(text));
      assert.equal(card.reads(), 1);
    }
  });
}

test('first identity confirmation renders without waiting for capability probes', async t => {
  const h = harness(t);
  h.identities.shift().resolve(owner); await settle();
  assert.equal(h.access().loading, false);
  assert.equal(h.access().hasConfirmedIdentity, true);
  assert.equal(h.access().isOwner, true);
  assert.equal(h.access().aiAvailable, false);
  assert.equal(h.access().aiPending, true, '能力探针未回来前是未知，不是关闭');
  assert.equal(h.capabilities.length, 2);
  h.resolveCapabilities(); await settle(); assert.equal(h.access().aiAvailable, true);
  assert.equal(h.access().aiPending, false);
});

for (const ticker of ['NVDA', null]) {
  test(`pending capabilities show ${ticker ? 'loading for a selected report' : 'the empty-selection prompt without a ticker'}`, async t => {
    const h = harness(t);
    h.identities.shift().resolve(owner); await settle();
    const card = mountCard(t, h, false, ticker); await h.flushImmediate();
    const pending = JSON.stringify(card.read());
    assert.equal(h.access().aiPending, true);
    assert.equal(pending.includes('AI 影响分析加载中'), ticker !== null);
    assert.equal(pending.includes('选择一只标的'), ticker === null);
    assert.equal(card.reads(), 0, 'capability confirmation must still precede the first report read');
    h.resolveCapabilities(); await settle(); card.rerender(); await h.flushImmediate();
    assert.equal(card.reads(), ticker ? 1 : 0);
    assert.equal(JSON.stringify(card.read()).includes('Saved analysis'), ticker !== null);
    assert.equal(JSON.stringify(card.read()).includes('选择一只标的'), ticker === null);
  });
}

test('a confirmed capability shutdown still closes the analysis confirmation', async t => {
  const h = harness(t); await h.ready();
  const card = mountCard(t, h, true); await h.flushImmediate();
  buttons(card.read()).find(node => JSON.stringify(node.props.children).includes('生成分析')).props.onClick();
  const refresh = h.access().refresh(); h.identities.shift().resolve(owner); await settle();
  h.resolveCapabilities(false); await refresh; card.rerender(); await h.flushImmediate();
  assert.equal(h.access().aiEnabled, false);
  assert.equal(h.access().aiAvailable, false);
  assert.equal(JSON.stringify(card.read()).includes('为 {ticker} 生成本次财报分析。'), false);
  assert.ok(JSON.stringify(card.read()).includes('AI 分析未启用'));
});

test('a late capability result cannot overwrite a newer visitor identity', async t => {
  const h = harness(t); await h.ready();
  const old = h.access().refresh(); h.identities.shift().resolve(owner); await settle();
  const pending = h.capabilities.splice(0);
  const current = h.access().refresh(); h.identities.shift().resolve(visitor); await current;
  assert.equal(h.access().isOwner, false); assert.equal(h.access().aiAvailable, false);
  h.resolveCapabilities(true, pending); await old;
  assert.equal(h.access().isOwner, false); assert.equal(h.access().aiAvailable, false);
});

test('different owner account identities do not inherit capability confirmation', async t => {
  const h = harness(t); await h.ready();
  const refresh = h.access().refresh();
  h.identities.shift().resolve({ ...owner, account: { logged_in: true, username: 'alice' } }); await settle();
  assert.equal(h.access().username, 'alice'); assert.equal(h.access().aiAvailable, false);
  h.resolveCapabilities(); await refresh; assert.equal(h.access().aiAvailable, true);
});

for (const action of ['credential write', 'explicit invalidation']) {
  test(`${action} retires capability confirmation even when the same owner returns`, async t => {
    const h = harness(t); await h.ready();
    const old = h.access().refresh(); h.identities.shift().resolve(owner); await settle();
    const pending = h.capabilities.splice(0);
    let writing;
    if (action === 'credential write') {
      writing = h.access().login('admin', 'changed');
      assert.equal(h.access().isOwner, false);
      h.writes.shift().resolve(); await settle();
    } else h.listeners.get('invalid')();
    assert.equal(h.access().aiAvailable, false);
    h.identities.shift().resolve(owner); await settle();
    assert.equal(h.access().isOwner, true); assert.equal(h.access().aiAvailable, false);
    h.resolveCapabilities(true, pending); await old;
    assert.equal(h.access().aiAvailable, false, 'the old enrichment must stay retired');
    h.resolveCapabilities(); await writing; await settle();
    assert.equal(h.access().aiAvailable, true);
  });
}
