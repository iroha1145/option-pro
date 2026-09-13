import { offerBootPrefetch, toQuery } from '@/api/client';

/** 与 FeedPanel 首页、theme-boot 默认 feed 同一组默认开关。 */
export const DEFAULT_FEED_PAGE_SIZE = 12;

export function defaultFeedApiPath(windowHours: number): string {
  return `/api/catalysts/feed?${toQuery({
    window_hours: windowHours,
    include_unanalyzed: true,
    include_neutral: true,
    limit: DEFAULT_FEED_PAGE_SIZE,
  })}`;
}

/** 用户展开筛选后预取其它时间窗，点击时只等待剩余网络而不是整轮 RTT。 */
export function prefetchDefaultFeed(windowHours: number): void {
  offerBootPrefetch(defaultFeedApiPath(windowHours));
}
