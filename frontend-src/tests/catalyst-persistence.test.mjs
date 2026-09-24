import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

/** Minimal IndexedDB stand-in: one object store, callbacks fired as microtasks. */
function fakeIndexedDB() {
  const records = new Map();
  const puts = [];
  const request = (produce) => {
    const req = {};
    queueMicrotask(() => {
      req.result = produce();
      req.onsuccess?.();
    });
    return req;
  };
  const store = {
    put(record) { puts.push(record.key); records.set(record.key, record); },
    get(key) { return request(() => records.get(key)); },
    getAll() { return request(() => [...records.values()]); },
    delete(key) { records.delete(key); },
    clear() { records.clear(); },
  };
  const db = {
    objectStoreNames: { contains: () => true },
    close() {},
    transaction() {
      const tx = { objectStore: () => store };
      queueMicrotask(() => queueMicrotask(() => tx.oncomplete?.()));
      return tx;
    },
  };
  return { records, puts, api: { open: () => request(() => db) } };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const fake = fakeIndexedDB();
globalThis.indexedDB = fake.api;

test('a principal change clears old disk records even before the catalysts page loads', async () => {
  fake.records.set('previous-owner-feed', { key: 'previous-owner-feed', data: { private: true } });

  // AccessProvider loads resourceSignals through the API contract, but the
  // route-level resourcePersistence module has not been imported yet.
  const { notifyCatalystReadsInvalidated } = await import('../src/components/catalysts/resourceSignals.ts');
  notifyCatalystReadsInvalidated({ principalChanged: true });
  await settle();
  assert.equal(fake.records.size, 0);
});

test('a principal change clears persisted catalyst snapshots; a refresh keeps them', async () => {
  const { catalystPersistence } = await import('../src/components/catalysts/resourcePersistence.ts');
  const { notifyCatalystReadsInvalidated } = await import('../src/components/catalysts/resourceSignals.ts');
  const now = Date.now();
  await catalystPersistence.write({ key: 'owner-feed', data: { items: [] }, validatedAt: now, expiresAt: now + 60_000 });
  assert.equal(fake.records.size, 1);

  notifyCatalystReadsInvalidated({ userInitiated: true });
  await settle();
  assert.equal(fake.records.size, 1);

  notifyCatalystReadsInvalidated({ userInitiated: true, principalChanged: true });
  await settle();
  assert.equal(fake.records.size, 0);
});

test('a write waiting for IndexedDB cannot put an old-principal snapshot after clear starts', async () => {
  const { catalystPersistence } = await import('../src/components/catalysts/resourcePersistence.ts');
  const before = fake.puts.length;
  const now = Date.now();
  const write = catalystPersistence.write({
    key: 'old-principal-late-write', data: { private: true },
    validatedAt: now, expiresAt: now + 60_000,
  });
  const clear = catalystPersistence.clear();
  await Promise.all([write, clear]);
  assert.equal(fake.puts.length, before);
  assert.equal(fake.records.has('old-principal-late-write'), false);
});

test('principal invalidation retires old catalyst reads without starting a new old-key read', async () => {
  const source = readFileSync(new URL('../src/components/catalysts/resourceRuntime.ts', import.meta.url), 'utf8');
  const start = source.indexOf('onCatalystReadsInvalidated((options) => {');
  const end = source.indexOf('\n});', start) + '\n});'.length;
  assert.ok(start >= 0 && end > start);
  const code = ts.transpileModule(source.slice(start, end), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const observed = { invalidations: 0, reads: 0 };
  let principalGeneration = 1;
  let notify = () => {};
  vm.runInNewContext(code, {
    onCatalystReadsInvalidated(listener) { notify = listener; },
    catalystResources: {
      invalidate() { observed.invalidations += 1; },
      tick() { observed.reads += 1; },
    },
    visible: () => true,
    getQueryPrincipalGeneration: () => principalGeneration,
    queueMicrotask,
  });

  notify({ principalChanged: true });
  await Promise.resolve();
  assert.deepEqual(observed, { invalidations: 1, reads: 0 });
  notify({ userInitiated: true });
  await Promise.resolve();
  assert.deepEqual(observed, { invalidations: 2, reads: 1 });

  notify({ userInitiated: true });
  principalGeneration += 1;
  notify({ principalChanged: true });
  await Promise.resolve();
  assert.deepEqual(observed, { invalidations: 4, reads: 1 }, 'queued old-principal refresh must be retired');
});
