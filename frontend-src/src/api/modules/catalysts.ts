/** 催化剂域：个股摘要与选股批量计数。页面级 feed/日历走 components/catalysts/api。 */
import { get, post, mockOr } from '../client';
import { asRec, pickB, pickN, pickS, unwrap } from '../live';
import * as fx2 from '@/mocks/fixtures2';
import type { NewsItem, NewsSentiment } from '../types';

/**
 * 72h 窗口逐股催化剂计数（选股页行内徽标消费）
 * - count = 返回条目数（契约 limit=5 截断，hasMore=true 时 UI 显「5+」）
 * - pos/neg = 契约 summary.bullish/bearish（窗口级真实计数）；
 *   契约无「中性」计数 → neu 恒 0 不编造，pending（待分析）如实透传
 */
export interface TickerCatalystSummary {
  count: number;
  hasMore: boolean;
  pos: number;
  neg: number;
  neu: number;
  pending: number | null;
  latestAt: string | null;
  latestTitle: string | null;
}

const BATCH_WINDOW_HOURS = 72;
const BATCH_MAX_TICKERS = 20; // 匿名上限（契约 tickers/batch）
const BATCH_ITEM_LIMIT = 5;

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
function mapNewsItem(r: Record<string, unknown>): NewsItem {
  const tickers = Array.isArray(r.source_tickers) ? (r.source_tickers as string[]) : Array.isArray(r.tickers) ? (r.tickers as string[]) : [];
  return {
    id: pickS(r, 'id', 'news_id') ?? '',
    // 公开投影会移除原文 title/summary，只保留已生成或安全回退的简体中文字段。
    title: pickS(r, 'title_zh', 'titleZh', 'title') ?? '',
    summary: pickS(r, 'summary_zh', 'summaryZh', 'summary') ?? '',
    source: pickS(r, 'source') ?? '',
    publishedAt: pickS(r, 'publishedAt', 'published_at') ?? '',
    sentiment: mapSentiment(r),
    tickers,
    heat: pickN(r, 'heat') ?? 0,
  };
}

export const catalystsApi = {
  byTicker: (ticker: string): Promise<NewsItem[]> =>
    mockOr(
      () => fx2.getNewsByTicker(ticker),
      // 个股影响区只展示已经分析且有方向的新闻；待分析/中性项不冒充股票影响。
      () =>
        get(
          `/catalysts/tickers/${encodeURIComponent(ticker)}?window_hours=72&limit=20&include_unanalyzed=false&include_neutral=false`,
        ).then((d) => unwrap(d, 'items').map(mapNewsItem).filter((item) => item.title)),
    ),
  /**
   * 72h 逐股计数（一页 ≤20 只一次 POST；禁止逐行请求）
   * live：POST /catalysts/tickers/batch {tickers, window_hours:72, limit:5, include_neutral, include_unanalyzed}
   *       → {results:{TICKER:{items, has_more, summary:{bullish,bearish,pending}}}}
   * mock：本地 fixtures 逐股聚合（72h 截断，情绪计数），不发 HTTP
   */
  batchSummaries72h: (tickers: string[]): Promise<Record<string, TickerCatalystSummary>> =>
    mockOr(
      () => {
        const cutoff = Date.now() - BATCH_WINDOW_HOURS * 3600_000;
        const out: Record<string, TickerCatalystSummary> = {};
        for (const t of tickers) {
          const items = fx2.getNewsByTicker(t).filter((n) => new Date(n.publishedAt).getTime() >= cutoff);
          const pos = items.filter((n) => n.sentiment === 'positive').length;
          const neg = items.filter((n) => n.sentiment === 'negative').length;
          out[t] = {
            count: items.length,
            hasMore: false,
            pos,
            neg,
            neu: items.length - pos - neg,
            pending: null,
            latestAt: items[0]?.publishedAt ?? null,
            latestTitle: items[0]?.title ?? null,
          };
        }
        return out;
      },
      () =>
        post('/catalysts/tickers/batch', {
          tickers: tickers.slice(0, BATCH_MAX_TICKERS),
          window_hours: BATCH_WINDOW_HOURS,
          limit: BATCH_ITEM_LIMIT,
          include_neutral: true,
          include_unanalyzed: true,
        }).then((d) => {
          const results = asRec(asRec(d).results);
          const out: Record<string, TickerCatalystSummary> = {};
          for (const [t, raw] of Object.entries(results)) {
            if (!raw || typeof raw !== 'object') continue;
            const r = asRec(raw);
            const items = unwrap(r, 'items');
            const sum = asRec(r.summary);
            let latestAt: string | null = null;
            let latestTitle: string | null = null;
            for (const it of items) {
              const at = pickS(it, 'published_at', 'publishedAt');
              if (at !== null && (latestAt === null || at > latestAt)) {
                latestAt = at;
                latestTitle = pickS(it, 'title_zh', 'title', 'summary_zh');
              }
            }
            out[t] = {
              count: items.length,
              hasMore: pickB(r, 'has_more', 'hasMore') ?? false,
              pos: pickN(sum, 'bullish') ?? 0,
              neg: pickN(sum, 'bearish') ?? 0,
              neu: 0, // 契约 summary 无中性计数——留空优于编造
              pending: pickN(sum, 'pending'),
              latestAt,
              latestTitle,
            };
          }
          return out;
        }),
    ),
};
