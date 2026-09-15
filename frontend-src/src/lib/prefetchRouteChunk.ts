/** 主包一执行就按当前路径预取对应路由块，避免身份确认后再开始下载页面 chunk。 */

const prefetched = new Set<string>();
const inflight = new Set<string>();
const MAX_INTENT = 2;

function prefersSaveData(): boolean {
  if (typeof navigator === 'undefined') return false;
  const connection = (navigator as Navigator & {
    connection?: { saveData?: boolean };
  }).connection;
  return connection?.saveData === true;
}

function loadRouteChunk(pathname: string): Promise<unknown> | undefined {
  const normalized = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  switch (normalized) {
    case '/':
      return import('@/pages/Home');
    case '/watchlist':
      return import('@/pages/Watchlist');
    case '/screener':
      return import('@/pages/Screener');
    case '/breakouts':
      return import('@/pages/Breakouts');
    case '/sectors':
      return import('@/pages/Sectors');
    case '/earnings':
      return import('@/pages/Earnings');
    case '/catalysts':
      return import('@/pages/Catalysts');
    case '/market':
      return import('@/pages/Market');
    case '/cta':
      return import('@/pages/CtaTrend');
    case '/login':
      return import('@/pages/Login');
    default:
      if (normalized.startsWith('/stock/')) return import('@/pages/StockDetail');
  }
}

export function prefetchRouteChunk(pathname: string): void {
  void loadRouteChunk(pathname);
}

/** 悬停/键盘聚焦时只预取目标路由代码，不发付费上游请求。 */
export function prefetchRouteOnIntent(pathname: string): void {
  if (!pathname || prefersSaveData()) return;
  const normalized = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  if (typeof window !== 'undefined') {
    const current = window.location.pathname.replace(/\/$/, '') || '/';
    if (current === normalized) return;
  }
  if (prefetched.has(normalized) || inflight.has(normalized)) return;
  if (inflight.size >= MAX_INTENT) return;
  inflight.add(normalized);
  prefetched.add(normalized);
  // 计数要覆盖整个 import，不能在 microtask 里立刻清掉，否则连扫三个
  // 导航项会同时开三个路由块，MAX_INTENT=2 形同虚设。
  Promise.resolve(loadRouteChunk(normalized)).finally(() => {
    inflight.delete(normalized);
  });
}

export function routeIntentHandlers(pathname: string): {
  onPointerEnter: () => void;
  onFocus: () => void;
} {
  return {
    onPointerEnter: () => prefetchRouteOnIntent(pathname),
    onFocus: () => prefetchRouteOnIntent(pathname),
  };
}
