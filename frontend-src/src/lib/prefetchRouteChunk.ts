/** 主包一执行就按当前路径预取对应路由块，避免身份确认后再开始下载页面 chunk。 */
export function prefetchRouteChunk(pathname: string): void {
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
