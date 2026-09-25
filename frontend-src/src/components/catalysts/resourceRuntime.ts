import { API_MODE } from '@/api/client';
import { getQueryPrincipalGeneration } from '@/api/queryRegistry';
import { clearCatalystReadCache } from './api';
import { ResourceCache } from './resourceCache';
import { catalystPersistence } from './resourcePersistence';
import { onCatalystReadsInvalidated } from './resourceSignals';

export const catalystResources = new ResourceCache(catalystPersistence);
let users = 0;
let source: EventSource | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
let closeTimer: ReturnType<typeof setTimeout> | null = null;
let flushTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectAttempts = 0;
/* 合并窗口到期时页面不可见或没人在用：提示留到回到前台 / 下次进入再生效，不能丢。 */
let hintPending = false;
let revision: string | null = null;
let lastHintRefresh = 0;
/* 服务端拒绝流（部署期 502、旧后端 404、连接数上限）后浏览器不会自己重连，按这个节奏重试。 */
const RECONNECT_WAITS_MS = [5_000, 15_000, 30_000, 60_000] as const;
const visible = () => typeof document !== 'undefined' && document.visibilityState === 'visible';

// Manual refresh / completed writes invalidate values AND in-flight generations.
// Retain the displayed snapshot; revalidation failure must not erase useful data.
onCatalystReadsInvalidated((options) => {
  catalystResources.invalidate(options);
  // The old page's subscriptions are still mounted until React commits the
  // new identity. Do not start another read under their old cache keys.
  if (!options?.principalChanged && visible()) {
    const principalGeneration = getQueryPrincipalGeneration();
    queueMicrotask(() => {
      if (principalGeneration === getQueryPrincipalGeneration()) catalystResources.tick();
    });
  }
});

function flushHint(): void {
  hintPending = false;
  lastHintRefresh = Date.now();
  clearCatalystReadCache();
}

function closeStream(): void { source?.close(); source = null; }

function clearReconnect(): void {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
}

function scheduleReconnect(): void {
  if (reconnectTimer || !users || !visible()) return;
  const delay = RECONNECT_WAITS_MS[Math.min(reconnectAttempts, RECONNECT_WAITS_MS.length - 1)];
  reconnectAttempts += 1;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delay);
}

function connect(): void {
  if (source || reconnectTimer || !users || !visible() || API_MODE !== 'live' || typeof EventSource === 'undefined') return;
  let stream: EventSource;
  try { stream = new EventSource('/api/catalysts/updates'); }
  catch { scheduleReconnect(); return; }
  source = stream;
  stream.addEventListener('open', () => { reconnectAttempts = 0; });
  stream.addEventListener('error', () => {
    // 网络闪断时浏览器处于 CONNECTING 并自行重连；只有它放弃（CLOSED）时才由这里退避重连。
    if (stream.readyState !== EventSource.CLOSED) return;
    stream.close();
    if (source === stream) source = null;
    scheduleReconnect();
  });
  stream.addEventListener('catalyst-update', (event) => {
    let next: unknown;
    try { next = (JSON.parse((event as MessageEvent<string>).data) as { revision?: unknown }).revision; }
    catch { return; }
    if (typeof next !== 'string' || !/^[a-f0-9]{24}$/.test(next)) return;
    if (next === revision) return;
    // First connection establishes a baseline; active queries are already loading.
    const changed = revision !== null;
    revision = next;
    if (!changed || flushTimer) return;
    // WAL checkpoints/job writes can be noisy: coalesce hints, at most once/30s.
    flushTimer = setTimeout(() => {
      flushTimer = null;
      if (users && visible()) flushHint();
      else hintPending = true;
    }, Math.max(0, 30_000 - (Date.now() - lastHintRefresh)));
  });
  // The 15s timer below remains independent if a gateway buffers SSE or the
  // stream stays down between reconnect attempts.
}
function onVisibility(): void {
  if (!visible()) { closeStream(); clearReconnect(); return; }
  // 回到前台（或网络恢复）：立即重连，不等剩余的退避。
  clearReconnect();
  if (hintPending) flushHint();
  else catalystResources.tick();
  connect();
}
function stop(): void {
  if (users) return;
  closeStream();
  clearReconnect();
  if (timer) clearInterval(timer);
  if (flushTimer) {
    clearTimeout(flushTimer);
    hintPending = true;
  }
  timer = null; flushTimer = null;
  document.removeEventListener('visibilitychange', onVisibility);
  window.removeEventListener('online', onVisibility);
}
export function retainCatalystRuntime(): () => void {
  users += 1;
  if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
  if (!timer) {
    timer = setInterval(() => { if (visible()) catalystResources.tick(); }, 15_000);
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('online', onVisibility);
  }
  if (hintPending && visible()) flushHint();
  connect();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    users -= 1;
    // A tab swap shouldn't tear down/reconnect the same stream.
    if (!users) closeTimer = setTimeout(stop, 750);
  };
}
