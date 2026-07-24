/**
 * B4 市场信号解读（signals/market）
 * 直接展示 signals 指标对象与 scores 聚合；接口未提供的时序统计不渲染。
 */
import { motion } from 'framer-motion';
import type { ApiError } from '@/api/client';
import type { IndexQuote, MarketSignalsSnapshot } from '@/api/types';
import type { MarketStatusDetail } from './api';
import { cn } from '@/lib/utils';
import { fmtPct, fmtPrice } from '@/lib/format';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
import InfoHint from '@/components/shared/InfoHint';
import { MARKET_SIGNAL_HINTS, SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonCard } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';

export interface TrendBias {
  label: '偏多' | '中性' | '偏空';
  basis: string;
}

function biasColor(label: TrendBias['label']): string {
  if (label === '偏多') return 'text-up-700';
  if (label === '偏空') return 'text-down-700';
  return 'text-ink-800';
}

/** 编辑式解读：全部由真实字段模板化生成 */
function buildReading(
  signals: MarketSignalsSnapshot,
  indices: IndexQuote[] | null,
  regimeMean: number | null,
  status: MarketStatusDetail | null,
  bias: TrendBias | null,
): string {
  const parts: string[] = [];
  if (signals.topScore !== null || signals.bottomScore !== null) {
    const scores = [
      signals.topScore !== null ? `顶部风险 ${signals.topScore}` : null,
      signals.bottomScore !== null ? `底部修复 ${signals.bottomScore}` : null,
    ].filter(Boolean);
    parts.push(`市场信号模型当前评分为${scores.join('，')}。`);
  }
  const leading = [...signals.metrics]
    .filter((metric) => metric.topScore !== null || metric.bottomScore !== null)
    .sort((a, b) => Math.max(b.topScore ?? 0, b.bottomScore ?? 0) - Math.max(a.topScore ?? 0, a.bottomScore ?? 0))
    .slice(0, 2);
  if (leading.length) {
    parts.push(`主要观测项为${leading.map((metric) => `「${metric.label}」${metric.value}`).join('、')}。`);
  }
  if (indices?.length) {
    const adv = indices.filter((q) => q.changePct >= 0).length;
    const spx = indices.find((q) => q.code === 'SPX');
    parts.push(
      `六大指数 ${adv} 涨 ${indices.length - adv} 跌` +
        (spx ? `，标普500 报 ${fmtPrice(spx.price)}（${fmtPct(spx.changePct)}）` : '') +
        '。',
    );
  }
  if (regimeMean !== null) {
    parts.push(`六维市场形态均值 ${regimeMean.toFixed(1)}${bias ? `，形态偏向「${bias.label}」` : ''}。`);
  }
  if (status) {
    if (status.market === 'open') parts.push('盘中关注量能能否延续，追高注意回撤风险。');
    else if (status.market === 'premarket') parts.push('盘前流动性较薄，信号以开盘后确认为准。');
    else if (status.market === 'postmarket') parts.push('盘后留意财报与公告对明日开盘的传导。');
    else parts.push('当前为最近交易日快照，待开盘后重新校准。');
  }
  return parts.join('');
}

function MetricRows({ data }: { data: MarketSignalsSnapshot }) {
  const rows = data.metrics.slice(0, 8);
  return (
    <div className="space-y-2.5">
      {rows.map((metric) => (
        <div key={metric.key} className="grid grid-cols-[minmax(0,1fr)_64px_64px] items-center gap-2">
          <span className="flex min-w-0 items-center gap-1">
            <span className="truncate text-caption text-ink-500" title={metric.label}>{metric.label}</span>
            {MARKET_SIGNAL_HINTS[metric.key] && (
              <InfoHint hint={MARKET_SIGNAL_HINTS[metric.key]} align="start" size={11} />
            )}
          </span>
          <span className="text-right font-mono text-caption text-ink-800 tnum">{metric.value}</span>
          <span className="text-right font-mono text-micro text-ink-400 tnum">
            {metric.topScore !== null || metric.bottomScore !== null
              ? `${metric.topScore ?? '—'} / ${metric.bottomScore ?? '—'}`
              : '未评分'}
          </span>
        </div>
      ))}
      <p className="pt-1 text-micro text-ink-400">右列：指标值 · 顶部风险分 / 底部修复分</p>
    </div>
  );
}

export default function SignalsReading({
  signals,
  loading,
  error,
  onRetry,
  refreshing,
  indices,
  regimeMean,
  status,
  bias,
}: {
  signals: MarketSignalsSnapshot | null;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  refreshing: boolean;
  indices: IndexQuote[] | null;
  regimeMean: number | null;
  status: MarketStatusDetail | null;
  bias: TrendBias | null;
}) {
  if (loading) return <SkeletonCard className="h-full" />;
  if (error || !signals) {
    return (
      <div className="card-surface h-full">
        <EmptyState
          variant="error"
          icon="doc-quote"
          title={error?.code === 503 ? '快照暂不可用' : '加载失败'}
          description={error ? error.message : '信号汇总未覆盖，留空而非编造'}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
            >
              {refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
              重试
            </button>
          }
        />
      </div>
    );
  }

  const reading = buildReading(signals, indices, regimeMean, status, bias);

  return (
    <motion.section
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.25 }}
      transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1] }}
      className="card-surface flex h-full flex-col p-6"
      aria-label="市场信号解读"
    >
      <div className="flex items-start justify-between">
        <p className="eyebrow">市场信号解读 · SIGNALS READING</p>
        <Icon name="flag" size={18} className="text-ink-400" />
      </div>

      <div className="mt-5 grid flex-1 grid-cols-1 gap-6 lg:grid-cols-2">
        {/* 左：真实评分 + 指标对象 */}
        <div>
          <div className="grid grid-cols-3 gap-3">
            <p className="rounded-md border border-line bg-card-warm p-3">
              <span className="block text-micro text-ink-400">
                顶部风险
                <InfoHint hint={SCORE_HINTS.readingTop} side="bottom" align="start" size={11} className="ml-1" />
              </span>
              <span className="mt-1 block font-mono text-data-m text-down-700 tnum">{signals.topScore ?? '—'}</span>
            </p>
            <p className="rounded-md border border-line bg-card-warm p-3">
              <span className="block text-micro text-ink-400">
                底部修复
                <InfoHint hint={SCORE_HINTS.readingBottom} side="bottom" size={11} className="ml-1" />
              </span>
              <span className="mt-1 block font-mono text-data-m text-up-700 tnum">{signals.bottomScore ?? '—'}</span>
            </p>
            <p className="rounded-md border border-line bg-card-warm p-3">
              <span className="block text-micro text-ink-400">
                数据质量
                <InfoHint hint={SCORE_HINTS.readingDataQuality} side="bottom" align="end" size={11} className="ml-1" />
              </span>
              <span className="mt-1 block font-mono text-data-m text-ink-800 tnum">{signals.dataQuality ?? '—'}</span>
            </p>
          </div>
          <div className="mt-5">
            <MetricRows data={signals} />
          </div>
        </div>

        {/* 右：趋势偏向 + 编辑式解读 */}
        <div className="flex flex-col">
          <div className="flex items-baseline justify-between gap-3">
            <p>
              <span className="text-caption text-ink-500">趋势偏向</span>
              <span className={cn('ml-3 font-display text-display-m font-semibold', bias ? biasColor(bias.label) : 'text-ink-300')}>
                {bias?.label ?? '—'}
              </span>
            </p>
          </div>
          <p className="mt-1 text-micro text-ink-400">{bias?.basis ?? '依据不足，留空而非编造'}</p>
          <blockquote className="mt-4 flex-1 rounded-lg border border-line bg-card-warm p-4">
            <p className="font-display text-[15px] leading-[26px] text-ink-800">{reading}</p>
          </blockquote>
        </div>
      </div>

      <SourceNote className="mt-5" text="计算方式：由当前真实接口字段模板化生成 · 非投资建议" />
    </motion.section>
  );
}
