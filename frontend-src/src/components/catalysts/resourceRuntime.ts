import { API_MODE } from '@/api/client';
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
let revision: string | null = null;
let lastHintRefresh = 0;
const visible = () => typeof document !== 'undefined' && document.visibilityState === 'visible';

// Manual refresh / completed writes invalidate values AND in-flight generations.
// Retain the displayed snapshot; revalidation failure must not erase useful data.
onCatalystReadsInvalidated(() => {
  catalystResources.invalidate();
  if (visible()) queueMicrotask(() => catalystResources.tick());
});

function closeStream(): void { source?.close(); source = null; }
function connect(): void {
  if (source || !users || !visible() || API_MODE !== 'live' || typeof EventSource === 'undefined') return;
  try { source = new EventSource('/api/catalysts/updates'); }
  catch { source = null; return; }
  source.addEventListener('catalyst-update', (event) => {
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
      lastHintRefresh = Date.now();
      if (users && visible()) clearCatalystReadCache();
    }, Math.max(0, 30_000 - (Date.now() - lastHintRefresh)));
  });
  // EventSource owns reconnect/backoff. The timer below remains independent if
  // a gateway buffers SSE, the old backend returns 404, or the stream cap is hit.
}
function onVisibility(): void {
  if (!visible()) { closeStream(); return; }
  catalystResources.tick();
  connect();
}
function stop(): void {
  if (users) return;
  closeStream();
  if (timer) clearInterval(timer);
  if (flushTimer) clearTimeout(flushTimer);
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
