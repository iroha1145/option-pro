import SoftBadge from '@/components/shared/SoftBadge';
/** sources 面板：数据源健康卡（采集状态 + 数据新鲜度 + 最近抓取 + 近 24h 条数） */
import { motion } from 'framer-motion';
import { usePolling } from '@/hooks/usePolling';
import { catalystsContract } from './api';
import { Led } from './bits';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonCard } from '@/components/shared/Skeleton';
import SourceNote from '@/components/shared/SourceNote';
import { fmtRelativeShort } from '@/lib/format';
import { t } from '../../i18n/core.ts';

function formatDataLag(milliseconds: number | null): string {
  if (milliseconds === null) return '—';
  if (milliseconds < 1_000) return t('<1 秒');
  if (milliseconds < 60_000) return t('{n} 秒', { n: Math.round(milliseconds / 1_000) });
  if (milliseconds < 3_600_000) return t('{n} 分', { n: Math.round(milliseconds / 60_000) });
  return t('{n} 小时', { n: (milliseconds / 3_600_000).toFixed(milliseconds < 36_000_000 ? 1 : 0) });
}

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
          title={t("数据源状态暂不可用")}
          description={t("稍后刷新再试")}
          action={
            <button
              onClick={() => q.refresh()}
              className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] hover:brightness-105"
            >
              {t('重试')}
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
              <SoftBadge tone={s.status === 'active' ? 'up' : 'warn'}>
                {/* 源正常/异常是静态健康状态，不脉冲 */}
                <Led tone={s.status === 'active' ? 'up' : 'warn'} className="size-1.5" />
                {s.status === 'active' ? t('正常') : t('异常')}
              </SoftBadge>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center">
              <div>
                <p className="font-mono text-data-l text-ink-900 tnum">{formatDataLag(s.latencyMs)}</p>
                <p className="mt-0.5 text-micro text-ink-400">{t('数据滞后')}</p>
              </div>
              <div>
                <p className="font-mono text-data-l text-ink-900 tnum">{s.itemsToday ?? '—'}</p>
                <p className="mt-0.5 text-micro text-ink-400">{t('近 24h 条数')}</p>
              </div>
              <div>
                <p className="font-mono text-data-l text-ink-900 tnum" suppressHydrationWarning>
                  {fmtRelativeShort(s.lastFetchedAt)}
                </p>
                <p className="mt-0.5 text-micro text-ink-400">{t('最近抓取')}</p>
              </div>
            </div>
            <p className="mt-3 border-t border-line pt-2.5 text-micro text-ink-400">{s.note}</p>
          </motion.div>
        ))}
      </motion.div>
      <SourceNote
        className="mt-4"
        text={t("滞后表示数据更新到了什么时候，不是页面加载速度；条数按最近 24 小时收录的新闻与经济事件统计")}
      />
    </div>
  );
}
