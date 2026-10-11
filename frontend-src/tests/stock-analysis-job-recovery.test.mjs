import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source = fs.readFileSync(new URL('../src/components/detail/useAiJob.ts', import.meta.url), 'utf8');
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const job = (id, status = 'queued') => ({ id, status, kind: 'signal-analysis', progress: null, createdAt: '', updatedAt: '' });
/** Tiny synchronous hook host; async transport and scheduled polls remain real hook code. */
function harness(api) {
  let cursor = 0, current;
  const slots = [], effects = [], timers = new Map(); let timerId = 0;
  const original = { document: globalThis.document, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
  globalThis.setTimeout = (fn, wait) => { const id = ++timerId; timers.set(id, { fn, wait }); return id; };
  globalThis.clearTimeout = id => timers.delete(id);
  const changed = (previous, next) => !previous || previous.length !== next.length || next.some((v, i) => !Object.is(v, previous[i]));
  const react = {
    useState(initial) { const index = cursor++; if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial; return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }]; },
    useRef(value) { const index = cursor++; return slots[index] ??= { current: value }; },
    useCallback(fn, deps) { const index = cursor++; if (!slots[index] || changed(slots[index].deps, deps)) slots[index] = { fn, deps }; return slots[index].fn; },
    useEffect(fn, deps) { const index = cursor++; if (!slots[index] || changed(slots[index].deps, deps)) { const old = slots[index]; slots[index] = { deps, cleanup: old?.cleanup }; effects.push(() => { old?.cleanup?.(); slots[index].cleanup = fn(); }); } },
  };
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  const imports = { react, '@/api/modules/ai-jobs': { aiJobsApi: api }, '@/api/aiJobNormalize': { aiJobCreateErrorMessage: err => String(err) }, '@/api/client': { ApiError: class extends Error {} }, '@/lib/boundedReadRetry': { AI_JOB_POLL_WAITS_MS: [2000, 3000, 5000] }, '@/lib/retryDelay': { retryDelayMs: (_attempt, retry, ladder) => retry ? retry * 1000 : ladder.ladderMs[0] }, '../../i18n/core.ts': { t: key => key } };
  new Function('module','exports','require',compiled)(module, module.exports, id => { if (!(id in imports)) throw new Error(id); return imports[id]; });
  const render = () => { cursor = 0; current = module.exports.useAiJob(); while (effects.length) effects.shift()(); return current; };
  render();
  return { render, get current() { return current; }, timers,
    async tick() { const [id, timer] = timers.entries().next().value ?? []; assert.ok(timer, 'a poll should be scheduled'); timers.delete(id); await timer.fn(); await Promise.resolve(); return render(); },
    close() { for (const slot of slots) slot?.cleanup?.(); globalThis.document = original.document; globalThis.setTimeout = original.setTimeout; globalThis.clearTimeout = original.clearTimeout; },
  };
}
test('adopting an active saved task polls the original id and never creates a second task', async () => {
  const ids = []; const h = harness({ get: async id => { ids.push(id); return job(id, 'succeeded'); }, cancel: async id => job(id, 'cancelled') });
  try {
    h.current.adopt(job('shared-active')); h.render();
    assert.equal(h.current.job.id, 'shared-active'); assert.equal(h.timers.size, 1);
    let created = 0; await h.current.start(async () => { created++; return job('duplicate'); });
    assert.equal(created, 0);
    await h.tick(); assert.deepEqual(ids, ['shared-active']); assert.equal(h.current.job.status, 'succeeded'); assert.equal(h.timers.size, 0);
  } finally { h.close(); }
});
test('clearing an identity ignores an outstanding old poll response', async () => {
  const response = deferred(); const h = harness({ get: () => response.promise });
  try {
    h.current.adopt(job('old-account')); h.render();
    const ticking = h.tick(); await Promise.resolve();
    h.current.clear(); h.render();
    response.resolve(job('old-account', 'succeeded')); await ticking;
    assert.equal(h.current.job, null); assert.equal(h.timers.size, 0);
    h.current.adopt(job('new-account', 'succeeded')); h.render();
    assert.equal(h.current.job.id, 'new-account');
  } finally { h.close(); }
});
test('clearing during POST prevents old creation from appearing or clearing a new submission latch', async () => {
  const old = deferred(), next = deferred(); const h = harness({ get: async id => job(id) });
  try {
    const first = h.current.start(() => old.promise); h.render(); assert.equal(h.current.starting, true);
    h.current.clear(); h.render(); assert.equal(h.current.starting, false);
    const second = h.current.start(() => next.promise); h.render();
    old.resolve(job('old-job')); await first; h.render(); assert.equal(h.current.job, null); assert.equal(h.current.starting, true);
    next.resolve(job('new-job')); await second; h.render(); assert.equal(h.current.job.id, 'new-job'); assert.equal(h.current.starting, false);
  } finally { h.close(); }
});
test('reading a completed report starts no polling, and reset cannot forget an active paid task', () => {
  const h = harness({ get: async id => job(id) });
  try {
    h.current.adopt(job('saved', 'succeeded')); h.render(); assert.equal(h.timers.size, 0);
    h.current.adopt(job('running')); h.render(); h.current.reset(); h.render(); assert.equal(h.current.job.id, 'running');
  } finally { h.close(); }
});
