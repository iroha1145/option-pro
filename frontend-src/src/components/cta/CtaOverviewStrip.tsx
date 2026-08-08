/**
 * 跨标的总览卡带（/cta B1）：一行看全 4 个指数代理的估算仓位。
 * 每张卡：标的名/代理 ETF、position_label 胶囊、目标仓位大数+相对前值箭头、
 * 今日边际流（趋势/波动率拆分小字）、120 日仓位 sparkline、最近触发位距离。
 * 点卡=选中该标的（父级负责平滑滚动到主区）；选中态 brand 边/浅底。
 */
import { motion } from 'framer-motion';
import Sparkline from '@/components/charts/Sparkline';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import type { CtaInstrumentEstimate } from '@/api/types';
import { t } from '../../i18n/core.ts';
import { POSITION_META, ZONE_LABELS, signed } from './ctaMeta';

function nearestZone(row: CtaInstrumentEstimate) {
  const zones = [...(row.trigger_levels?.above ?? []), ...(row.trigger_levels?.below ?? [])];
  return zones.length
    ? zones.reduce((best, z) => (Math.abs(z.distance_pct) < Math.abs(best.distance_pct) ? z : best))
    : null;
}

function OverviewCard({
  row,
  selected,
  onSelect,
}: {
  row: CtaInstrumentEstimate;
  selected: boolean;
  onSelect: () => void;
}) {
  const active = row.source_status === 'active' && row.position_score !== null;
  const meta = row.position_label ? POSITION_META[row.position_label] : null;
  const delta =
    active && row.previous_position_score !== null ? row.position_score! - row.previous_position_score : null;
  const zone = active ? nearestZone(row) : null;

  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'card-surface card-lift w-full p-4 text-left',
        selected && 'border-brand-600/50 bg-brand-50 ring-1 ring-brand-500/30',
      )}
    >
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-body-s font-medium text-ink-900">{t(row.label)}</span>
          <span className="font-mono text-micro text-ink-400 tnum">{row.proxy_symbol} · ETF</span>
        </span>
        {active && meta ? (
          <span className={cn('shrink-0 rounded-pill px-2 py-0.5 text-micro font-medium', meta.cls)}>{meta.label}</span>
        ) : (
          <span className="shrink-0 rounded-pill bg-paper-2 px-2 py-0.5 text-micro text-ink-500">{t('暂无数据')}</span>
        )}
      </span>

      {active ? (
        <>
          <span className="mt-2 flex items-baseline gap-2">
            <span className="font-mono text-data-l text-ink-900 tnum">{signed(row.position_score)}</span>
            {delta !== null && (
              <span
                className={cn(
                  'inline-flex items-center gap-0.5 font-mono text-micro tnum',
                  delta > 0 ? 'text-up-700' : delta < 0 ? 'text-down-700' : 'text-ink-400',
                )}
              >
                <Icon name={delta > 0 ? 'arrow-up' : delta < 0 ? 'arrow-down' : 'minus'} size={11} />
                {t('前值 {v}', { v: signed(row.previous_position_score) })}
              </span>
            )}
          </span>
          <span className="mt-1 block">
            <span className="text-micro text-ink-400">{t('今日边际流')} </span>
            <span className={cn('font-mono text-caption tnum', (row.flow_score ?? 0) >= 0 ? 'text-up-700' : 'text-down-700')}>
              {signed(row.flow_score)}
            </span>
            <span className="block font-mono text-micro text-ink-400 tnum">
              {t('趋势 {a} · 波动率 {b}', { a: signed(row.trend_flow), b: signed(row.volatility_flow) })}
            </span>
          </span>
          {row.history.length >= 2 && (
            <Sparkline
              data={row.history.map((h) => h.position)}
              width={120}
              height={36}
              change={row.position_score ?? 0}
              className="mt-2 h-9 w-full"
            />
          )}
          {zone && (
            <span className="mt-2 block truncate text-micro text-ink-400">
              {t('最近触发位')}{' '}
              <span className={zone.est_position_change >= 0 ? 'text-up-700' : 'text-down-700'}>
                {ZONE_LABELS[zone.label_key] ?? zone.label_key}
              </span>{' '}
              <span className="font-mono tnum">
                {zone.distance_pct > 0 ? '+' : ''}{zone.distance_pct.toFixed(1)}%
              </span>
            </span>
          )}
        </>
      ) : (
        /* 诚实空态：历史不足/代理不可用时明示，不以中性值代替 */
        <span className="mt-3 block rounded-md border border-line bg-card-warm px-2.5 py-2 text-micro text-ink-500">
          {t('数据不足，未生成估算')}
        </span>
      )}
    </button>
  );
}

export default function CtaOverviewStrip({
  rows,
  selected,
  onSelect,
}: {
  rows: CtaInstrumentEstimate[];
  selected: string;
  onSelect: (instrument: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {rows.map((row, i) => (
        <motion.div
          key={row.instrument}
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: Math.min(i * 0.07, 0.3), ease: [0.16, 1, 0.3, 1] }}
        >
          <OverviewCard row={row} selected={row.instrument === selected} onSelect={() => onSelect(row.instrument)} />
        </motion.div>
      ))}
    </div>
  );
}
