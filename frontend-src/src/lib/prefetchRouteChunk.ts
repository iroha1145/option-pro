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

function loadRouteChunk(pathname: string): void {
  const normalized = pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
  switch (normalized) {
    case '/':
      void import('@/pages/Home');
      return;
    case '/watchlist':
      void import('@/pages/Watchlist');
      return;
    case '/screener':
      void import('@/pages/Screener');
      return;
    case '/breakouts':
      void import('@/pages/Breakouts');
      return;
    case '/sectors':
      void import('@/pages/Sectors');
      return;
    case '/earnings':
      void import('@/pages/Earnings');
      return;
    case '/catalysts':
      void import('@/pages/Catalysts');
      return;
    case '/market':
      void import('@/pages/Market');
      return;
    case '/cta':
      void import('@/pages/CtaTrend');
      return;
    case '/login':
      void import('@/pages/Login');
      return;
    default:
      if (normalized.startsWith('/stock/')) void import('@/pages/StockDetail');
  }
}

export function prefetchRouteChunk(pathname: string): void {
  loadRouteChunk(pathname);
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
  try {
    loadRouteChunk(normalized);
  } finally {
    queueMicrotask(() => inflight.delete(normalized));
  }
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
