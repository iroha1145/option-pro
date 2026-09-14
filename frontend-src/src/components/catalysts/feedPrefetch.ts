import { offerBootPrefetch } from '@/api/client';
import { feedApiPath } from './api';
import { DEFAULT_FILTERS, toFeedQuery, type CatalystFilters } from './filters';

/** 与 FeedPanel 首页、theme-boot 默认 feed 同一页大小。 */
export const DEFAULT_FEED_PAGE_SIZE = 12;

/** 预取 URL 必须与点击后的真实首页请求逐字一致：带上当前筛选，只换时间窗。 */
export function feedPrefetchUrl(windowHours: number, filters: CatalystFilters = DEFAULT_FILTERS): string {
  return `/api${feedApiPath({ ...toFeedQuery(filters), windowHours, limit: DEFAULT_FEED_PAGE_SIZE })}`;
}

/** 用户展开筛选或悬停时间窗时预取，点击时只等待剩余网络而不是整轮 RTT。 */
export function prefetchDefaultFeed(windowHours: number, filters: CatalystFilters = DEFAULT_FILTERS): void {
  offerBootPrefetch(feedPrefetchUrl(windowHours, filters));
}
