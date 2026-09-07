import type { ResourcePersistence, StoredResource } from './resourceCache';

const DB = 'optix-catalysts-v1';
const STORE = 'resources';
const MAX_RECORDS = 48;
const MAX_RECORD_BYTES = 1_500_000;
let opening: Promise<IDBDatabase | null> | null = null;
function open(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null);
  if (opening) return opening;
  opening = new Promise((resolve) => {
    try {
      const request = indexedDB.open(DB, 1);
      let settled = false;
      const finish = (value: IDBDatabase | null) => {
        if (settled) { value?.close(); return; }
        settled = true;
        clearTimeout(timer);
        resolve(value);
      };
      const timer = setTimeout(() => finish(null), 600);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE)) {
          request.result.createObjectStore(STORE, { keyPath: 'key' });
        }
      };
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => { db.close(); opening = null; };
        finish(db);
      };
      request.onerror = request.onblocked = () => finish(null);
    } catch { resolve(null); }
  });
  return opening;
}

export const catalystPersistence: ResourcePersistence = {
  async read(key) {
    const db = await open();
    if (!db) return null;
    return new Promise((resolve) => {
      try {
        const request = db.transaction(STORE, 'readonly').objectStore(STORE).get(key);
        request.onsuccess = () => {
          const record = request.result as StoredResource | undefined;
          resolve(record && record.key === key && Number.isFinite(record.validatedAt)
            && Number.isFinite(record.expiresAt) && record.data !== undefined ? record : null);
        };
        request.onerror = () => resolve(null);
      } catch { resolve(null); }
    });
  },
  async write(record) {
    // No unbounded feed archive in a browser; old pages remain on the server.
    if (JSON.stringify(record).length * 2 > MAX_RECORD_BYTES) return;
    const db = await open();
    if (!db) return;
    await new Promise<void>((resolve) => {
      try {
        const tx = db.transaction(STORE, 'readwrite');
        const store = tx.objectStore(STORE);
        store.put(record);
        const all = store.getAll();
        all.onsuccess = () => {
          const rows = (all.result as StoredResource[])
            .sort((a, b) => b.validatedAt - a.validatedAt);
          rows.forEach((row, i) => {
            if (row.expiresAt <= Date.now() || i >= MAX_RECORDS) store.delete(row.key);
          });
        };
        tx.oncomplete = tx.onerror = tx.onabort = () => resolve();
      } catch { resolve(); }
    });
  },
  async remove(key) {
    const db = await open();
    if (!db) return;
    await new Promise<void>((resolve) => {
      try {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).delete(key);
        tx.oncomplete = tx.onerror = tx.onabort = () => resolve();
      } catch { resolve(); }
    });
  },
};
