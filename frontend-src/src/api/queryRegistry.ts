/**
 * 共享查询注册表:公开只读端点的统一读取层。
 *
 * usePolling / sharedRead / 各模块裸 get 各自解决了一部分问题,却互不知情:
 * 同一接口被多个组件重复请求、身份切换只清了其中一层、刷新后一切从零。这里
 * 把"原始响应层"的共享收敛到一个注册表:
 *
 * - 同一路径同一时刻只有一个 in-flight 请求(并发合流);
 * - fresh 窗口内直接复用内存值(多个 60s 轮询打同一路径 ≈ 单计时器效果);
 * - 白名单路径把上一份真实响应持久化到 IndexedDB,冷启动先恢复再后台
 *   用 If-None-Match 条件请求确认,304 时不重复下载正文;
 * - 手动刷新按路径前缀精确失效,失效瞬间世代 +1,旧在途响应不得回写;
 * - 登录/登出/切账号由 useAccess 调 dropQueryRegistry() 全清(含持久层)。
 *
 * 共享出去的对象视为只读 —— 与 sharedRead 的既有约定一致,mapper 只读字段
 * 并另建对象。
 */

import { requestRaw } from './client.ts';
import {
  clearPersisted,
  deletePersisted,
  readPersisted,
  writePersisted,
} from './persistedCache.ts';

interface QueryConfig {
  /** fresh 窗口:窗口内重复调用直接回内存值,不发请求。 */
  ttlMs: number;
  /** 是否把原始响应持久化到 IndexedDB(仅公开研究快照)。 */
  persist?: boolean;
}

/**
 * 注册表白名单:全局、只读、同一身份下返回同一份数据的端点。
 * 按用户/参数变化的接口不进来;身份差异靠 dropQueryRegistry 在主体
 * 切换时全清覆盖(与 sharedRead 的既有约定一致)。
 */
const QUERY_CONFIG: Record<string, QueryConfig> = {
  '/market/status': { ttlMs: 15_000 },
  '/market/indices': { ttlMs: 30_000, persist: true },
  '/strength/market': { ttlMs: 30_000, persist: true },
  '/signals/market': { ttlMs: 30_000, persist: true },
  '/earnings/upcoming': { ttlMs: 60_000, persist: true },
  '/sectors': { ttlMs: 120_000, persist: true },
  '/macro/conditions': { ttlMs: 60_000, persist: true },
  '/breakouts/status': { ttlMs: 10_000 },
  '/breakouts/current': { ttlMs: 10_000 },
};

interface Entry {
  inFlight: Promise<unknown> | null;
  value: unknown;
  etag: string | null;
  fetchedAt: number;
  generation: number;
  restored: boolean;
  /** 硬失效后下一次请求绕过浏览器 HTTP 缓存（cache:'reload'），用后即清。 */
  forceReload: boolean;
}

const entries = new Map<string, Entry>();
let principalKey = 'anonymous';
let knownAppCommit: string | null = null;

function entryFor(path: string): Entry {
  let entry = entries.get(path);
  if (!entry) {
    entry = {
      inFlight: null,
      value: undefined,
      etag: null,
      fetchedAt: 0,
      generation: 0,
      restored: false,
      forceReload: false,
    };
    entries.set(path, entry);
  }
  return entry;
}

export function queryConfigFor(path: string): QueryConfig | null {
  return QUERY_CONFIG[path] ?? null;
}

/** useAccess 在身份确定/变化时调用;持久层记录按 principal 校验。 */
export function setQueryPrincipal(key: string): void {
  principalKey = key;
}

async function fetchInto(path: string, entry: Entry, config: QueryConfig): Promise<unknown> {
  const generation = entry.generation;
  /* 硬失效后的第一发必须打穿浏览器 HTTP 缓存：后端给 max-age=60，普通
     GET 在窗口内可能整个不出浏览器就拿回刷新前的旧正文（审计 P1-01
     场景二）。标记一次性使用；即便这发失败，重试退回条件请求也可接受。 */
  const reload = entry.forceReload;
  if (reload) entry.forceReload = false;
  const conditional = reload ? null : entry.etag;
  const res = await requestRaw(path, {
    method: 'GET',
    ...(reload ? { cache: 'reload' as RequestCache } : {}),
    acceptNotModified: Boolean(conditional),
    headers: conditional ? { 'If-None-Match': conditional } : {},
  });
  const commit = res.headers.get('X-App-Commit');
  if (commit) knownAppCommit = commit;
  if (res.status === 304) {
    // 数据没变:正文零下载,只把这份值确认成"刚验证过"。
    if (entry.generation === generation) {
      entry.fetchedAt = Date.now();
    }
    return entry.value;
  }
  const data: unknown = res.status === 204 ? undefined : await res.json();
  if (entry.generation !== generation) {
    // 手动刷新已作废这一世代:旧在途响应不得回写缓存,原调用方照常拿到它。
    return data;
  }
  entry.value = data;
  entry.etag = res.headers.get('ETag');
  entry.fetchedAt = Date.now();
  if (config.persist) {
    void writePersisted({
      path,
      principal: principalKey,
      appCommit: knownAppCommit,
      etag: entry.etag,
      raw: data,
      storedAt: Date.now(),
    });
  }
  return data;
}

/**
 * 读取一个白名单路径。非白名单路径请继续用各自的数据层(marketGet 等)。
 */
export function registryGet<T>(path: string): Promise<T> {
  const config = QUERY_CONFIG[path];
  if (!config) {
    return requestRaw(path, { method: 'GET' }).then(
      (res) => res.json() as Promise<T>,
    );
  }
  const entry = entryFor(path);
  const now = Date.now();
  if (entry.value !== undefined && now - entry.fetchedAt < config.ttlMs) {
    return Promise.resolve(entry.value as T);
  }
  if (entry.inFlight) return entry.inFlight as Promise<T>;
  const request = fetchInto(path, entry, config).finally(() => {
    if (entry.inFlight === request) entry.inFlight = null;
  });
  entry.inFlight = request;
  return request as Promise<T>;
}

/**
 * 恢复上一份持久化的真实响应(不发网络)。返回 null 表示没有可用记录。
 * 恢复值同时写进内存注册表(fetchedAt 保持 0 → 下一次 registryGet 仍会
 * 发条件请求确认,304 则零正文)。
 */
export async function restorePersistedQuery<T>(path: string): Promise<T | null> {
  const config = QUERY_CONFIG[path];
  if (!config?.persist) return null;
  const entry = entryFor(path);
  if (entry.value !== undefined) return entry.value as T;
  if (entry.restored) return null;
  entry.restored = true;
  const record = await readPersisted(path);
  if (!record || record.principal !== principalKey) return null;
  if (
    record.appCommit &&
    knownAppCommit &&
    record.appCommit !== knownAppCommit
  ) {
    return null;
  }
  if (entry.value !== undefined) return entry.value as T;
  entry.value = record.raw;
  entry.etag = record.etag;
  // fetchedAt 故意保持 0:恢复值可显示,但绝不算 fresh。
  return record.raw as T;
}

/**
 * 手动刷新:按路径前缀精确失效;失效瞬间旧在途响应全部作废。
 * reload:硬失效——同时删掉 IndexedDB 里的旧记录,并让下一次请求带
 * cache:'reload' 打穿浏览器 HTTP 缓存。写路径(POST 成功后)应当用它,
 * 配合 usePolling 的 refresh({force:true}) 保证页面拿到的是刷新后的版本。
 */
export function invalidateQueryPaths(
  prefixes: string[],
  options?: { reload?: boolean },
): void {
  for (const [path, entry] of entries) {
    if (prefixes.some((prefix) => path === prefix || path.startsWith(prefix))) {
      entry.generation += 1;
      entry.value = undefined;
      entry.etag = null;
      entry.fetchedAt = 0;
      entry.inFlight = null;
      if (options?.reload) {
        entry.forceReload = true;
        void deletePersisted(path);
      }
    }
  }
}

/** 主体切换:内存与持久层一起清,不同主体不共享任何响应。 */
export function dropQueryRegistry(): void {
  for (const entry of entries.values()) {
    entry.generation += 1;
  }
  entries.clear();
  void clearPersisted();
}

/** 测试用复位。 */
export function resetQueryRegistry(): void {
  entries.clear();
  principalKey = 'anonymous';
  knownAppCommit = null;
}
