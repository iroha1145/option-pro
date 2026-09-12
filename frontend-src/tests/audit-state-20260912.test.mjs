import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');
const source = name => fs.readFileSync(path.join(root, name), 'utf8');
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
async function settle() { for (let i = 0; i < 15; i++) await Promise.resolve(); }
function environment() {
  const timers = new Map(), listeners = new Map();
  let next = 0;
  const env = {
    setTimeout: fn => { timers.set(++next, fn); return next; }, clearTimeout: id => timers.delete(id),
    setInterval: () => ++next, clearInterval() {},
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name); },
  };
  return { ...env, window: env, document: { ...env, visibilityState: 'visible', querySelector: () => null }, timers, listeners };
}
function load(name, imports, env = environment()) {
  const module = { exports: {} };
  const code = ts.transpileModule(source(name), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, require(id) { if (Object.hasOwn(imports, id)) return imports[id]; throw Error(`Unexpected import ${id}`); }, Date, ...env });
  return module.exports;
}
function createReactStub() {
  const slots = [];
  let cursor = 0;
  let renderFn = null;
  let renderArgs = [];
  let lastResult = null;
  let rendering = false;
  let renderQueued = false;
  const effects = [];

  function flushEffects() {
    for (const effect of effects) {
      if (!effect.dirty) continue;
      effect.dirty = false;
      if (effect.cleanup) effect.cleanup();
      const cleanup = effect.create();
      effect.cleanup = typeof cleanup === 'function' ? cleanup : null;
    }
  }

  function render() {
    if (rendering) {
      renderQueued = true;
      return;
    }
    rendering = true;
    let guard = 0;
    do {
      renderQueued = false;
      guard += 1;
      if (guard > 50) throw new Error('render loop runaway');
      cursor = 0;
      lastResult = renderFn(...renderArgs);
      flushEffects();
    } while (renderQueued);
    rendering = false;
  }

  const React = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) {
        slots[index] = {
          value: typeof initial === 'function' ? initial() : initial,
        };
      }
      const slot = slots[index];
      if (!slot.set) {
        slot.set = (next) => {
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (Object.is(value, slot.value)) return;
          slot.value = value;
          render();
        };
      }
      return [slot.value, slot.set];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: { current: initial } };
      return slots[index].value;
    },
    useCallback(fn, deps) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: fn, deps: undefined };
      const slot = slots[index];
      const changed =
        slot.deps === undefined ||
        deps === undefined ||
        deps.length !== slot.deps.length ||
        deps.some((dep, i) => !Object.is(dep, slot.deps[i]));
      if (changed) {
        slot.value = fn;
        slot.deps = deps;
      }
      return slot.value;
    },
    useEffect(create, deps) {
      const index = cursor++;
      if (!(index in slots)) {
        const record = { deps: undefined, create, cleanup: null, dirty: true };
        slots[index] = { value: record };
        effects.push(record);
      }
      const record = slots[index].value;
      const changed =
        record.deps === undefined ||
        deps === undefined ||
        deps.length !== record.deps.length ||
        deps.some((dep, i) => !Object.is(dep, record.deps[i]));
      record.deps = deps;
      record.create = create;
      if (changed && !record.dirty) record.dirty = true;
      if (record.deps !== undefined && !changed) record.dirty = record.dirty && true;
    },
  };

  return {
    React,
    unmount() {
      for (const effect of effects) if (effect.cleanup) effect.cleanup();
    },
    mount(fn, ...args) {
      renderFn = fn;
      renderArgs = args;
      render();
      return () => lastResult;
    },
  };
}


function reactRunner() {
  const runner = createReactStub();
  runner.React.useMemo = (fn, deps) => runner.React.useCallback(fn, deps)();
  runner.React.createContext = () => ({ Provider: 'provider' });
  return runner;
}
class ApiError extends Error { constructor(code, message) { super(message); this.code = code; } }
const translate = { t: text => text };
const jsx = { jsx: (type, props, key) => ({ type, props, key }), jsxs: (type, props, key) => ({ type, props, key }) };
function polling(runner, env) { return load('hooks/usePolling.ts', { react: runner.React, '@/api/client': { ApiError }, '../i18n/core.ts': translate }, env).usePolling; }
function registryHarness() {
  const env = environment(), requests = [], writes = [], records = new Map();
  let now = 100000, readRecord = async p => records.get(p) ?? null;
  const q = load('api/queryRegistry.ts', {
    './client.ts': { REQUEST_TIMEOUT_MS: 20000, requestRaw: (path, init) => { const request = deferred(); requests.push({ ...request, path, init }); return request.promise; } },
    './persistedCache.ts': {
      readPersisted: p => readRecord(p), deletePersisted: async p => records.delete(p), clearPersisted: async () => records.clear(),
      writePersisted: async (record, isCurrent) => { writes.push({ record, isCurrent }); if (isCurrent()) records.set(record.path, record); },
    },
  }, { ...env, Date: { now: () => now } });
  const reply = (body, status = 200, index = 0) => requests.splice(index, 1)[0].resolve({ status, headers: new Headers({ ETag: '"v1"' }), json: async () => body });
  return { q, env, requests, writes, records, reply, advance: ms => { now += ms; }, setRead: fn => { readRecord = fn; } };
}

test('unready watchlist makes no request or successful empty payload, including manual refresh', async () => {
  const runner = reactRunner(), env = environment(), usePolling = polling(runner, env);
  let enabled = false, calls = 0;
  const network = deferred();
  const render = () => usePolling(() => { calls++; return network.promise; }, null, [], { enabled });
  const read = runner.mount(render);
  read().refresh({ force: true }); await settle();
  assert.equal(calls, 0); assert.equal(read().data, null); assert.equal(read().lastUpdatedAt, null);
  enabled = true; runner.mount(render); await settle();
  assert.equal(calls, 1); assert.equal(read().loading, true);
  network.reject(new ApiError(503, 'unavailable')); await settle();
  assert.equal(read().data, null); assert.equal(read().error.code, 503); assert.equal(read().loading, false);
  assert.match(source('pages/Watchlist.tsx'), /enabled: !personal.loading && !personal.error/);
  assert.doesNotMatch(source('pages/Watchlist.tsx'), /if \(personal.loading \|\| personal.error\) return Promise.resolve\(\[\]\)/);
  runner.unmount();
});

test('old conditional response returns its own snapshot after invalidation and newer success', async () => {
  const h = registryHarness(); h.q.setQueryPrincipal('visitor\0');
  const first = h.q.registryGet('/earnings/upcoming'); h.reply({ version: 'first' }); await first;
  h.advance(61000);
  const old = h.q.registryGet('/earnings/upcoming');
  assert.equal(h.requests[0].init.headers['If-None-Match'], '"v1"');
  h.q.invalidateQueryPaths(['/earnings/upcoming'], { reload: true });
  const fresh = h.q.registryGet('/earnings/upcoming'); h.reply({ version: 'new' }, 200, 1); await fresh;
  h.reply(null, 304);
  assert.equal((await old).version, 'first');
  assert.equal((await h.q.registryGet('/earnings/upcoming')).version, 'new');
});

test('late cache restore cannot revive a disabled polling effect', async () => {
  const runner = reactRunner(), env = environment(), usePolling = polling(runner, env);
  const restored = deferred(), network = deferred();
  let enabled = true;
  const render = () => usePolling(() => network.promise, null, [], { enabled, restore: () => restored.promise });
  const read = runner.mount(render);
  enabled = false; runner.mount(render);
  restored.resolve({ version: 'late' }); network.resolve({ version: 'old' }); await settle();
  assert.equal(read().data, null);
  runner.unmount();
});

test('unknown identity never persists or reuses a fresh response after confirmation', async () => {
  const h = registryHarness();
  const early = h.q.registryGet('/strength/market');
  const shared = h.q.registryGet('/strength/market');
  assert.equal(h.requests.length, 1);
  h.reply({ version: 'unconfirmed' });
  assert.equal(await early, await shared);
  assert.equal(h.records.size, 0);
  h.q.setQueryPrincipal('owner\0');
  const late = h.q.registryGet('/strength/market'); h.reply({ version: 'confirmed' }); await late;
  assert.equal(h.records.get('/strength/market').principal, 'owner\0');
  h.q.setQueryPrincipal('owner\0');
  assert.equal((await h.q.registryGet('/strength/market')).version, 'confirmed');
  assert.equal(h.requests.length, 0);
});

test('old principal response cannot persist during unknown state or after same principal reconfirmation', async () => {
  const h = registryHarness(); h.q.setQueryPrincipal('owner\0');
  const old = h.q.registryGet('/strength/market');
  h.q.setQueryPrincipal(null); h.q.setQueryPrincipal('owner\0');
  h.reply({ version: 'old' }); await old;
  assert.equal(h.records.size, 0);
  const fresh = h.q.registryGet('/strength/market'); h.reply({ version: 'new' }); await fresh;
  const queuedWrite = h.writes.at(-1);
  h.q.setQueryPrincipal(null);
  assert.equal(queuedWrite.isCurrent(), false);
});

test('restore waits for first identity, rejects changed identity reads, and ends on failure or timeout', async () => {
  const h = registryHarness();
  h.records.set('/earnings/upcoming', { path: '/earnings/upcoming', principal: 'visitor\0', raw: { version: 'saved' }, storedAt: 100000 });
  const restoring = h.q.restorePersistedQuery('/earnings/upcoming');
  h.q.setQueryPrincipal('visitor\0');
  assert.equal((await restoring).version, 'saved');
  h.q.setQueryPrincipal(null);
  const failed = h.q.restorePersistedQuery('/earnings/upcoming');
  h.q.setQueryPrincipal(null); assert.equal(await failed, null);
  const timedOut = h.q.restorePersistedQuery('/earnings/upcoming');
  for (const finish of [...h.env.timers.values()]) finish();
  assert.equal(await timedOut, null);
  h.q.setQueryPrincipal('visitor\0');
  const read = deferred(); h.setRead(() => read.promise);
  const stale = h.q.restorePersistedQuery('/earnings/upcoming'); await settle();
  h.q.setQueryPrincipal(null); h.q.setQueryPrincipal('owner\0');
  read.resolve(h.records.get('/earnings/upcoming'));
  assert.equal(await stale, null);
});

test('persisted database opening delay rechecks the response generation before creating a write transaction', async () => {
  let opening, transactions = 0, current = true;
  const p = load('api/persistedCache.ts', {}, { ...environment(), indexedDB: { open: () => { opening = {}; return opening; } } });
  const pending = p.writePersisted({ path: '/strength/market' }, () => current);
  current = false;
  opening.result = { transaction: () => { transactions++; throw Error('must not begin'); } };
  opening.onsuccess(); await pending;
  assert.equal(transactions, 0);
});

function accessHarness() {
  const runner = reactRunner(), h = registryHarness(), statuses = [], logins = [];
  const status = { role: 'visitor', accountUsername: null, aiEnabled: false, aiAvailable: false, aiReason: 'owner_login_required' };
  const accessApi = {
    status: () => { const d = deferred(); statuses.push(d); return d.promise; },
    login: () => { const d = deferred(); logins.push(d); return d.promise; },
    register: async () => {}, logout: async () => {},
  };
  const { AccessProvider } = load('hooks/useAccess.tsx', {
    react: runner.React, 'react/jsx-runtime': jsx, '@/api/modules/account': { accountApi: { logout: async () => {} } },
    '@/api/modules/access': { accessApi }, '@/api/client': { ApiError, PRINCIPAL_INVALID_EVENT: 'invalid' },
    '@/lib/identityRetry': { identityRetryDelayMs: () => 1000 },
    '@/api/sharedRead': { dropSharedReads: h.q.dropQueryRegistry }, '@/api/queryRegistry': h.q,
    '@/api/marketRead': { resetMarketReadState() {} }, '@/components/catalysts/api': { clearCatalystReadCache() {} }, '../i18n/core.ts': translate,
  }, h.env);
  const result = runner.mount(() => AccessProvider({ children: null }));
  return { ...h, statuses, logins, read: () => result().props.value, status, unmount: runner.unmount };
}

test('confirmed customer invalidation clears username and write permission before status recovery', async () => {
  const h = accessHarness();
  h.statuses.shift().resolve({ ...h.status, accountUsername: 'alice' }); await settle();
  assert.equal(h.read().canManageWatchlist, true);
  h.env.listeners.get('invalid')();
  assert.equal(h.read().username, null); assert.equal(h.read().isSignedIn, false);
  assert.equal(h.read().canManageWatchlist, false); assert.equal(h.read().identityUnavailable, true);
  h.statuses.shift().reject(new ApiError(503, 'identity unavailable')); await settle();
  assert.equal(h.read().isSignedIn, false);
  h.unmount();
});

test('failed login rechecks and restores original identity without treating an old probe as current', async () => {
  const h = accessHarness(); h.statuses.shift().resolve(h.status); await settle();
  const oldProbe = h.read().refresh();
  const writing = h.read().login('admin', 'wrong');
  const failed = assert.rejects(writing, /bad password/);
  h.statuses.shift().resolve({ ...h.status, role: 'owner' }); await oldProbe;
  assert.equal(h.read().role, 'visitor'); assert.equal(h.read().identityUnavailable, true);
  await h.read().refresh(); assert.equal(h.statuses.length, 0);
  h.logins.shift().reject(Error('bad password')); await settle();
  await failed; // The original write error does not await a potentially slow identity service.
  assert.equal(h.read().identityUnavailable, true);
  h.statuses.shift().resolve(h.status); await settle();
  assert.equal(h.read().identityUnavailable, false); assert.equal(h.read().role, 'visitor');
  h.unmount();
});

test('successful cookie write with failed status remains unconfirmed and cannot persist under visitor label', async () => {
  const h = accessHarness(); h.statuses.shift().resolve(h.status); await settle();
  const writing = h.read().login('admin', 'correct');
  h.logins.shift().resolve(); await settle();
  h.statuses.shift().reject(new ApiError(503, 'identity unavailable')); await writing;
  assert.equal(h.read().identityUnavailable, true);
  const market = h.q.registryGet('/strength/market'); h.reply({ version: 'owner-cookie' }); await market;
  assert.equal(h.records.size, 0);
  const confirm = h.read().refresh(); h.statuses.shift().resolve({ ...h.status, role: 'owner' }); await confirm;
  assert.equal(h.read().identityUnavailable, false);
  h.unmount();
});

test('LivePrice isolates its stateful child by normalized symbol and stale UI retains retry controls', () => {
  const imports = {
    '../../i18n/core.ts': translate, '@/hooks/useLiveQuote': {}, '@/hooks/useTickFlash': {}, '@/lib/liveQuotes': {},
    '@/lib/format': {}, '@/lib/utils': {}, './NumberTicker': {}, './ChangeBadge': {}, 'react/jsx-runtime': jsx,
  };
  const { LivePrice } = load('components/shared/LiveQuote.tsx', imports);
  const first = LivePrice({ symbol: ' aapl ', fallback: 100 });
  const same = LivePrice({ symbol: 'AAPL', fallback: 101 });
  const next = LivePrice({ symbol: 'MSFT', fallback: 200 });
  assert.equal(first.key, same.key); assert.notEqual(first.key, next.key);
  assert.equal(first.props.symbol, 'AAPL');
  assert.match(source('pages/Home.tsx'), /if \(error && !status\)/);
  assert.match(source('pages/Home.tsx'), /error && <StaleStrip onRetry=\{onRetry\}/);
  assert.match(source('pages/Watchlist.tsx'), /\{err && items.length > 0 &&/);
});
