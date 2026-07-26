/**
 * B5 强度分布（strength/market）
 * 10 桶直方图：grow-bar 错峰 + 均值标线 + ≥85 计数徽章
 */
import { motion } from 'framer-motion';
import type { ApiError } from '@/api/client';
import type { MarketStrength } from '@/api/types';
import { cn } from '@/lib/utils';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonCard } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { t } from '../../i18n/core.ts';

export default function BreadthHistogram({
  data,
  loading,
  error,
  onRetry,
  refreshing,
}: {
  data: MarketStrength | null;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  refreshing: boolean;
}) {
  if (loading) return <SkeletonCard className="h-full" />;
  if (error || !data) {
    return (
      <div className="card-surface h-full">
        <EmptyState
          variant="error"
          icon="doc-quote"
          title={error?.code === 503 ? t('数据暂不可用') : t('加载失败')}
          description={error ? error.message : t('暂无强度分布数据')}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
            >
              {refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
              {t('重试')}
            </button>
          }
        />
      </div>
    );
  }

  const max = Math.max(...data.histogram, 1);
  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <section
      className="card-surface flex h-full flex-col p-5"
      aria-label={t("强度分布")}
    >
      <div className="flex items-start justify-between">
        <p className="eyebrow">{t('强度分布 · 全市场')}</p>
        <span className="inline-flex items-center gap-1 rounded-xs bg-up-50 px-1.5 py-0.5 font-mono text-micro text-up-700 tnum">
          <Icon name="target" size={12} strokeWidth={1.45} />
          ≥85 · {data.ge85Count} {t('只')}
        </span>
      </div>

      <div className="relative mt-6">
        {/* 均值标线 */}
        <div
          className="pointer-events-none absolute -top-4 bottom-6 z-10 border-l border-dashed border-ink-400"
          style={{ left: `${Math.max(1, Math.min(99, data.avgScore))}%` }}
          aria-hidden="true"
        >
          <span className="absolute -left-8 -top-0.5 whitespace-nowrap font-mono text-[9px] text-ink-400 tnum">
            {t('均值')} {data.avgScore.toFixed(1)}
          </span>
        </div>
        <div className="flex h-28 items-end gap-1.5 pt-4">
          {data.histogram.map((n, i) => {
            const score = i * 10 + 5;
            const color =
              score >= 85 ? 'bg-up-600' : score >= 70 ? 'bg-brand-600' : score >= 50 ? 'bg-brand-400' : 'bg-ink-300';
            return (
              <div key={i} className="group relative flex-1">
                <div className="glass pointer-events-none absolute -top-7 left-1/2 z-10 hidden -translate-x-1/2 rounded-xs border border-line px-1.5 py-0.5 font-mono text-[10px] text-ink-600 shadow-sh-2 group-hover:block">
                  {n}
                </div>
                <motion.div
                  className={cn('w-full origin-bottom rounded-t-[3px]', color)}
                  initial={{ scaleY: 0 }}
                  whileInView={{ scaleY: 1 }}
                  viewport={{ once: true, amount: 0.4 }}
                  transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.04 }}
                  style={{ height: `${Math.max(4, (n / max) * 96)}px` }}
                  aria-label={`强度 ${i * 10}–${i === 9 ? 100 : i * 10 + 9}：${n} 只`}
                />
              </div>
            );
          })}
        </div>
        <div className="mt-1.5 flex justify-between font-mono text-[9px] text-ink-300">
          <span>0</span>
          <span>50</span>
          <span>100</span>
        </div>
      </div>

      <p className="mt-auto pt-4 text-micro leading-relaxed text-ink-400">
        {t('桶宽 10 分 · 色阶 &lt;50 弱 / 50–69 / 70–84 / ≥85 强')}
        {/* 「全市场」指扫描股票池整体，与上方选中的指数无关——不标出来读者会以为它跟着指数变。 */}
        <span className="mt-1 block">{t('统计范围为扫描股票池全体，不区分指数。')}</span>
      </p>
    </section>
  );
}
