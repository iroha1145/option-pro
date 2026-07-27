/**
 * IndexedDB 持久层:公开研究快照的"上一份真实响应"。
 *
 * 目标是打开页面先恢复上次的真实数据(带数据截至时间),再在后台发条件请求
 * 确认是否有更新 —— 而不是每次都白屏等一个 4MB 的财报响应。
 *
 * 只进白名单的公开快照(见 queryRegistry 的 PERSIST 配置);会话密钥、账号
 * 私有数据一律不落盘。记录里存 principal 与 appCommit,读取时不匹配即弃用,
 * 主体切换时整库清空 —— 登录前的数据不会泄给登录后的主体,反之亦然。
 */

const DB_NAME = 'optix-reads-v1';
const STORE = 'responses';

export interface PersistedResponse {
  path: string;
  principal: string;
  appCommit: string | null;
  etag: string | null;
  /** 原始响应 JSON(mapper 之前的形状,与 locale 无关)。 */
  raw: unknown;
  storedAt: number;
}

function openDb(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === 'undefined') return Promise.resolve(null);
  return new Promise((resolve) => {
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DB_NAME, 1);
    } catch {
      resolve(null);
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'path' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });
}

let dbPromise: Promise<IDBDatabase | null> | null = null;

function db(): Promise<IDBDatabase | null> {
  if (!dbPromise) dbPromise = openDb();
  return dbPromise;
}

export async function readPersisted(path: string): Promise<PersistedResponse | null> {
  const handle = await db();
  if (!handle) return null;
  return new Promise((resolve) => {
    try {
      const tx = handle.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).get(path);
      req.onsuccess = () => resolve((req.result as PersistedResponse | undefined) ?? null);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

export async function writePersisted(record: PersistedResponse): Promise<void> {
  const handle = await db();
  if (!handle) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = handle.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(record);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    } catch {
      resolve();
    }
  });
}

export async function deletePersisted(path: string): Promise<void> {
  const handle = await db();
  if (!handle) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = handle.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(path);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    } catch {
      resolve();
    }
  });
}

/** 主体切换(登录/登出/切账号)时整库清空:不同主体不共享持久响应。 */
export async function clearPersisted(): Promise<void> {
  const handle = await db();
  if (!handle) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = handle.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    } catch {
      resolve();
    }
  });
}
