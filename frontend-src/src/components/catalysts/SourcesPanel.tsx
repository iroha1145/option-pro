/** sources 面板：数据源健康卡（active/degraded LED + 延迟 + 最近抓取 + 今日条数） */
import { motion } from 'framer-motion';
import { usePolling } from '@/hooks/usePolling';
import { catalystsContract } from './api';
import { Led } from './bits';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonCard } from '@/components/shared/Skeleton';
import SourceNote from '@/components/shared/SourceNote';
import { cn } from '@/lib/utils';
import { fmtRelative } from '@/lib/format';

export default function SourcesPanel({ refreshToken }: { refreshToken: number }) {
  const q = usePolling(() => catalystsContract.sources(), 120_000, [refreshToken]);

  if (q.loading && !q.data) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }, (_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    );
  }
  if (q.error || !q.data) {
    return (
      <div className="card-surface">
        <EmptyState
          variant="error"
          icon="doc-quote"
          title="数据源状态快照暂不可用"
          description="接口未覆盖此能力，留空而非编造"
          action={
            <button
              onClick={q.refresh}
              className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105"
            >
              重试
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div>
      <motion.div
        initial="hidden"
        animate="show"
        variants={{ show: { transition: { staggerChildren: 0.05 } } }}
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
      >
        {q.data.map((s) => (
          <motion.div
            key={s.source}
            variants={{
              hidden: { opacity: 0, y: 14 },
              show: { opacity: 1, y: 0, transition: { duration: 0.48, ease: [0.16, 1, 0.3, 1] } },
            }}
            className="card-surface p-5"
          >
            <div className="flex items-center justify-between">
              <p className="text-h3 text-ink-800">{s.source}</p>
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-xs px-1.5 py-0.5 text-micro font-medium',
                  s.status === 'active' ? 'bg-up-50 text-up-700' : 'bg-warn-50 text-warn-600',
                )}
              >
                <Led tone={s.status === 'active' ? 'up' : 'warn'} pulse={s.status === 'active'} className="size-1.5" />
                {s.status === 'active' ? '正常' : '降级'}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              <div>
                <p className="font-mono text-data-l text-ink-900 tnum">{s.latencyMs}</p>
                <p className="mt-0.5 text-micro text-ink-400">延迟 ms</p>
              </div>
              <div>
                <p className="font-mono text-data-l text-ink-900 tnum">{s.itemsToday}</p>
                <p className="mt-0.5 text-micro text-ink-400">今日条数</p>
              </div>
              <div>
                <p className="font-mono text-data-l text-ink-900 tnum" suppressHydrationWarning>
                  {fmtRelative(s.lastFetchedAt).replace(' 分钟前', 'm').replace(' 小时前', 'h').replace(' 天前', 'd')}
                </p>
                <p className="mt-0.5 text-micro text-ink-400">最近抓取</p>
              </div>
            </div>
            <p className="mt-3 border-t border-line pt-2.5 text-micro text-ink-400">{s.note}</p>
          </motion.div>
        ))}
      </motion.div>
      <SourceNote className="mt-4" text="来源：各通讯社公开稿件 · 采集频率每 5 分钟 · 延迟行情" />
    </div>
  );
}
