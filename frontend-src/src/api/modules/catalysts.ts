/** 催化剂域：status / feed / news / tickers / calendar / hotspots / focus-cycles */
import { get, post, mockOr, toQuery } from '../client';
import { asRec, pickN, pickS, unwrap } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { CatalystsStatus, FocusCycle, Hotspot, NewsItem, NewsSentiment } from '../types';

export interface FeedParams {
  page?: number;
  pageSize?: number;
  sentiment?: 'positive' | 'neutral' | 'negative';
  ticker?: string;
}

/** 契约 NewsItem.analysis.classification → UI sentiment */
function mapSentiment(r: Record<string, unknown>): NewsSentiment {
  const direct = pickS(r, 'sentiment');
  if (direct === 'positive' || direct === 'negative' || direct === 'neutral') return direct;
  const cls = pickS(asRec(r.analysis), 'classification');
  if (cls === 'bullish') return 'positive';
  if (cls === 'bearish') return 'negative';
  return 'neutral';
}

/** 契约 NewsItem（snake_case）→ UI NewsItem */
export function mapNewsItem(r: Record<string, unknown>): NewsItem {
  const tickers = Array.isArray(r.source_tickers) ? (r.source_tickers as string[]) : Array.isArray(r.tickers) ? (r.tickers as string[]) : [];
  return {
    id: pickS(r, 'id', 'news_id') ?? '',
    title: pickS(r, 'title') ?? '',
    summary: pickS(r, 'summary') ?? '',
    source: pickS(r, 'source') ?? '',
    publishedAt: pickS(r, 'publishedAt', 'published_at') ?? '',
    sentiment: mapSentiment(r),
    tickers,
    heat: pickN(r, 'heat') ?? 0,
  };
}

/** UI sentiment → 契约 classification */
const CLASSIFICATION_MAP: Record<NewsSentiment, string> = {
  positive: 'bullish',
  negative: 'bearish',
  neutral: 'neutral',
};

export const catalystsApi = {
  status: (): Promise<CatalystsStatus> =>
    mockOr(
      () => fx2.getCatalystsStatus(),
      () =>
        get('/catalysts/status').then((d) => {
          const r = asRec(d);
          return {
            newsToday: pickN(r, 'newsToday', 'news_today') ?? 0,
            hotspotsActive: pickN(r, 'hotspotsActive', 'hotspots_active') ?? 0,
            lastCrawlAt: pickS(r, 'lastCrawlAt', 'last_crawl_at') ?? '',
          };
        }),
    ),
  feed: (params: FeedParams = {}): Promise<{ items: NewsItem[]; total: number }> =>
    mockOr(
      () => fx2.getCatalystsFeed(params.page ?? 1, params.pageSize ?? 20),
      // 契约为 cursor 分页（ticker/classification/limit/cursor…）；page 概念不下发
      () => {
        const qs = toQuery({
          ticker: params.ticker,
          classification: params.sentiment ? CLASSIFICATION_MAP[params.sentiment] : undefined,
          limit: params.pageSize,
        });
        return get(`/catalysts/feed${qs ? `?${qs}` : ''}`).then((d) => {
          const items = unwrap(d, 'items').map(mapNewsItem);
          return { items, total: items.length };
        });
      },
    ),
  news: (id: string): Promise<NewsItem> =>
    mockOr(() => fx2.getNewsById(id), () => get(`/catalysts/news/${encodeURIComponent(id)}`).then((d) => mapNewsItem(asRec(d)))),
  byTicker: (ticker: string): Promise<NewsItem[]> =>
    mockOr(
      () => fx2.getNewsByTicker(ticker),
      () => get(`/catalysts/tickers/${encodeURIComponent(ticker)}`).then((d) => unwrap(d, 'items').map(mapNewsItem)),
    ),
  batchTickers: (tickers: string[]): Promise<{ accepted: number }> =>
    mockOr(
      () => fx2.submitTickersBatch(tickers),
      // 契约 body {tickers≤50, window_hours, limit, include_neutral} → 每股 {items, net_impact, ...}
      () =>
        post('/catalysts/tickers/batch', { tickers: tickers.slice(0, 50), include_neutral: true }).then((d) => {
          const r = asRec(d);
          const perTicker = asRec(r.results ?? r.tickers ?? d);
          return { accepted: pickN(r, 'accepted') ?? (Object.keys(perTicker).length || tickers.length) };
        }),
    ),
  calendar: (): Promise<{ date: string; items: { kind: string; label: string }[] }[]> =>
    mockOr(
      () => fx2.getCatalystsCalendar(),
      // 契约 {events:[{event_id,country,title,impact,impact_zh,scheduled_at,...}]} → 按日分组
      () =>
        get('/catalysts/calendar').then((d) => {
          const groups = new Map<string, { kind: string; label: string }[]>();
          for (const e of unwrap(d, 'events')) {
            const day = (pickS(e, 'scheduled_at') ?? '').slice(0, 10);
            const label = pickS(e, 'title') ?? '';
            if (!day || !label) continue;
            const kind = pickS(e, 'impact') ?? 'low';
            groups.set(day, [...(groups.get(day) ?? []), { kind, label }]);
          }
          return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, items]) => ({ date, items }));
        }),
    ),
  hotspots: (): Promise<Hotspot[]> =>
    mockOr(
      () => fx2.getHotspots(),
      () =>
        get('/catalysts/hotspots?limit=8').then((d) =>
          unwrap(d, 'items').map((r) => ({
            id: pickS(r, 'id', 'hotspot_id') ?? '',
            theme: pickS(r, 'theme') ?? '',
            heat: pickN(r, 'heat') ?? 0,
            newsCount: pickN(r, 'newsCount', 'news_count') ?? 0,
            representative: pickS(r, 'representative') ?? '',
            tickers: Array.isArray(r.tickers) ? (r.tickers as string[]) : [],
          })),
        ),
    ),
  hotspotsStatus: (): Promise<{ scanning: boolean; updatedAt: string }> =>
    mockOr(
      () => fx2.getHotspotsStatus(),
      () =>
        get('/catalysts/hotspots/status').then((d) => {
          const r = asRec(d);
          return {
            scanning: r.scanning === true,
            updatedAt: pickS(r, 'updatedAt', 'updated_at', 'as_of') ?? '',
          };
        }),
    ),
  latestFocusCycle: (): Promise<FocusCycle> =>
    mockOr(
      () => fx2.getLatestFocusCycle(),
      () =>
        get('/catalysts/market-focus-cycles/latest').then((d) => {
          const r = asRec(d);
          return {
            id: pickS(r, 'id', 'cycle_id') ?? '',
            theme: pickS(r, 'theme') ?? '',
            startedAt: pickS(r, 'startedAt', 'started_at') ?? '',
            days: pickN(r, 'days') ?? 0,
            stage: (pickS(r, 'stage') as FocusCycle['stage']) ?? '发酵',
            summary: pickS(r, 'summary') ?? '',
          };
        }),
    ),
};
