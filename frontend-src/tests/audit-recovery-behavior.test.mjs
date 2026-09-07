import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import { ApiError } from '../src/api/client.ts';
import { createReactStub } from './helpers/react-hooks.mjs';

const settle = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
function compile(file, React, imports, environment = {}) {
  const module = { exports: {} };
  const source = fs.readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, Error, TypeError, Promise, Date,
    require(id) {
      if (id === 'react') return React;
      if (id === 'react/jsx-runtime') return { jsx: (_type, props) => props.value };
      if (id.includes('i18n/core')) return { t: value => value };
      if (id in imports) return imports[id];
      throw new Error(`Unexpected import: ${id}`);
    }, ...environment });
  return module.exports;
}
function timers() {
  let serial = 0; let elapsed = 0; const queued = new Map();
  return { queued, now: () => elapsed,
    setTimeout(fn, delay) { const id = ++serial; queued.set(id, { fn, delay }); return id; },
    clearTimeout(id) { queued.delete(id); },
    async next() { const [id, next] = queued.entries().next().value; queued.delete(id); elapsed += next.delay; await next.fn(); await settle(); return next.delay; },
  };
}
function identityHarness() {
  const stub = createReactStub();
  stub.React.createContext = () => ({ Provider: 'Provider' });
  stub.React.useMemo = (factory, deps) => stub.React.useCallback(factory, deps)();
  const requests = []; const clock = timers();
  const { AccessProvider } = compile('hooks/useAccess.tsx', stub.React, {
    '@/api/modules/access': { accessApi: { status: () => new Promise((resolve, reject) => requests.push({ resolve, reject })) } },
    '@/api/modules/account': { accountApi: {} },
    '@/api/client': { ApiError, PRINCIPAL_INVALID_EVENT: 'invalid' },
    '@/lib/identityRetry': { identityRetryDelayMs: () => 1000 },
    '@/api/sharedRead': { dropSharedReads() {} },
    '@/api/queryRegistry': { setQueryPrincipal() {} },
    '@/api/marketRead': { resetMarketReadState() {} },
    '@/components/catalysts/api': { clearCatalystReadCache() {} },
  }, { window: { ...clock, setInterval: () => 1, clearInterval() {}, addEventListener() {}, removeEventListener() {} }, document: { addEventListener() {}, removeEventListener() {} } });
  return { ...stub, requests, clock, read: stub.mount(() => AccessProvider({ children: null })) };
}
const alice = { role: 'visitor', aiEnabled: false, aiAvailable: false, aiReason: 'owner_login_required', accountUsername: 'alice' };
const visitor = { ...alice, accountUsername: null };

test('a late ordinary identity probe cannot restore the account after a newer signed-out result', async () => {
  const h = identityHarness(); h.requests[0].resolve(alice); await settle();
  const older = h.read().refresh(); const newer = h.read().refresh();
  h.requests[2].resolve(visitor); await newer; await settle();
  assert.equal(h.read().username, null);
  h.requests[1].resolve(alice); await older; await settle();
  assert.equal(h.read().username, null); assert.equal(h.read().canManageWatchlist, false);
  h.unmount();
});

test('an obsolete identity failure cannot mark a newer successful identity unavailable or schedule retries', async () => {
  const h = identityHarness(); h.requests[0].resolve(alice); await settle();
  const older = h.read().refresh(); const newer = h.read().refresh();
  h.requests[2].resolve(visitor); await newer;
  h.requests[1].reject(new ApiError(503, 'old failure')); await older; await settle();
  assert.equal(h.read().identityUnavailable, false); assert.equal(h.clock.queued.size, 0);
  h.unmount();
});

function jobHarness(respond) {
  const stub = createReactStub(); const clock = timers(); const queries = []; let creates = 0;
  const { useAiJob } = compile('components/detail/useAiJob.ts', stub.React, {
    '@/api/client': { ApiError },
    '@/api/modules/ai-jobs': { aiJobsApi: { get: id => { queries.push(id); return respond(id); }, cancel: async id => ({ id, status: 'cancelled' }) } },
  }, { ...clock, Date: class extends Date { static now() { return clock.now(); } }, document: { hidden: false } });
  const read = stub.mount(() => useAiJob());
  return { ...stub, clock, queries, read, creates: () => creates,
    start: () => read().start(async () => { creates++; return { id: 'paid-1', status: 'queued', progress: null }; }) };
}

test('temporary polling failure retries the same paid task and recovers its completed result', async () => {
  let calls = 0;
  const h = jobHarness(async id => { if (++calls === 1) throw new ApiError(503, 'temporary'); return { id, status: 'succeeded', result: { summary: 'done' } }; });
  await h.start(); await h.clock.next();
  assert.equal(h.read().queryIssue, 'retrying'); assert.equal(h.read().job.id, 'paid-1');
  await h.start(); assert.equal(h.creates(), 1, 'query retry must not create a second paid task');
  await h.clock.next();
  assert.equal(h.read().job.status, 'succeeded'); assert.equal(h.read().error, null);
  assert.deepEqual(h.queries, ['paid-1', 'paid-1']); assert.equal(h.clock.queued.size, 0);
  h.unmount();
});

test('consecutive transport failures pause with the original job intact and resume queries without creating', async () => {
  let failing = true;
  const h = jobHarness(async id => { if (failing) throw new TypeError('network'); return { id, status: 'succeeded' }; });
  await h.start(); for (let i = 0; i < 5; i++) await h.clock.next();
  assert.equal(h.read().queryIssue, 'paused'); assert.equal(h.clock.queued.size, 0);
  h.read().reset(); assert.equal(h.read().job.id, 'paid-1', 'reset must not forget an active paid task');
  failing = false; h.read().resume(); await h.clock.next();
  assert.equal(h.read().job.status, 'succeeded'); assert.equal(h.creates(), 1);
  h.unmount();
});

test('401, 403, 404 and non-retryable server failures never enter automatic task retries', async () => {
  for (const code of [401, 403, 404, 502]) {
    const h = jobHarness(async () => { throw new ApiError(code, 'blocked', code === 502 ? { retryable: false } : undefined); });
    await h.start(); await h.clock.next();
    assert.equal(h.read().queryIssue, 'blocked'); assert.equal(h.clock.queued.size, 0); assert.equal(h.queries.length, 1);
    h.unmount();
  }
});

test('task retry respects Retry-After and healthy long-running tasks are not abandoned after five minutes', async () => {
  let calls = 0;
  const h = jobHarness(async id => { if (++calls === 1) throw new ApiError(429, 'slow down', { retryAfter: 30 }); return { id, status: 'in_progress' }; });
  await h.start(); await h.clock.next(); assert.equal(await h.clock.next(), 30000);
  for (let i = 0; i < 50; i++) await h.clock.next();
  assert.equal(h.read().job.status, 'in_progress'); assert.equal(h.read().queryIssue, null); assert.equal(h.clock.queued.size, 1);
  h.unmount(); assert.equal(h.clock.queued.size, 0);
});

test('a retained start callback cannot create another paid job while the previous job remains active', async () => {
  const h = jobHarness(async id => ({ id, status: 'in_progress' }));
  const oldStart = h.read().start; let creates = 0;
  const create = async () => { creates++; return { id: 'paid-fixed-id', status: 'queued' }; };
  await oldStart(create); await oldStart(create);
  assert.equal(creates, 1); assert.equal(h.read().job.id, 'paid-fixed-id');
  h.unmount();
});

test('an authorization failure can be explicitly resumed after login while preserving the job ID', async () => {
  let authorized = false;
  const h = jobHarness(async id => { if (!authorized) throw new ApiError(401, 'login required'); return { id, status: 'succeeded' }; });
  await h.start(); await h.clock.next(); assert.equal(h.clock.queued.size, 0);
  authorized = true; h.read().resume(); await h.clock.next();
  assert.equal(h.read().job.status, 'succeeded'); assert.deepEqual(h.queries, ['paid-1', 'paid-1']); assert.equal(h.creates(), 1);
  h.unmount();
});
