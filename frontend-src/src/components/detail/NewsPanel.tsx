/**
 * 相关新闻（stock-detail.md T4）：catalysts/tickers/{t} · 72h · 最多 5 条
 * 顶部 AI 财报影响摘要条（±X.X% · 非收益标注）· 「更多」→ /catalysts?ticker=X
 */
import { Link } from 'react-router';
import { catalystsApi } from '@/api/modules/catalysts';
import { earningsApi } from '@/api/modules/earnings';
import { usePolling } from '@/hooks/usePolling';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonText } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';
import type { NewsItem, NewsSentiment } from '@/api/types';

const SENTIMENT_META: Record<NewsSentiment, { text: string; cls: string }> = {
  positive: { text: '利多', cls: 'bg-up-50 text-up-700' },
  neutral: { text: '中性', cls: 'bg-card-warm text-ink-500 border border-line-strong' },
  negative: { text: '利空', cls: 'bg-down-50 text-down-700' },
};

const IMPACT_SENTIMENT = {
  bullish: { text: '偏多', cls: 'bg-up-50 text-up-700' },
  neutral: { text: '中性', cls: 'bg-card-warm text-ink-500 border border-line-strong' },
  bearish: { text: '偏空', cls: 'bg-down-50 text-down-700' },
} as const;

const WINDOW_MS = 72 * 3600_000;
const MAX_ITEMS = 5;

export default function NewsPanel({ ticker }: { ticker: string }) {
  const { data: news, loading } = usePolling(() => catalystsApi.byTicker(ticker), null, [ticker]);
  const { data: impact } = usePolling(() => earningsApi.impact(ticker), null, [ticker]);

  if (loading) return <SkeletonText lines={6} className="py-2" />;

  const items: NewsItem[] = (news ?? [])
    .filter((n) => Date.now() - new Date(n.publishedAt).getTime() < WINDOW_MS)
    .slice(0, MAX_ITEMS);

  return (
    <div>
      {impact && (
        <div className="mb-4 rounded-md border border-ai-600/25 bg-ai-50 px-3.5 py-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span className="flex items-center gap-1.5 text-body-s font-medium text-ink-800">
              <Icon name="spark-ai" size={15} className="text-ai-600" />
              AI 财报影响
            </span>
            <span className="font-mono text-body-s font-medium text-ink-900 tnum">±{impact.expectedMovePct.toFixed(1)}%</span>
            <span className={cn('rounded-xs px-1.5 py-px text-micro font-medium', IMPACT_SENTIMENT[impact.sentiment].cls)}>
              {IMPACT_SENTIMENT[impact.sentiment].text}
            </span>
            <Link to="/earnings" className="ml-auto flex items-center gap-1 text-caption font-medium text-ai-600 hover:text-ai-600/80">
              查看财报页
              <Icon name="chevron-right" size={13} />
            </Link>
          </div>
          <p className="mt-1.5 text-micro text-ink-400">预期波动为方向性估计 · 非收益预测 · IV 百分位 {impact.ivRank}%</p>
        </div>
      )}

      {items.length === 0 ? (
        <EmptyState
          image="/empty-news.svg"
          title="72 小时内无相关新闻"
          description="新的催化剂出现后将在电报纸上呈现"
          className="py-8"
        />
      ) : (
        <ul className="divide-y divide-line">
          {items.map((n) => {
            const meta = SENTIMENT_META[n.sentiment];
            return (
              <li key={n.id} className="group py-3 first:pt-0">
                <div className="flex items-start justify-between gap-3">
                  <p className="min-w-0 flex-1 text-body-s leading-[20px] text-ink-800 transition-colors group-hover:text-brand-700">
                    {n.title}
                  </p>
                  <span className={cn('shrink-0 rounded-xs px-1.5 py-px text-micro font-medium', meta.cls)}>{meta.text}</span>
                </div>
                <p className="mt-1 flex items-center gap-2 text-micro text-ink-400">
                  <span>{n.source}</span>
                  <span aria-hidden="true">·</span>
                  <span className="font-mono tnum">{fmtRelative(n.publishedAt)}</span>
                </p>
              </li>
            );
          })}
        </ul>
      )}

      <div className="mt-4 border-t border-line pt-3">
        <Link
          to={`/catalysts?ticker=${encodeURIComponent(ticker)}`}
          className="inline-flex items-center gap-1.5 text-caption font-medium text-brand-600 transition-colors hover:text-brand-700"
        >
          更多相关新闻
          <Icon name="arrow-up-right" size={13} />
        </Link>
      </div>
    </div>
  );
}
