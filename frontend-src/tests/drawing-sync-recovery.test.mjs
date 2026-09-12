import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { build } from 'esbuild';
import { createReactStub } from './helpers/react-hooks.mjs';

const settle = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const sample = () => ({ schemaVersion: 1, id: '11111111-1111-4111-8111-111111111111', ticker: 'AAPL', range: '1d', adjustment: 'raw',
  kind: 'horizontal', anchors: [{ time: "2026-09-10T00:00:00Z", barKey: "2026-09-10", price: 100 }], style: { color: '#2E46E0', width: 3, dash: 'solid' },
  locked: false, hidden: false, zOrder: 1, revision: 1, createdAt: '2026-09-12T00:00:00Z', updatedAt: '2026-09-12T00:00:00Z' });

async function harness(t) {
  const stub = createReactStub();
  stub.React.useLayoutEffect = stub.React.useEffect;
  stub.React.useMemo = (factory, deps) => stub.React.useCallback(factory, deps)();
  const reads = [], writes = [], updates = [], removals = [], creations = [], timers = new Map(), storage = new Map();
  let timerId = 0, server = sample(), revision = 1, deferUpdates = false;
  const api = {
    remove: (id, expectedScopeRevision) => {
      removals.push({ id, expectedScopeRevision });
      assert.equal(expectedScopeRevision, revision);
      return Promise.resolve({ scopeRevision: ++revision });
    },
    create: (drawing, expectedScopeRevision) => {
      creations.push({ drawing, expectedScopeRevision });
      assert.equal(expectedScopeRevision, revision);
      return Promise.resolve({ drawing, scopeRevision: ++revision });
    },
    list: () => new Promise((resolve, reject) => reads.push({ resolve, reject })),
    update: (drawing, expectedScopeRevision) => {
      writes.push({ drawing, expectedScopeRevision, actualScopeRevision: revision, actualDrawingRevision: server.revision });
      assert.equal(expectedScopeRevision, revision, 'write must use the latest server scope revision');
      assert.equal(drawing.revision, server.revision, 'write must use the latest drawing revision');
      const complete = () => {
        server = { ...drawing, revision: server.revision + 1 }; revision++;
        return { drawing: server, scopeRevision: revision };
      };
      return deferUpdates ? new Promise(resolve => updates.push(() => resolve(complete()))) : Promise.resolve(complete());
    },
  };
  const bundled = await build({ entryPoints: [new URL('../src/components/detail/chart-drawings/useDrawingController.ts', import.meta.url).pathname],
    bundle: true, write: false, format: 'cjs', platform: 'node', plugins: [{ name: 'test-boundaries', setup(build) {
      build.onResolve({ filter: /^(react|\.\/api\.ts|.*useAppearance\.ts)$/ }, args => ({ path: args.path, external: true }));
    } }] });
  const module = { exports: {} };
  vm.runInNewContext(bundled.outputFiles[0].text, { module, exports: module.exports, console,
    require: id => id === 'react' ? stub.React : id.endsWith('useAppearance.ts') ? { useAppearance: () => 'light' }
      : { drawingsApi: api, drawingErrorCode: error => error.bizCode ?? null, drawingErrorStatus: error => error.status ?? null },
    window: { addEventListener() {}, removeEventListener() {} },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    setTimeout: (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout: id => timers.delete(id),
  });
  const args = { ticker: 'AAPL', range: '1d', bars: undefined, chart: null,
    identity: { signedIn: true, key: 'owner' }, measureActive: false, onCancelMeasure() {} };
  const read = stub.mount(() => module.exports.useDrawingController(args));
  t.after(() => {
    stub.unmount();
    // drain catches API errors; also assert here so an incorrect revision cannot
    // be mistaken for a deliberately retryable server failure by this harness.
    for (const write of writes) {
      assert.equal(write.expectedScopeRevision, write.actualScopeRevision);
      assert.equal(write.drawing.revision, write.actualDrawingRevision);
    }
  });
  const answer = async (request = reads.shift(), drawing = server, scopeRevision = revision) => {
    request.resolve({ drawings: [{ ...drawing }], scopeRevision }); await settle();
  };
  await settle(); await answer();
  const flushEdit = async () => {
    for (const [id, timer] of timers) if (timer.delay === 400) { timers.delete(id); timer.fn(); }
    await settle();
  };
  const failReconcile = async () => {
    reads.shift().reject({ status: 429, retryAfter: 1 }); await settle();
    assert.equal(read().syncStatus, 'load_failed');
  };
  return { read, reads, writes, updates, removals, creations, timers, args, rerender: stub.rerender, answer, flushEdit, failReconcile,
    dropCache: () => storage.clear(),
    externalChange: drawings => {
      server = drawings.find(drawing => drawing.id === sample().id) ?? server;
      revision++;
    },
    deferUpdates: () => { deferUpdates = true; },
    server: () => ({ ...server }), revision: () => revision };
}

test('same-scope recovery after a successful lock and rate-limited reconcile preserves undo', async t => {
  const h = await harness(t);
  h.read().patchDrawing('11111111-1111-4111-8111-111111111111', { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  assert.equal(h.read().canUndo, true);
  h.read().retry(); await h.answer();
  assert.equal(h.read().canUndo, true, 'recovering synchronization must retain the lock undo entry');
  h.read().undo(); await settle();
  assert.equal(h.read().drawings[0].locked, false);
  assert.equal(h.writes.at(-1).drawing.locked, false);
});

test('a delayed recovery cannot overwrite an edit or rebase below a newer completed write', async t => {
  const h = await harness(t);
  h.read().patchDrawing('11111111-1111-4111-8111-111111111111', { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  h.read().retry(); const oldRead = h.reads.shift(), oldServer = h.server(), oldRevision = h.revision();
  h.read().patchDrawing('11111111-1111-4111-8111-111111111111', { hidden: true }, true, { coalesce: false });
  await h.flushEdit();
  await h.answer(); // The newer edit's successful reconciliation.
  await h.answer(oldRead, oldServer, oldRevision);
  assert.equal(h.read().drawings[0].hidden, true, 'old GET must not replace the newer edit');
  if (h.reads.length) await h.answer(); // Recovery retries a read invalidated by editing.
  h.read().undo(); await settle();
  assert.equal(h.read().drawings[0].hidden, false);
  assert.equal(h.read().drawings[0].locked, true);
  assert.equal(h.writes.at(-1).drawing.hidden, false);
});

test('changing drawing scope still resets the undo session', async t => {
  const h = await harness(t);
  h.read().patchDrawing('11111111-1111-4111-8111-111111111111', { locked: true }, false);
  assert.equal(h.read().canUndo, true);
  h.args.ticker = 'MSFT'; h.rerender(); await settle();
  await h.answer(h.reads.shift(), { ...sample(), ticker: 'MSFT' });
  assert.equal(h.read().canUndo, false);
  assert.equal(h.read().drawings[0].ticker, 'MSFT');
});

test('changing the signed-in principal still resets the undo session', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, false);
  assert.equal(h.read().canUndo, true);
  h.args.identity = { signedIn: true, key: 'account:alice' }; h.rerender(); await settle();
  await h.answer();
  assert.equal(h.read().canUndo, false);
  assert.equal(h.read().drawings[0].locked, false);
});

test('explicitly choosing the server version still resets the undo session', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, false);
  const taking = h.read().takeServerConflict();
  await h.answer(); await taking;
  assert.equal(h.read().canUndo, false);
  assert.equal(h.read().drawings[0].locked, false);
});

test('recovery includes an edit still inside the save debounce window', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  h.read().patchDrawing(sample().id, { hidden: true }, true, { coalesce: false });
  h.read().retry(); await h.answer();
  assert.equal(h.read().drawings[0].hidden, true);
  assert.equal(h.writes.at(-1).drawing.hidden, true);
  assert.equal(h.read().canUndo, true);
});

test('an undo during recovery invalidates the older read before it changes send revisions', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  h.read().retry(); const oldRead = h.reads.shift(), oldServer = h.server(), oldRevision = h.revision();
  h.read().undo(); await settle(); await h.answer();
  await h.answer(oldRead, oldServer, oldRevision);
  if (h.reads.length) await h.answer();
  assert.equal(h.read().drawings[0].locked, false);
  assert.equal(h.read().canRedo, true);
  h.read().redo(); await settle();
  assert.equal(h.writes.at(-1).drawing.locked, true);
});

test('a save and an older recovery GET resolving together cannot roll back server revisions', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  h.read().retry(); const firstRead = h.reads.shift();
  h.read().patchDrawing(sample().id, { hidden: true }, true, { coalesce: false });
  h.deferUpdates(); await h.flushEdit();
  // The edit predates this GET, so stateVersion alone is unchanged when both
  // network promises resolve. drain rebases the outbox before writeLocal runs.
  await h.answer(firstRead);
  const oldRead = h.reads.shift(), oldServer = h.server(), oldRevision = h.revision();
  h.updates.shift()();
  oldRead.resolve({ drawings: [oldServer], scopeRevision: oldRevision });
  await settle();
  assert.equal(h.read().drawings[0].hidden, true);
  while (h.reads.length) await h.answer();
  h.read().undo(); await settle();
  assert.equal(h.writes.at(-1).expectedScopeRevision, h.revision());
});

test('a rate-limited recovery invalidated by editing still honors Retry-After', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  h.read().retry(); const oldRead = h.reads.shift();
  h.read().patchDrawing(sample().id, { hidden: true }, true);
  oldRead.reject({ status: 429, retryAfter: 30 }); await settle();
  assert.equal(h.reads.length, 0, 'must not immediately issue another rate-limited GET');
  assert.ok([...h.timers.values()].some(timer => timer.delay >= 30_000));
  assert.equal(h.read().drawings[0].hidden, true);
});

const externalB = () => ({ ...sample(), id: '22222222-2222-4222-8222-222222222222' });
for (const [change, changedDrawings] of [
  ['addition', drawing => [drawing, externalB()]],
  ['deletion', () => []],
  ['price modification', drawing => [{ ...drawing, anchors: [{ ...drawing.anchors[0], price: 120 }], revision: drawing.revision + 1 }]],
]) {
  test(`adopting an external ${change} invalidates undo instead of changing another page's work`, async t => {
    const h = await harness(t);
    h.read().patchDrawing(sample().id, { locked: true }, true);
    await h.flushEdit(); await h.failReconcile();
    const remote = changedDrawings(h.server());
    h.externalChange(remote);
    h.read().retry();
    h.reads.shift().resolve({ drawings: remote, scopeRevision: h.revision() }); await settle();
    const adopted = JSON.stringify(h.read().drawings), writesBeforeUndo = h.writes.length;
    h.read().undo(); await settle();
    assert.equal(h.removals.length, 0, 'old undo must not delete externally added objects');
    assert.equal(h.creations.length, 0, 'old undo must not restore externally deleted objects');
    assert.equal(h.writes.length, writesBeforeUndo, 'old undo must not overwrite an external field change');
    assert.equal(JSON.stringify(h.read().drawings), adopted);
    assert.equal(h.read().canUndo, false);
  });
}

test('a recovery that only updates server revisions and timestamps retains undo', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  const sameContent = { ...h.server(), revision: 9, createdAt: '2026-09-11T00:00:00Z', updatedAt: '2026-09-12T01:00:00Z' };
  h.externalChange([sameContent]);
  h.read().retry(); await h.answer();
  assert.equal(h.read().canUndo, true);
  h.read().undo(); await settle();
  assert.equal(h.read().drawings[0].locked, false);
  assert.equal(h.writes.at(-1).drawing.revision, 9);
});

test('a safe replay that also adopts an external addition invalidates the earlier undo snapshots', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  h.read().patchDrawing(sample().id, { hidden: true }, true, { coalesce: false });
  const remote = [h.server(), externalB()];
  h.externalChange(remote);
  h.read().retry();
  h.reads.shift().resolve({ drawings: remote, scopeRevision: h.revision() }); await settle();
  assert.equal(h.read().drawings.length, 2);
  assert.equal(h.read().drawings.find(drawing => drawing.id === sample().id).hidden, true);
  h.read().undo(); await settle();
  assert.equal(h.removals.length, 0, 'safe replay is not permission to keep snapshots missing B');
  assert.equal(h.read().canUndo, false);
});

test('another failed recovery with a missing cache preserves current drawings and undo', async t => {
  const h = await harness(t);
  h.read().patchDrawing(sample().id, { locked: true }, true);
  await h.flushEdit(); await h.failReconcile();
  h.dropCache(); h.read().retry();
  h.reads.shift().reject({ status: 429, retryAfter: 30 }); await settle();
  assert.equal(h.read().drawings.length, 1, 'failed GET cannot treat a missing cache as a remote deletion');
  assert.equal(h.read().drawings[0].locked, true);
  assert.equal(h.read().canUndo, true);
});
