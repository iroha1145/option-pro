/**
 * B4 市场信号解读（signals/market）
 * 今日信号总数 / 较昨日 delta / 六类分布横条 + 趋势偏向大字（由真实 scores 推导并标注依据）
 * + 编辑式解读（真实字段模板化生成，serif 引文卡 + SourceNote「· 非投资建议」）
 */
import { motion } from 'framer-motion';
import type { ApiError } from '@/api/client';
import type { IndexQuote, MarketSignalsSummary, MarketStrength } from '@/api/types';
import type { MarketStatusDetail } from './api';
import { useCountUp } from '@/hooks/useCountUp';
import { cn } from '@/lib/utils';
import { fmtPct, fmtPrice } from '@/lib/format';
import EmptyState from '@/components/shared/EmptyState';
import SourceNote from '@/components/shared/SourceNote';
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
  signals: MarketSignalsSummary,
  indices: IndexQuote[] | null,
  strength: MarketStrength | null,
  regimeMean: number | null,
  status: MarketStatusDetail | null,
  bias: TrendBias | null,
): string {
  const parts: string[] = [];
  const delta = signals.deltaVsYesterday;
  const top = [...signals.byType].sort((a, b) => b.today - a.today)[0];
  parts.push(
    `今日全市场共触发 ${signals.totalToday} 条信号，较昨日${delta >= 0 ? '增加' : '减少'} ${Math.abs(delta)} 条` +
      (top ? `，其中「${top.label}」类 ${top.today} 条居首，7 日均值 ${top.avg7d} 条` : '') +
      '。',
  );
  if (indices?.length) {
    const adv = indices.filter((q) => q.changePct >= 0).length;
    const spx = indices.find((q) => q.code === 'SPX');
    parts.push(
      `六大指数 ${adv} 涨 ${indices.length - adv} 跌` +
        (spx ? `，标普500 报 ${fmtPrice(spx.price)}（${fmtPct(spx.changePct)}）` : '') +
        '。',
    );
  }
  const bits: string[] = [];
  if (strength) bits.push(`全市场强度均值 ${strength.avgScore.toFixed(1)}，高强度（≥85）标的 ${strength.ge85Count} 只`);
  if (regimeMean !== null) bits.push(`六维形态均值 ${regimeMean.toFixed(1)}`);
  if (bits.length) parts.push(`${bits.join('，')}${bias ? `，综合判断趋势偏向「${bias.label}」` : ''}。`);
  if (status) {
    if (status.market === 'open') parts.push('盘中关注量能能否延续，追高注意回撤风险。');
    else if (status.market === 'premarket') parts.push('盘前流动性较薄，信号以开盘后确认为准。');
    else if (status.market === 'postmarket') parts.push('盘后留意财报与公告对明日开盘的传导。');
    else parts.push('当前为最近交易日快照，待开盘后重新校准。');
  }
  return parts.join('');
}

function TypeBars({ data }: { data: MarketSignalsSummary }) {
  const max = Math.max(...data.byType.map((t) => Math.max(t.today, t.avg7d)), 1);
  return (
    <div className="space-y-2.5">
      {data.byType.map((t, i) => (
        <div key={t.type} className="grid grid-cols-[52px_1fr_64px] items-center gap-2">
          <span className="text-caption text-ink-500">{t.label}</span>
          <div className="space-y-1">
            <motion.div
              className="h-2 origin-left rounded-[2px] bg-brand-600"
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: 1 }}
              viewport={{ once: true, amount: 0.4 }}
              transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.045 }}
              style={{ width: `${(t.today / max) * 100}%` }}
              title={`今日 ${t.today} 条`}
            />
            <motion.div
              className="h-2 origin-left rounded-[2px] border border-brand-400/50"
              initial={{ scaleX: 0 }}
              whileInView={{ scaleX: 1 }}
              viewport={{ once: true, amount: 0.4 }}
              transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1], delay: i * 0.045 + 0.06 }}
              style={{
                width: `${(t.avg7d / max) * 100}%`,
                backgroundImage: 'repeating-linear-gradient(45deg, rgba(46,70,224,.5) 0 1.2px, transparent 1.2px 4px)',
              }}
              title={`7 日均值 ${t.avg7d} 条`}
            />
          </div>
          <span className="text-right font-mono text-micro text-ink-500 tnum">
            {t.today}
            <span className="text-ink-300"> / {t.avg7d}</span>
          </span>
        </div>
      ))}
      <p className="pt-1 text-micro text-ink-400">▮ 今日 · ▨ 7 日均值</p>
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
  strength,
  regimeMean,
  status,
  bias,
}: {
  signals: MarketSignalsSummary | null;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
  refreshing: boolean;
  indices: IndexQuote[] | null;
  strength: MarketStrength | null;
  regimeMean: number | null;
  status: MarketStatusDetail | null;
  bias: TrendBias | null;
}) {
  const total = useCountUp(signals?.totalToday ?? 0, 900);

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

  const delta = signals.deltaVsYesterday;
  const reading = buildReading(signals, indices, strength, regimeMean, status, bias);

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
        {/* 左：总量 + 六类分布 */}
        <div>
          <div className="flex items-end gap-4">
            <p>
              <span className="font-mono text-data-xl text-ink-900 tnum">{Math.round(total)}</span>
              <span className="ml-1.5 text-caption text-ink-500">今日信号总数</span>
            </p>
            <p
              className={cn('pb-1 font-mono text-data-m tnum', delta >= 0 ? 'text-up-700' : 'text-down-700')}
              aria-label={`较昨日${delta >= 0 ? '增加' : '减少'} ${Math.abs(delta)} 条`}
            >
              {delta >= 0 ? '+' : '−'}
              {Math.abs(delta)} 较昨日
            </p>
          </div>
          <div className="mt-5">
            <TypeBars data={signals} />
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

      <SourceNote className="mt-5" text="来源：Optix Research · 由真实字段模板化生成 · 非投资建议" />
    </motion.section>
  );
}
