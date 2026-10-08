/**
 * B4 市场信号解读（signals/market）
 * 直接展示 signals 指标对象与 scores 聚合；接口未提供的时序统计不渲染。
 */
import type { ApiError } from '@/api/client';
import type { IndexQuote, MarketSignalsSnapshot } from '@/api/types';
import type { MarketStatusDetail } from './api';
import { cn } from '@/lib/utils';
import { fmtPct, fmtPrice } from '@/lib/format';
import { isUsIndexSymbol } from '@/lib/quoteSymbol';
import { sectorBreadthReading } from '@/lib/sectorBreadth';
import EmptyState from '@/components/shared/EmptyState';
import InfoHint from '@/components/shared/InfoHint';
import { MARKET_SIGNAL_HINTS, SCORE_HINTS } from '@/lib/scoreHints';
import { SkeletonCard } from '@/components/shared/Skeleton';
import { BusyIcon } from '@/components/shared/IconSwap';
import Icon from '@/components/icons';
import { Link } from 'react-router';
import { routeIntentHandlers } from '@/lib/prefetchRouteChunk';
import { t } from '../../i18n/core.ts';

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
      signals.topScore !== null ? t('顶部风险 {score}', { score: signals.topScore }) : null,
      signals.bottomScore !== null ? t('底部修复 {score}', { score: signals.bottomScore }) : null,
    ].filter(Boolean);
    parts.push(t('当前信号评分：{scores}。', { scores: scores.join(t('，')) }));
  }
  const leading = [...signals.metrics]
    .filter((metric) => metric.topScore !== null || metric.bottomScore !== null)
    .sort((a, b) => Math.max(b.topScore ?? 0, b.bottomScore ?? 0) - Math.max(a.topScore ?? 0, a.bottomScore ?? 0))
    .slice(0, 2);
  if (leading.length) {
    const items = leading
      .map((metric) => metric.key === 'sectors_above_50dma'
        ? sectorBreadthReading(metric.value, signals.breadthCoverage)
        : t('「{label}」{value}', { label: metric.label, value: metric.value }))
      .join(t('、'));
    parts.push(t('主要指标：{items}。', { items }));
  }
  /* 只数美股指数：本页其余读数都是美股的，日经、上证的涨跌混进来会让这句话和上下文对不上。 */
  const usIndices = indices?.filter((q) => isUsIndexSymbol(q.symbol || q.code)) ?? [];
  if (usIndices.length) {
    /* 平盘不算上涨（审计 P2-4 同一口径）；这段文字会进 AI 上下文，口径必须准。 */
    const adv = usIndices.filter((q) => q.changePct !== null && q.changePct > 0).length;
    const dec = usIndices.filter((q) => q.changePct !== null && q.changePct < 0).length;
    const flat = usIndices.filter((q) => q.changePct === 0).length;
    const unknown = usIndices.length - adv - dec - flat;
    const spx = usIndices.find((q) => q.code === 'SPX');
    /* 数量随真实指数列表走（审计 2.1.3）：写死「三大」「六大」会和实际取到的个数对不上。 */
    parts.push(
      t('美股 {n} 个主要指数 {adv} 涨 {dec} 跌', { n: usIndices.length, adv, dec }) +
        (flat > 0 ? t(' {flat} 平', { flat }) : '') +
        (unknown > 0 ? t('，{unknown} 个涨跌未知', { unknown }) : '') +
        (spx ? t('，标普 500 报 {price}（{pct}）', { price: fmtPrice(spx.price), pct: fmtPct(spx.changePct) }) : '') +
        t('。'),
    );
  }
  if (regimeMean !== null) {
    parts.push(
      t('走势评分六项均分 {mean}', { mean: regimeMean.toFixed(1) }) +
        (bias ? t('，整体「{label}」', { label: t(bias.label) }) : '') +
        t('。'),
    );
  }
  if (status) {
    if (status.market === 'premarket') parts.push(t('盘前流动性较薄，信号以开盘后确认为准。'));
    else if (status.market === 'closed') parts.push(t('当前为最近一个交易日的数据，开盘后会重新计算。'));
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
          <span className="text-right text-caption text-ink-800 tnum">
            {metric.key === 'sectors_above_50dma' ? `${metric.value.toFixed(2)}%` : metric.value}
          </span>
          <span className="text-right text-micro text-ink-400 tnum">
            {metric.topScore !== null || metric.bottomScore !== null
              ? `${metric.topScore ?? '—'} / ${metric.bottomScore ?? '—'}`
              : t('未评分')}
          </span>
        </div>
      ))}
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
          title={error?.code === 503 ? t('数据暂不可用') : t('加载失败')}
          description={error ? error.message : t('暂无信号汇总数据')}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              aria-busy={refreshing}
              className="btn-primary"
            >
              <BusyIcon busy={refreshing} size={14} tone="on-accent" />
              {t('重试')}
            </button>
          }
        />
      </div>
    );
  }

  const reading = buildReading(signals, indices, regimeMean, status, bias);

  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <section
      className="card-surface flex h-full flex-col p-6"
      aria-label={t("信号解读")}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <h3 className="text-h3 text-ink-900">{t('信号解读')}</h3>
        {/* 原页底联动卡里的突破雷达入口：属于选股组，跨组跳转留在读信号的地方 */}
        <Link
          to="/breakouts"
          {...routeIntentHandlers('/breakouts')}
          className="touch-target group inline-flex min-h-8 items-center gap-1 text-caption font-medium text-brand-600 hover:text-brand-700"
        >
          {t('查看突破雷达')}
          <Icon
            name="arrow-up-right"
            size={14}
            className="transition-transform duration-fast group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
          />
        </Link>
      </div>

      <div className="mt-5 grid flex-1 grid-cols-1 gap-6 lg:grid-cols-2">
        {/* 左：真实评分 + 指标对象 */}
        <div>
          {/* 标签 +「ⓘ」要 59px；320 宽每格内容只有 45px，ⓘ 掉到下一行。标签不折行，<360 把格内左右边距收到 6px。 */}
          <div className="grid grid-cols-3 gap-3 max-[359px]:[&>p]:px-1.5 [&>p>span:first-child]:whitespace-nowrap">
            <p className="rounded-md border border-line bg-card-warm p-3">
              <span className="block text-micro text-ink-400">
                {t('顶部风险')}
                <InfoHint hint={SCORE_HINTS.readingTop} side="bottom" align="start" size={11} className="ml-1" />
              </span>
              <span className="mt-1 block metric-value text-data-m text-down-700">{signals.topScore ?? '—'}</span>
            </p>
            <p className="rounded-md border border-line bg-card-warm p-3">
              <span className="block text-micro text-ink-400">
                {t('底部修复')}
                <InfoHint hint={SCORE_HINTS.readingBottom} side="bottom" size={11} className="ml-1" />
              </span>
              <span className="mt-1 block metric-value text-data-m text-up-700">{signals.bottomScore ?? '—'}</span>
            </p>
            <p className="rounded-md border border-line bg-card-warm p-3">
              <span className="block text-micro text-ink-400">
                {t('数据质量')}
                <InfoHint hint={SCORE_HINTS.readingDataQuality} side="bottom" align="end" size={11} className="ml-1" />
              </span>
              <span className="mt-1 block metric-value text-data-m text-ink-800">{signals.dataQuality ?? '—'}</span>
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
              <span className="text-caption text-ink-500">{t('趋势偏向')}</span>
              <span className={cn('ml-3 font-display text-display-m font-medium', bias ? biasColor(bias.label) : 'text-ink-400')}>
                {bias?.label ? t(bias.label) : '—'}
              </span>
            </p>
          </div>
          <p className="mt-1 text-micro text-ink-400">{bias?.basis ?? t('数据不足，暂无法判断趋势')}</p>
          <blockquote className="mt-4 flex-1 rounded-lg border border-line bg-card-warm p-4">
            <p className="text-[15px] leading-[26px] text-ink-800">{reading}</p>
          </blockquote>
        </div>
      </div>
    </section>
  );
}
