import test from 'node:test';
import assert from 'node:assert/strict';

/** Minimal IndexedDB stand-in: one object store, callbacks fired as microtasks. */
function fakeIndexedDB() {
  const records = new Map();
  const request = (produce) => {
    const req = {};
    queueMicrotask(() => {
      req.result = produce();
      req.onsuccess?.();
    });
    return req;
  };
  const store = {
    put(record) { records.set(record.key, record); },
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
  return { records, api: { open: () => request(() => db) } };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test('a principal change clears persisted catalyst snapshots; a refresh keeps them', async () => {
  const fake = fakeIndexedDB();
  globalThis.indexedDB = fake.api;
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
