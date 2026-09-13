/**
 * 路由 chunk 预取：悬停/聚焦导航或空闲时把下一页 JS 拉进缓存。
 * 只触发动态 import，不挂载页面，不发业务请求。
 */
const LOADERS: Record<string, () => Promise<unknown>> = {
  '/': () => import('@/pages/Home'),
  '/watchlist': () => import('@/pages/Watchlist'),
  '/screener': () => import('@/pages/Screener'),
  '/breakouts': () => import('@/pages/Breakouts'),
  '/sectors': () => import('@/pages/Sectors'),
  '/earnings': () => import('@/pages/Earnings'),
  '/catalysts': () => import('@/pages/Catalysts'),
  '/market': () => import('@/pages/Market'),
  '/cta': () => import('@/pages/CtaTrend'),
  '/login': () => import('@/pages/Login'),
};

const warmed = new Set<string>();

export function prefetchRoute(path: string): void {
  const loader = LOADERS[path];
  if (!loader || warmed.has(path)) return;
  warmed.add(path);
  void loader();
}

/** 手机端「更多」与桌面导航空闲时预热最常进的三页（含新闻）。 */
export function prefetchPrimaryRoutes(): void {
  prefetchRoute('/');
  prefetchRoute('/watchlist');
  prefetchRoute('/catalysts');
}

export function prefetchNewsDrawer(): void {
  if (warmed.has('news-drawer')) return;
  warmed.add('news-drawer');
  void import('@/components/catalysts/NewsDrawer');
}

/** 自选/首页点进个股前，只预热详情页 chunk，不发行情请求。 */
export function prefetchStockPage(): void {
  if (warmed.has('stock-detail')) return;
  warmed.add('stock-detail');
  void import('@/pages/StockDetail');
}
